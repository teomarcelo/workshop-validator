"""Escape 2026-11-04: a dead producer behind a WORKING chain must still be taken over."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import json
import os
import stat
import tempfile
import unittest

from workshop_validator import public_safety, team_kit, team_state

NOW = datetime(2026, 11, 4, 20, 0, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(hours=14)


def _age(path: Path, moment: datetime) -> None:
    os.utime(path, (moment.timestamp(), moment.timestamp()))


class WatchdogTakeoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "running"}]}, STALE)
        _age(team_kit.now_path(self.team), STALE)
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

    def _job(self, heartbeat: datetime) -> None:
        team_state.start_job(self.root, "guide.pdf", "quick", now=heartbeat.isoformat())
        team_state.advance(self.root, "fix", now=heartbeat.isoformat())

    def _bound_producer(self, last_sign: datetime) -> None:
        team_kit.claim_producer(self.team, session_id="old-session", now=NOW - timedelta(days=1))
        transcript = self.transcripts / "old-session.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        _age(transcript, last_sign)

    def test_status_alone_says_working(self):
        self._job(STALE)
        self.assertEqual(team_kit.status(self.repo, self.team, NOW)[0], "WORKING")

    def test_stale_job_and_stale_producer_is_takeover(self):
        self._job(STALE)
        team_kit.claim_producer(self.team, now=NOW - timedelta(days=1))
        line = team_kit.watchdog_check(self.repo, self.team, NOW)
        self.assertTrue(line.startswith("TAKEOVER STALLED"), line)
        self.assertNotIn("\n", line)

    def test_stale_job_and_dead_bound_producer_is_takeover(self):
        self._job(STALE)
        self._bound_producer(STALE)
        line = team_kit.watchdog_check(self.repo, self.team, NOW)
        self.assertTrue(line.startswith("TAKEOVER"), line)

    def test_stale_job_and_no_claim_is_takeover(self):
        self._job(STALE)
        self.assertTrue(team_kit.watchdog_check(self.repo, self.team, NOW).startswith("TAKEOVER"))

    def test_quiet_but_reachable_producer_is_nudged(self):
        self._job(STALE)
        self._bound_producer(NOW - timedelta(minutes=team_kit.STALL_MINUTES + 20))
        self.assertTrue(team_kit.watchdog_check(self.repo, self.team, NOW).startswith("NUDGE"))

    def test_stale_job_and_fresh_producer_is_left_alone(self):
        self._job(STALE)
        self._bound_producer(NOW - timedelta(minutes=5))
        line = team_kit.watchdog_check(self.repo, self.team, NOW)
        self.assertTrue(line.startswith("NONE WORKING"), line)

    def test_fresh_last_seen_keeps_a_pending_producer_alive(self):
        self._job(STALE)
        team_kit.claim_producer(self.team, now=NOW - timedelta(days=1))
        live = team_kit.load_producer(self.team)
        live["last_seen"] = (NOW - timedelta(minutes=5)).isoformat()
        team_state._write_private(team_kit.producer_live_path(self.team), json.dumps(live))
        self.assertTrue(team_kit.watchdog_check(self.repo, self.team, NOW).startswith("NONE"))

    def test_fresh_job_is_none(self):
        self._job(NOW - timedelta(minutes=3))
        team_kit.claim_producer(self.team, now=NOW - timedelta(days=1))
        line = team_kit.watchdog_check(self.repo, self.team, NOW)
        self.assertTrue(line.startswith("NONE WORKING"), line)


class ProducerLastSeenTests(unittest.TestCase):
    def test_heartbeat_and_advance_refresh_last_seen_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            team = root / "team"
            team_state.start_job(root, "guide.pdf", "quick", now=STALE.isoformat())
            team_kit.claim_producer(team, now=STALE)
            team_state.heartbeat(root, now=NOW.isoformat())
            live = team_kit.load_producer(team)
            self.assertEqual(live["last_seen"], NOW.isoformat())
            self.assertEqual(live["claimed"], STALE.isoformat())
            later = (NOW + timedelta(minutes=1)).isoformat()
            team_state.advance(root, "fix", now=later)
            self.assertEqual(team_kit.load_producer(team)["last_seen"], later)
            mode = stat.S_IMODE(team_kit.producer_live_path(team).stat().st_mode)
            self.assertEqual(mode, 0o600)
            self.assertEqual(team_kit.producer_active_at(root, team_kit.load_producer(team)),
                             datetime.fromisoformat(later))

    def test_heartbeat_without_claim_creates_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            team_state.start_job(root, "guide.pdf", "quick", now=STALE.isoformat())
            team_state.heartbeat(root, now=NOW.isoformat())
            self.assertFalse(team_kit.producer_live_path(root / "team").exists())


if __name__ == "__main__":
    unittest.main()
