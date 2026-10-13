"""SQLite-backed bounded 429 retry queue for disposable workshop test runs.

This module never bypasses provider limits, invents PASS, or automatically
retries auth/UI failures. Durable *cloud* resume requires protecting and
persisting this SQLite file outside ephemeral runners.
"""

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import sqlite3


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3  # Includes original attempt.
    base_seconds: int = 60
    max_backoff_seconds: int = 3600


@dataclass(frozen=True)
class RetryTicket:
    run_id: str
    step_id: str
    failed_attempts: int
    retry_at: float | None
    exhausted: bool


def parse_retry_after(value: str | None, *, now: float) -> int | None:
    """Honor Retry-After delta seconds or HTTP dates, without trusting junk."""
    if not value or len(value) > 200:
        return None
    value = value.strip()
    if value.isdigit():
        return min(int(value), 30 * 24 * 3600)
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            return None
        return max(0, min(30 * 24 * 3600,
                          math.ceil(dt.timestamp() - now)))
    except (ValueError, TypeError, OverflowError):
        return None


class RetryQueue:
    """Transactionally release due blocked steps back to NOT_RUN.

    Checkpoint version increments invalidate stale executor completions. A
    separate browser worker must re-authenticate and independently revalidate
    each retry; this queue never changes statuses to PASS.
    """

    def __init__(self, sqlite_path: str, policy: RetryPolicy = RetryPolicy()):
        if (type(policy.max_attempts) is not int or
                not 1 <= policy.max_attempts <= 20 or
                type(policy.base_seconds) is not int or policy.base_seconds < 10 or
                type(policy.max_backoff_seconds) is not int or
                policy.max_backoff_seconds < policy.base_seconds or
                policy.max_backoff_seconds > 30 * 24 * 3600):
            raise ValueError("Invalid bounded retry policy")
        self.path = str(sqlite_path)
        self.policy = policy
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS retry_queue (
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    failed_attempts INTEGER NOT NULL,
                    due_epoch REAL,
                    reason_code TEXT NOT NULL,
                    PRIMARY KEY (run_id, step_id),
                    FOREIGN KEY (run_id, step_id)
                        REFERENCES checkpoints (run_id, step_id)
                );
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout = 10000")
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def record_429(self, run_id: str, step_id: str, *,
                   now: float, retry_after: str | None = None,
                   jitter_seconds: int = 0) -> RetryTicket:
        """Queue an observed 429 after the execution worker sets ENV_BLOCKED.

        retry_after must come from an actual response header, not the executor's
        unsupported estimate. Caller may add jitter within policy limits.
        """
        if not math.isfinite(now) or type(jitter_seconds) is not int or not 0 <= jitter_seconds <= 120:
            raise ValueError("Invalid clock or jitter")
        wait_header = parse_retry_after(retry_after, now=now)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM checkpoints WHERE run_id=? AND step_id=?",
                             (run_id, step_id)).fetchone()
            if row is None or row[0] != "ENV_BLOCKED":
                raise ValueError("Only currently environment-blocked steps can be retried")
            previous = db.execute(
                "SELECT failed_attempts FROM retry_queue WHERE run_id=? AND step_id=?",
                (run_id, step_id)
            ).fetchone()
            failures = (previous[0] if previous else 0) + 1
            exhausted = failures >= self.policy.max_attempts
            due = None
            if not exhausted:
                wait = min(self.policy.max_backoff_seconds,
                           self.policy.base_seconds * 2 ** (failures - 1))
                wait = max(wait + jitter_seconds, wait_header or 0)
                due = now + wait
            db.execute("""
                INSERT INTO retry_queue(run_id,step_id,failed_attempts,due_epoch,reason_code)
                VALUES(?,?,?,?,?)
                ON CONFLICT(run_id,step_id) DO UPDATE SET
                    failed_attempts=excluded.failed_attempts,
                    due_epoch=excluded.due_epoch,
                    reason_code=excluded.reason_code
                """, (run_id, step_id, failures, due, "HTTP_429"))
            return RetryTicket(run_id, step_id, failures, due, exhausted)

    def wake_due(self, *, now: float, limit: int = 50) -> tuple[tuple[str, str], ...]:
        """Atomically wake due steps; prerequisite gate is rechecked by runner."""
        if not math.isfinite(now) or type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Invalid wake arguments")
        awakened = []
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            queued = db.execute("""
                SELECT r.run_id, r.step_id
                FROM retry_queue r
                INNER JOIN checkpoints c ON c.run_id=r.run_id AND c.step_id=r.step_id
                WHERE r.due_epoch IS NOT NULL AND r.due_epoch<=?
                  AND c.status='ENV_BLOCKED'
                ORDER BY r.due_epoch, r.run_id, r.step_id LIMIT ?
            """, (now, limit)).fetchall()
            for run_id, step_id in queued:
                result = db.execute(
                    "UPDATE checkpoints SET status='NOT_RUN',version=version+1 "
                    "WHERE run_id=? AND step_id=? AND status='ENV_BLOCKED'",
                    (run_id, step_id)
                )
                if result.rowcount != 1:
                    continue
                db.execute("UPDATE retry_queue SET due_epoch=NULL "
                           "WHERE run_id=? AND step_id=?", (run_id, step_id))
                awakened.append((run_id, step_id))
        return tuple(awakened)

    def tickets(self, run_id: str) -> tuple[RetryTicket, ...]:
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT step_id,failed_attempts,due_epoch FROM retry_queue "
                "WHERE run_id=? ORDER BY step_id", (run_id,)
            ).fetchall()
        return tuple(RetryTicket(run_id, step_id, count, due,
                                 count >= self.policy.max_attempts)
                     for step_id, count, due in rows)
