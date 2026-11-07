"""Tests for W-153b item 1: WORKING clears markers (idle-since, nudged-at, takeover-at, takeover-count, watchdog-waiting-owner).

Tests that drive idle -> WORKING -> idle through watchdog_check WITHOUT calling _clear_markers() by hand.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import json
import tempfile
import unittest

from workshop_validator import team_kit, team_state, watchdog

NOW = datetime(2026, 11, 7, 12, 0, 0, tzinfo=timezone.utc)


class WorkingClearsMarkersTests(unittest.TestCase):
    """Test that WORKING state clears takeover markers in the live paths."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        from workshop_validator import public_safety
        for patch in (
            mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
            mock.patch.object(team_kit, "free_gb", return_value=100.0),
            mock.patch.object(public_safety, "check_main_clean", return_value=(True, "")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_idle_takeovers_working_idle_gives_takeover(self):
        """After 3 takeovers, WORKING, idle: next idle gives TAKEOVER (not WAITING).

        This test drives the full sequence through watchdog_check without calling
        _clear_markers() by hand. The WORKING state must clear the markers so that
        the next idle stretch starts fresh.
        """
        # Set up plan with a next step
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Start with idle
        status, _ = team_kit.status(self.repo, self.team, NOW)
        self.assertEqual(status, "IDLE")

        # Do 3 takeovers
        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        for i in range(3):
            t = NOW + timedelta(hours=i)
            result = team_kit.watchdog_check(self.repo, self.team, t, launcher=fake_launcher)
            self.assertIn("LAUNCHED", result, f"Takeover {i+1} should launch")

        self.assertEqual(len(launches), 3)

        # Verify markers are present after 3 takeovers
        self.assertTrue((self.team / "idle-since").is_file())
        self.assertTrue((self.team / "takeover-at").is_file())
        self.assertTrue((self.team / "takeover-count").is_file())

        # Read the takeover count
        count = int((self.team / "takeover-count").read_text(encoding="utf-8").strip())
        self.assertEqual(count, 3)

        # Now simulate WORKING state by opening a job and having a running step
        t_working = NOW + timedelta(hours=4)
        team_state.start_job(self.root, "guide.pdf", "quick", now=(t_working - timedelta(minutes=5)).isoformat())
        team_kit.save_plan(
            self.team,
            {"steps": [{"id": "S1", "state": "running", "branch": "test-branch"}]},
            t_working
        )

        # Check status is WORKING
        status, _ = team_kit.status(self.repo, self.team, t_working)
        self.assertEqual(status, "WORKING")

        # Call watchdog_check in WORKING state - should clear markers
        result = team_kit.watchdog_check(self.repo, self.team, t_working, launcher=fake_launcher)
        self.assertIn("NONE WORKING", result)

        # Verify markers are cleared
        self.assertFalse((self.team / "idle-since").is_file(), "idle-since should be cleared")
        self.assertFalse((self.team / "nudged-at").is_file(), "nudged-at should be cleared")
        self.assertFalse((self.team / "takeover-at").is_file(), "takeover-at should be cleared")
        self.assertFalse((self.team / "takeover-count").is_file(), "takeover-count should be cleared")
        self.assertFalse((self.team / "watchdog-waiting-owner").is_file(), "watchdog-waiting-owner should be cleared")

        # Set a new ready step to make status IDLE again (finish the job first)
        t_idle_again = NOW + timedelta(hours=6)
        # Simply remove the job by deleting state.json
        import os
        try:
            os.unlink(team_state._state_path(self.root))
        except FileNotFoundError:
            pass
        team_kit.save_plan(
            self.team,
            {"steps": [{"id": "S2", "state": "next"}]},
            t_idle_again
        )

        # Check status is IDLE again
        status, _ = team_kit.status(self.repo, self.team, t_idle_again)
        self.assertEqual(status, "IDLE")

        # Next watchdog_check should give TAKEOVER (not WAITING) because markers were cleared
        launches.clear()
        result = team_kit.watchdog_check(self.repo, self.team, t_idle_again, launcher=fake_launcher)
        self.assertIn("LAUNCHED", result, "After WORKING cleared markers, idle should give TAKEOVER/LAUNCHED")
        self.assertEqual(len(launches), 1, "Should launch on the new idle")

    def test_nudge_working_idle_nudge_again(self):
        """NUDGE, then WORKING, then idle with live producer: NUDGE again.

        This verifies that WORKING clears nudged-at so a fresh idle gets NUDGE again.
        """
        # Set up plan with a next step
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Claim producer seat
        session_id = "test-session"
        team_kit.claim_producer(self.team, session_id=session_id, now=NOW)
        transcript = self.transcripts / f"{session_id}.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        # Touch the transcript to show activity
        import os
        os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))

        # Status should be IDLE with a live producer
        status, _ = team_kit.status(self.repo, self.team, NOW)
        self.assertEqual(status, "IDLE")

        # After idle delay, should get NUDGE
        t_nudge = NOW + timedelta(minutes=31)
        os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))  # Keep producer fresh

        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        result = team_kit.watchdog_check(self.repo, self.team, t_nudge, launcher=fake_launcher)
        self.assertIn("NUDGE", result)
        self.assertEqual(len(launches), 0, "NUDGE should not launch")
        self.assertTrue((self.team / "nudged-at").is_file())

        # Now simulate WORKING state by opening a job and having a running step
        t_working = t_nudge + timedelta(hours=1)
        team_state.start_job(self.root, "guide.pdf", "quick", now=(t_working - timedelta(minutes=5)).isoformat())
        team_kit.save_plan(
            self.team,
            {"steps": [{"id": "S1", "state": "running", "branch": "test-branch"}]},
            t_working
        )

        # Call watchdog_check in WORKING state - should clear nudged-at
        result = team_kit.watchdog_check(self.repo, self.team, t_working, launcher=fake_launcher)
        self.assertIn("NONE WORKING", result)
        self.assertFalse((self.team / "nudged-at").is_file(), "nudged-at should be cleared by WORKING")

        # Set a new ready step to make status IDLE again (finish the job first)
        t_idle_again = t_working + timedelta(hours=1)
        # Simply remove the job by deleting state.json
        import os
        try:
            os.unlink(team_state._state_path(self.root))
        except FileNotFoundError:
            pass
        team_kit.save_plan(
            self.team,
            {"steps": [{"id": "S2", "state": "next"}]},
            t_idle_again
        )

        # Keep producer fresh
        os.utime(transcript, (t_idle_again.timestamp(), t_idle_again.timestamp()))

        # After idle delay, should get NUDGE again (not NONE)
        t_nudge_again = t_idle_again + timedelta(minutes=31)
        os.utime(transcript, (t_idle_again.timestamp(), t_idle_again.timestamp()))

        result = team_kit.watchdog_check(self.repo, self.team, t_nudge_again, launcher=fake_launcher)
        self.assertIn("NUDGE", result, "After WORKING cleared nudged-at, idle should give NUDGE again")
        self.assertTrue((self.team / "nudged-at").is_file())

    def test_takeover_launch_clears_markers_on_working(self):
        """takeover_launch also clears markers when state is WORKING.

        This verifies that takeover_launch (used by both watchdog_check and run_once)
        clears markers in WORKING state.
        """
        # Set up plan with a next step
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Do one takeover to create markers
        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        result = team_kit.watchdog_check(self.repo, self.team, NOW, launcher=fake_launcher)
        self.assertIn("LAUNCHED", result)

        # Verify markers exist
        self.assertTrue((self.team / "idle-since").is_file())
        self.assertTrue((self.team / "takeover-at").is_file())
        self.assertTrue((self.team / "takeover-count").is_file())

        # Now simulate WORKING state by opening a job and having a running step
        t_working = NOW + timedelta(hours=1)
        team_state.start_job(self.root, "guide.pdf", "quick", now=(t_working - timedelta(minutes=5)).isoformat())
        team_kit.save_plan(
            self.team,
            {"steps": [{"id": "S1", "state": "running", "branch": "test-branch"}]},
            t_working
        )

        # Call takeover_launch directly in WORKING state - should return NONE and clear markers
        status, message = team_kit.takeover_launch(self.repo, self.team, t_working, fake_launcher)
        self.assertEqual(status, "NONE")
        self.assertIn("WORKING", message)

        # Verify markers are cleared
        self.assertFalse((self.team / "idle-since").is_file())
        self.assertFalse((self.team / "nudged-at").is_file())
        self.assertFalse((self.team / "takeover-at").is_file())
        self.assertFalse((self.team / "takeover-count").is_file())
        self.assertFalse((self.team / "watchdog-waiting-owner").is_file())


if __name__ == "__main__":
    unittest.main()
