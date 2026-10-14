"""SQLite prototype: transactional, evidence-gated step checkpoints.

Not a cloud orchestrator. Keep this database in private storage: trusted
assertion specifications can contain workshop-specific expected values.
"""

from contextlib import closing
from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from typing import Mapping, Sequence

from .dependencies import NOT_RUN, Step, Schedule, plan_steps
from .evidence import (
    AssertionSpec, Observation, StepContext, Verification,
    OPERATORS, VALUE_TYPES, verify_step,
)


class TransitionError(ValueError):
    """A stale, invalid, or unauthorized-by-state transition was rejected."""


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_specs(specs: Sequence[AssertionSpec]) -> None:
    seen: set[str] = set()
    for spec in specs:
        if not _nonempty(spec.assertion_id) or spec.assertion_id in seen:
            raise ValueError("Invalid or duplicate assertion identifier")
        seen.add(spec.assertion_id)
        expected_type = VALUE_TYPES.get(spec.kind)
        if expected_type is None or type(spec.expected) is not expected_type:
            raise ValueError("Invalid assertion expected type")
        if spec.operator not in OPERATORS or (
            spec.operator in {"contains", "not_contains"} and
            (spec.kind != "DOM_TEXT" or not spec.expected)
        ):
            raise ValueError("Invalid assertion operator")


class CheckpointStore:
    """Atomic local checkpoints; no executor-supplied status can grant PASS.

    A start returns an attempt version. The matching version must be supplied
    to finish/block, preventing stale worker completions and double claims.
    Retries and crash recovery are intentionally not implemented yet.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    org_id TEXT NOT NULL,
                    manifest_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT NOT NULL REFERENCES runs(run_id),
                    step_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0,
                    verification_json TEXT,
                    PRIMARY KEY (run_id, step_id)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 10000")
        return db

    def create_run(
        self,
        run_id: str,
        org_id: str,
        steps: Sequence[Step],
        assertions: Mapping[str, Sequence[AssertionSpec]],
    ) -> None:
        """Freeze trusted manifest/assertions before any executor starts."""
        if not _nonempty(run_id) or not _nonempty(org_id):
            raise ValueError("Run and org identifiers must be nonempty")
        steps = tuple(steps)
        plan_steps(steps, {})  # Validate dependencies, cycles and identifiers.
        known = {step.step_id for step in steps}
        if set(assertions) - known:
            raise ValueError("Assertions for unknown step")
        manifest = []
        for step in steps:
            specs = tuple(assertions.get(step.step_id, ()))
            _validate_specs(specs)
            manifest.append({"step": asdict(step),
                             "assertions": [asdict(spec) for spec in specs]})
        serialized = json.dumps(manifest, separators=(",", ":"), allow_nan=False)
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO runs(run_id, org_id, manifest_json) VALUES(?,?,?)",
                       (run_id, org_id, serialized))
            db.executemany(
                "INSERT INTO checkpoints(run_id, step_id, status) VALUES(?,?,?)",
                [(run_id, step.step_id, NOT_RUN) for step in steps],
            )

    @staticmethod
    def _load(db: sqlite3.Connection, run_id: str):
        row = db.execute("SELECT org_id, manifest_json FROM runs WHERE run_id=?",
                         (run_id,)).fetchone()
        if row is None:
            raise KeyError("Unknown run")
        manifest = json.loads(row[1])
        steps = [Step(item["step"]["step_id"],
                      tuple(item["step"]["dependency_ids"]),
                      item["step"]["required"]) for item in manifest]
        specs = {
            item["step"]["step_id"]: [AssertionSpec(**s) for s in item["assertions"]]
            for item in manifest
        }
        statuses = {
            step_id: status for step_id, status in db.execute(
                "SELECT step_id, status FROM checkpoints WHERE run_id=?", (run_id,)
            )
        }
        return row[0], steps, specs, statuses

    def schedule(self, run_id: str) -> Schedule:
        with closing(self._connect()) as db:
            _, steps, _, statuses = self._load(db, run_id)
            return plan_steps(steps, statuses)

    def status(self, run_id: str, step_id: str) -> tuple[str, int]:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT status, version FROM checkpoints WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()
            if row is None:
                raise KeyError("Unknown run or step")
            return row[0], row[1]

    def start_step(self, run_id: str, step_id: str) -> int:
        """Claim a NOT_RUN step atomically only if all dependencies PASS."""
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            _, steps, _, statuses = self._load(db, run_id)
            if step_id not in {s.step_id for s in steps}:
                raise KeyError("Unknown step")
            if step_id not in plan_steps(steps, statuses).runnable:
                raise TransitionError("Step is not eligible to start")
            db.execute("UPDATE checkpoints SET status='RUNNING', version=version+1 "
                       "WHERE run_id=? AND step_id=?", (run_id, step_id))
            version = db.execute(
                "SELECT version FROM checkpoints WHERE run_id=? AND step_id=?",
                (run_id, step_id),
            ).fetchone()[0]
            return version

    @staticmethod
    def _check_running(db: sqlite3.Connection, run_id: str,
                       step_id: str, attempt_version: int) -> None:
        row = db.execute(
            "SELECT status, version FROM checkpoints WHERE run_id=? AND step_id=?",
            (run_id, step_id),
        ).fetchone()
        if row is None:
            raise KeyError("Unknown run or step")
        if (row[0] != "RUNNING" or type(attempt_version) is not int or
                row[1] != attempt_version):
            raise TransitionError("Step is not running under this attempt version")

    def finish_step(
        self,
        run_id: str,
        step_id: str,
        attempt_version: int,
        context: StepContext,
        observations: Sequence[Observation],
    ) -> Verification:
        """Evaluate stored trusted assertions, then atomically record outcome.

        The executor supplies observations, never expected assertions or PASS.
        Only verify_step can produce a PASS transition. Raw observations are
        not persisted; only evidence IDs and sanitized reason strings are.
        """
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            org_id, _, specs, _ = self._load(db, run_id)
            self._check_running(db, run_id, step_id, attempt_version)
            if (context.run_id != run_id or context.org_id != org_id or
                    context.step_id != step_id):
                raise TransitionError("Verification context does not match checkpoint")
            verdict = verify_step(context, specs[step_id], observations)
            summary = json.dumps({"reasons": verdict.reasons,
                                  "evidence_ids": verdict.evidence_ids})
            db.execute("UPDATE checkpoints SET status=?, version=version+1, "
                       "verification_json=? WHERE run_id=? AND step_id=?",
                       (verdict.status, summary, run_id, step_id))
            return verdict

    def block_step(self, run_id: str, step_id: str,
                   attempt_version: int, status: str) -> None:
        """Record an executor-detected blocker, never an unverified PASS."""
        if status not in {"ENV_BLOCKED", "AUTH_BLOCKED"}:
            raise TransitionError("Only environment/auth blockers are accepted")
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._load(db, run_id)
            self._check_running(db, run_id, step_id, attempt_version)
            db.execute("UPDATE checkpoints SET status=?, version=version+1 "
                       "WHERE run_id=? AND step_id=?", (status, run_id, step_id))
