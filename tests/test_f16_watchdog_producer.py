"""F16: the watchdog never restarts a job a live producer is handling."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import json
import os
import tempfile
import unittest

from workshop_validator import team_kit
from workshop_validator.lessons import TEAM, read_lessons
from workshop_validator.team_state import _write_private, load, start_job
from workshop_validator.watchdog import RESTART_CAP_PER_HOUR, decide, run_once

NOW = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(minutes=40)
DRIVEN_BY_PRODUCER = ["fix", "test", "audit", "publish", "lessons", "report"]


def _state(phase: str, restarts=None) -> dict:
    return {"phase": phase, "heartbeat": STALE.isoformat(), "lock": None,
            "restarts": restarts or []}


class DecideTests(unittest.TestCase):
    def test_stale_heartbeat_with_fresh_producer_is_alive(self):
        self.assertEqual(decide(_state("run"), now=NOW, producer_fresh=True), "alive")
        self.assertEqual(decide(_state("verify"), now=NOW, producer_fresh=True), "alive")

    def test_stale_heartbeat_in_a_producer_phase_is_waiting(self):
        for phase in DRIVEN_BY_PRODUCER:
            with self.subTest(phase=phase):
                self.assertEqual(decide(_state(phase), now=NOW), "waiting")

    def test_producer_phase_at_the_restart_cap_is_waiting_not_stalled(self):
        capped = [(NOW - timedelta(minutes=i + 1)).isoformat() for i in range(RESTART_CAP_PER_HOUR)]
        self.assertEqual(decide(_state("fix", capped), now=NOW), "waiting")

    def test_stale_run_and_verify_without_producer_restart(self):
        for phase in ("run", "verify"):
            with self.subTest(phase=phase):
                self.assertEqual(decide(_state(phase), now=NOW), "restart")

    def test_restart_cap_still_stalls_a_run_phase(self):
        capped = [(NOW - timedelta(minutes=i + 1)).isoformat() for i in range(RESTART_CAP_PER_HOUR)]
        self.assertEqual(decide(_state("run", capped), now=NOW), "stalled")


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.team = self.root / "team"
        self.launched = []

    def tearDown(self):
        self.tmp.cleanup()

    def _job(self, phase: str) -> None:
        start_job(self.root, "guide.pdf", "quick", now=STALE.isoformat())
        state = load(self.root)
        state["phase"] = phase
        _write_private(self.team / "state.json", json.dumps(state) + "\n")

    def _run(self, now=NOW) -> int:
        return run_once(self.root, self.root,
                        launcher=lambda r, l, v: self.launched.append(r), now=now)

    def _waiting_entries(self) -> list:
        path = self.team / "team-log.json"
        entries = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        return [e for e in entries if e.get("action") == "waiting"]

    def test_fresh_producer_claim_blocks_the_restart(self):
        self._job("run")
        team_kit.claim_producer(self.team, now=NOW - timedelta(minutes=10))
        self.assertEqual(self._run(), 0)
        self.assertEqual(self.launched, [])
        self.assertEqual(load(self.root)["restarts"], [])
        self.assertEqual(read_lessons(self.root, kind=TEAM), [])

    def test_stale_producer_claim_falls_through_to_restart(self):
        self._job("run")
        old = NOW - timedelta(minutes=team_kit.PRODUCER_ALIVE_MINUTES + 5)
        team_kit.claim_producer(self.team, now=old)
        self.assertEqual(self._run(), 0)
        self.assertEqual(len(self.launched), 1)

    def test_bound_producer_is_fresh_from_its_transcript(self):
        self._job("run")
        now = datetime.now(timezone.utc)
        team_kit.claim_producer(self.team, session_id="synthetic-session", now=now - timedelta(hours=5))
        transcripts = self.root / "transcripts"
        transcripts.mkdir()
        transcript = transcripts / "synthetic-session.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        recent = (now - timedelta(minutes=5)).timestamp()
        os.utime(transcript, (recent, recent))
        with mock.patch.object(team_kit, "transcripts_dir", return_value=transcripts):
            self.assertEqual(self._run(now=now), 0)
            self.assertEqual(self.launched, [])
            old = (now - timedelta(minutes=team_kit.PRODUCER_ALIVE_MINUTES + 5)).timestamp()
            os.utime(transcript, (old, old))
            self._run(now=now)
        self.assertEqual(len(self.launched), 1)

    def test_producer_phase_waits_without_stall_marker_or_lesson(self):
        self._job("fix")
        for i in range(RESTART_CAP_PER_HOUR):
            state = load(self.root)
            state["restarts"].append((NOW - timedelta(minutes=i + 1)).isoformat())
            _write_private(self.team / "state.json", json.dumps(state) + "\n")
        self.assertEqual(self._run(), 0)
        self.assertEqual(self.launched, [])
        self.assertFalse((self.team / "stalled.json").exists())
        self.assertEqual(read_lessons(self.root, kind=TEAM), [])
        self.assertEqual(len(self._waiting_entries()), 1)

    def test_waiting_is_logged_at_most_once_per_hour(self):
        self._job("audit")
        self._run(now=NOW)
        self._run(now=NOW + timedelta(minutes=5))
        self._run(now=NOW + timedelta(minutes=59))
        self.assertEqual(len(self._waiting_entries()), 1)
        self._run(now=NOW + timedelta(minutes=61))
        self.assertEqual(len(self._waiting_entries()), 2)
        self.assertEqual(self.launched, [])
        self.assertEqual(read_lessons(self.root, kind=TEAM), [])


if __name__ == "__main__":
    unittest.main()
