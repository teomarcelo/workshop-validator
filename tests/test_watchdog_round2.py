"""Watchdog takeover, round 2: subagent activity, no-job masking, naive and future
times, malformed producer-live, the producer-live lock and who may refresh last_seen.

Synthetic data only; every path is a temp root.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import json
import os
import tempfile
import threading
import unittest

from workshop_validator import public_safety, team_kit, team_state, watchdog

NOW = datetime(2026, 11, 4, 20, 0, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(hours=14)
DAY_AGO = NOW - timedelta(days=1)


def _age(path: Path, moment: datetime) -> None:
    os.utime(path, (moment.timestamp(), moment.timestamp()))


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)
        self._plan([{"id": "S1", "state": "running"}])
        # An unrelated session (the hourly watchdog task itself) keeps status at WORKING.
        other = self.transcripts / "watchdog-task.jsonl"
        other.write_text("{}\n", encoding="utf-8")
        _age(other, NOW - timedelta(minutes=2))
        for patch in (mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
                      mock.patch.object(team_kit, "free_gb", return_value=100.0),
                      mock.patch.object(public_safety, "check_main_clean", return_value=(True, ""))):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def _plan(self, steps):
        team_kit.save_plan(self.team, {"steps": steps}, STALE)
        _age(team_kit.now_path(self.team), STALE)

    def _job(self, heartbeat: datetime) -> None:
        team_state.start_job(self.root, "guide.pdf", "quick", now=heartbeat.isoformat())
        team_state.advance(self.root, "fix", now=heartbeat.isoformat())

    def _bound_producer(self, main_sign: datetime, sid: str = "prod") -> None:
        team_kit.claim_producer(self.team, session_id=sid, now=DAY_AGO)
        transcript = self.transcripts / f"{sid}.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        _age(transcript, main_sign)

    def _subagent(self, rel: str, moment: datetime) -> Path:
        path = self.transcripts / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        _age(path, moment)
        return path

    def _write_live(self, payload) -> None:
        team_state._write_private(team_kit.producer_live_path(self.team), json.dumps(payload))

    def _clear_markers(self) -> None:
        for name in ("idle-since", "nudged-at", "takeover-at", "takeover-count"):
            (self.team / name).unlink(missing_ok=True)

    def _check(self) -> str:
        line = team_kit.watchdog_check(self.repo, self.team, NOW)
        self.assertNotIn("\n", line)
        return line

    def _active(self):
        return team_kit.producer_active_at(self.repo, team_kit.load_producer(self.team), NOW)


class SubagentActivityTests(_Base):
    """Finding 1: a producer waiting on a long executor is not quiet (F16)."""

    def test_fresh_subagent_transcript_keeps_producer_alive(self):
        self._job(STALE)
        self._bound_producer(NOW - timedelta(minutes=40))
        sub = self._subagent("prod/subagents/agent-1.jsonl", NOW - timedelta(minutes=1))
        line = self._check()
        self.assertTrue(line.startswith("NONE WORKING"), line)
        self.assertEqual(self._active(), datetime.fromtimestamp(sub.stat().st_mtime, timezone.utc))
        # Deeper nesting counts too; another session's subtree never does.
        deep = self._subagent("prod/subagents/nested/agent-2.jsonl", NOW - timedelta(seconds=10))
        self.assertEqual(self._active(), datetime.fromtimestamp(deep.stat().st_mtime, timezone.utc))
        self._subagent("someone-else/subagents/agent-3.jsonl", NOW - timedelta(seconds=1))
        self.assertEqual(self._active(), datetime.fromtimestamp(deep.stat().st_mtime, timezone.utc))


class SessionIdPathTests(_Base):
    """A session id that is not a plain name never reads another session's transcripts."""

    def _assert_not_activity(self, sid: str) -> None:
        self._job(STALE)
        (self.transcripts / "prod").mkdir(exist_ok=True)  # lets "prod/.." resolve
        main = self._subagent(f"{sid}.jsonl", NOW - timedelta(minutes=1))
        sub = self._subagent(f"{sid}/subagents/agent-1.jsonl", NOW - timedelta(minutes=1))
        self.assertTrue(main.is_file() and sub.is_file())
        team_kit.claim_producer(self.team, session_id=sid, now=DAY_AGO)
        self.assertEqual(self._active(), DAY_AGO)
        line = self._check()
        self.assertTrue(line.startswith(("TAKEOVER", "NUDGE")), line)

    def test_parent_escape_is_not_activity(self):
        self._assert_not_activity("../other")

    def test_dotdot_inside_id_is_not_activity(self):
        self._assert_not_activity("prod/../other")

    def test_nested_id_is_not_activity(self):
        self._assert_not_activity("a/b")


class NoOpenJobTests(_Base):
    """Finding 2: with no open job an unrelated transcript must not mask an idle chain."""

    def test_no_job_quiet_producer_is_takeover_and_branch_commit_keeps_none(self):
        self._bound_producer(STALE)
        line = self._check()
        self.assertTrue(line.startswith("TAKEOVER STALLED"), line)
        # A fresh commit on the running step's branch is real work: left alone.
        self._clear_markers()
        self._plan([{"id": "S1", "state": "running", "branch": "automation/x"}])
        with mock.patch.object(team_kit, "_branch_commit_time",
                               return_value=(NOW - timedelta(minutes=3)).timestamp()):
            line = self._check()
        self.assertTrue(line.startswith("NONE WORKING"), line)

    def test_no_job_producer_fresh_subagent_keeps_none(self):
        self._bound_producer(STALE)
        self.assertTrue(self._check().startswith("TAKEOVER"))
        self._clear_markers()
        self._subagent("prod/subagents/agent-1.jsonl", NOW - timedelta(minutes=1))
        self.assertTrue(self._check().startswith("NONE WORKING"))


