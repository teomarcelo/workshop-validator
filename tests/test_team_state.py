"""Tests for team_state module."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest

from workshop_validator.team_state import (
    PHASES,
    acquire_lock,
    advance,
    finish,
    heartbeat,
    is_open,
    is_stale,
    load,
    log,
    release_lock,
    start_job,
)


class TestTeamState(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.now = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_start_job(self):
        state = start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        self.assertEqual(state["pdf"], "guide.pdf")
        self.assertEqual(state["mode"], "quick")
        self.assertEqual(state["phase"], "preflight")
        self.assertEqual(state["iteration"], 0)
        self.assertIsNone(state["run_dir"])
        self.assertIsNone(state["outcome"])
        self.assertEqual(state["job_id"], "20261022T100000Z")

    def test_load_no_file(self):
        self.assertIsNone(load(self.root))

    def test_advance_phase(self):
        start_job(self.root, "guide.pdf", "full", now=self.now.isoformat())
        later = (self.now + timedelta(minutes=1)).isoformat()
        state = advance(self.root, "run", run_dir="/tmp/run", detail="Starting", now=later)
        self.assertEqual(state["phase"], "run")
        self.assertEqual(state["run_dir"], "/tmp/run")
        self.assertEqual(state["iteration"], 1)
        self.assertEqual(state["heartbeat"], later)

    def test_advance_increments_iteration_on_run(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        advance(self.root, "run", now=self.now.isoformat())
        state = advance(self.root, "verify", now=self.now.isoformat())
        self.assertEqual(state["iteration"], 1)
        advance(self.root, "run", now=self.now.isoformat())
        state = load(self.root)
        self.assertEqual(state["iteration"], 2)

    def test_heartbeat_updates_time(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        later = (self.now + timedelta(minutes=5)).isoformat()
        state = heartbeat(self.root, now=later)
        self.assertEqual(state["heartbeat"], later)

    def test_log_appends_entries(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        log(self.root, "runner", "start", "Beginning run", "/path", now=self.now.isoformat())
        log_path = self.root / "team" / "team-log.json"
        entries = json.loads(log_path.read_text())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["agent"], "runner")
        self.assertEqual(entries[0]["action"], "start")
        self.assertEqual(entries[0]["detail"], "Beginning run")
        self.assertEqual(entries[0]["link"], "/path")

    def test_finish_marks_done(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        state = finish(self.root, "success", now=self.now.isoformat())
        self.assertEqual(state["phase"], "done")
        self.assertEqual(state["outcome"], "success")

    def test_is_stale_old_heartbeat(self):
        state = {"heartbeat": self.now.isoformat()}
        later = self.now + timedelta(minutes=20)
        self.assertTrue(is_stale(state, later, minutes=15))

    def test_is_stale_recent_heartbeat(self):
        state = {"heartbeat": self.now.isoformat()}
        later = self.now + timedelta(minutes=10)
        self.assertFalse(is_stale(state, later, minutes=15))

    def test_acquire_lock_when_free(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        result = acquire_lock(self.root, 12345, alive=lambda p: False, now=self.now.isoformat())
        self.assertTrue(result)
        state = load(self.root)
        self.assertEqual(state["lock"]["pid"], 12345)

    def test_acquire_lock_held_by_live_pid(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        acquire_lock(self.root, 99999, alive=lambda p: True, now=self.now.isoformat())
        result = acquire_lock(self.root, 12345, alive=lambda p: p == 99999, now=self.now.isoformat())
        self.assertFalse(result)

    def test_acquire_lock_held_by_dead_pid(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        acquire_lock(self.root, 99999, alive=lambda p: False, now=self.now.isoformat())
        result = acquire_lock(self.root, 12345, alive=lambda p: False, now=self.now.isoformat())
        self.assertTrue(result)
        state = load(self.root)
        self.assertEqual(state["lock"]["pid"], 12345)

    def test_release_lock(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        acquire_lock(self.root, 12345, alive=lambda p: False, now=self.now.isoformat())
        state = release_lock(self.root, now=self.now.isoformat())
        self.assertIsNone(state["lock"])

    def test_is_open_when_running(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        state = load(self.root)
        self.assertTrue(is_open(state))

    def test_is_open_when_done(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        finish(self.root, "success", now=self.now.isoformat())
        state = load(self.root)
        self.assertFalse(is_open(state))

    def test_file_modes(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        state_path = self.root / "team" / "state.json"
        log_path = self.root / "team" / "team-log.json"
        self.assertEqual(state_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(log_path.stat().st_mode & 0o777, 0o600)
        team_dir = self.root / "team"
        self.assertEqual(team_dir.stat().st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