class TimeSafetyTests(_Base):
    """Findings 3 and C: naive times are UTC, future times are ignored."""

    def test_naive_last_seen_is_read_as_utc(self):
        self._job(STALE)
        self._write_live({"session_id": "pending", "claimed": DAY_AGO.isoformat(),
                          "last_seen": (NOW - timedelta(minutes=5)).replace(tzinfo=None).isoformat()})
        line = self._check()
        self.assertTrue(line.startswith("NONE WORKING"), line)

    def test_naive_job_heartbeat_is_read_as_utc(self):
        self._job(STALE)
        state = team_state.load(self.root)
        state["heartbeat"] = STALE.replace(tzinfo=None).isoformat()
        team_state._write_private(self.root / "team" / team_state.STATE_NAME, json.dumps(state))
        line = self._check()
        self.assertTrue(line.startswith("TAKEOVER STALLED"), line)

    def test_future_last_seen_cannot_hide_a_stall(self):
        self._job(STALE)
        self._write_live({"session_id": "pending", "claimed": DAY_AGO.isoformat(),
                          "last_seen": (NOW + timedelta(days=1)).isoformat()})
        line = self._check()
        self.assertTrue(line.startswith("TAKEOVER STALLED"), line)


class MalformedProducerLiveTests(_Base):
    """Finding B: a broken producer-live never crashes the WORKING path."""

    def test_non_dict_and_bad_types_are_no_claim(self):
        for payload in ([1, 2], "pending", 7,
                        {"session_id": "pending", "claimed": DAY_AGO.isoformat(), "last_seen": 5},
                        {"session_id": ["x"], "claimed": DAY_AGO.isoformat()}):
            with self.subTest(payload=payload):
                self._clear_markers()
                self._job(STALE)
                self._write_live(payload)
                line = self._check()
                self.assertTrue(line.startswith("TAKEOVER STALLED"), line)
        self._write_live([1, 2])
        self.assertIsNone(team_kit.load_producer(self.team))
        self.assertFalse(team_kit.producer_is_fresh(self.repo, self.team, NOW))


class LastSeenOwnerTests(_Base):
    """Finding A: only the claimed session refreshes last_seen."""

    def test_stale_run_with_day_old_claim_restarts(self):
        team_state.start_job(self.root, "guide.pdf", "quick", now=DAY_AGO.isoformat())
        team_kit.claim_producer(self.team, session_id="prod", now=DAY_AGO)
        old = NOW - timedelta(minutes=40)
        team_state.advance(self.root, "run", now=old.isoformat())
        team_state.heartbeat(self.root, now=old.isoformat())
        self.assertNotIn("last_seen", team_kit.load_producer(self.team))
        fresh = team_kit.producer_is_fresh(self.repo, self.team, NOW)
        self.assertFalse(fresh)
        self.assertEqual(watchdog.decide(team_state.load(self.root), NOW, producer_fresh=fresh), "restart")

    def test_only_the_claimed_session_refreshes(self):
        team_state.start_job(self.root, "guide.pdf", "quick", now=DAY_AGO.isoformat())
        team_kit.claim_producer(self.team, session_id="prod", now=DAY_AGO)
        team_state.heartbeat(self.root, now=NOW.isoformat(), session_id="someone-else")
        self.assertNotIn("last_seen", team_kit.load_producer(self.team))
        team_state.heartbeat(self.root, now=NOW.isoformat(), session_id="prod")
        self.assertEqual(team_kit.load_producer(self.team)["last_seen"], NOW.isoformat())


class ProducerLiveLockTests(_Base):
    """Finding 4: a last_seen refresh never resurrects a release or restores an old claim."""

    def _race(self, rival):
        """Run `rival` in a thread at the moment touch is about to write producer-live."""
        real = team_state._write_private
        threads = []

        def racing_write(path, text):
            if Path(path).name == team_state.PRODUCER_LIVE_NAME and not threads:
                thread = threading.Thread(target=rival)
                threads.append(thread)
                thread.start()
                thread.join(0.3)  # under the lock the rival waits; without it, it lands first
            real(path, text)

        with mock.patch.object(team_state, "_write_private", side_effect=racing_write):
            team_state.heartbeat(self.root, now=NOW.isoformat(), session_id="prod")
        for thread in threads:
            thread.join(5)
        self.assertTrue(threads)

    def setUp(self):
        super().setUp()
        team_state.start_job(self.root, "guide.pdf", "quick", now=DAY_AGO.isoformat())
        team_kit.claim_producer(self.team, session_id="prod", now=DAY_AGO)

    def test_touch_does_not_resurrect_a_released_claim(self):
        self._race(lambda: team_kit.release_producer(self.team))
        self.assertFalse(team_kit.producer_live_path(self.team).exists())

    def test_touch_does_not_restore_an_old_session(self):
        self._race(lambda: team_kit.claim_producer(self.team, now=NOW))
        live = team_kit.load_producer(self.team)
        self.assertEqual(live["session_id"], "pending")
        self.assertEqual(live["claimed"], NOW.isoformat())


if __name__ == "__main__":
    unittest.main()
