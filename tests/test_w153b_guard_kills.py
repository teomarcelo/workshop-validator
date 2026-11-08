"""Killing tests for W-153b item 2: guard mutants must be killed by these tests.

These tests must fail when only the specific guard is mutated.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import tempfile
import unittest

from workshop_validator import team_kit, team_state, watchdog

NOW = datetime(2026, 11, 7, 14, 0, 0, tzinfo=timezone.utc)


class GuardKillingTests(unittest.TestCase):
    """Tests that kill the specific guards for W-153b item 2."""

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

    def test_fresh_producer_guard_in_takeover_launch(self):
        """Kills: team_kit.py `if fresh: return NONE` mutated to `if False: return NONE`.

        When a fresh producer is active AND action would be TAKEOVER (nudged and grace
        period expired), takeover_launch must return NONE. Without the guard, it would
        launch a second producer.
        """
        # Set up IDLE state
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Set up idle-since and nudged-at to make action TAKEOVER
        idle_since = NOW - timedelta(minutes=60)
        nudged_at = NOW - timedelta(minutes=40)  # Past NUDGE_GRACE_MINUTES (30)
        team_kit._write_private(self.team / "idle-since", idle_since.isoformat())
        team_kit._write_private(self.team / "nudged-at", nudged_at.isoformat())

        # Claim producer seat and create fresh transcript
        session_id = "active-producer"
        team_kit.claim_producer(self.team, session_id=session_id, now=idle_since)
        transcript = self.transcripts / f"{session_id}.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")

        # Touch transcript to show recent activity (fresh producer)
        import os
        os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))

        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        # With the guard, no launch happens
        status, message = team_kit.takeover_launch(self.repo, self.team, NOW, fake_launcher)
        self.assertEqual(status, "NONE", "fresh producer guard should prevent launch")
        self.assertIn("producer is fresh", message)
        self.assertEqual(len(launches), 0, "No launch should occur with fresh producer")

    def test_action_idle_guard_in_run_once(self):
        """Kills: watchdog.py `action == "idle"` mutated to `action == "never"`.

        When action is "idle", run_once checks for takeover. With the guard removed,
        it would skip the takeover check even when idle.
        """
        # Set up IDLE state (no job, ready step)
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        # With the guard, takeover check runs and launches
        result = watchdog.run_once(
            self.repo,
            self.root,
            launcher=None,  # Don't test job restart
            producer_launcher=fake_launcher,
            now=NOW
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(launches), 1, "Idle state should trigger takeover launch")

    def test_live_producer_plus_six_launches_in_24h(self):
        """Kills: team_kit.py fresh check mutated to `if False:` or moved after the rolling cap.

        When a live producer is active AND 6 launches already happened in 24h,
        takeover_launch must return NONE with no marker or print. The fresh check must
        happen BEFORE the rolling 24h cap, so the cap is never reached when a producer is live.
        """
        # Set up IDLE state
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Set up idle-since and nudged-at to make watchdog_action return TAKEOVER
        idle_since = NOW - timedelta(minutes=60)
        nudged_at = NOW - timedelta(minutes=40)
        team_kit._write_private(self.team / "idle-since", idle_since.isoformat())
        team_kit._write_private(self.team / "nudged-at", nudged_at.isoformat())

        # Claim producer seat and create fresh transcript
        session_id = "active-producer"
        team_kit.claim_producer(self.team, session_id=session_id, now=idle_since)
        transcript = self.transcripts / f"{session_id}.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        import os
        os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))

        # Record 6 launches in the last 24 hours (at cap)
        recent_launches = [NOW - timedelta(hours=i) for i in range(1, 7)]
        team_kit._write_private(
            self.team / "takeover-launches",
            team_kit.json.dumps([t.isoformat() for t in recent_launches], indent=2) + "\n"
        )

        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        # Test through takeover_launch
        status, message = team_kit.takeover_launch(self.repo, self.team, NOW, fake_launcher)
        self.assertEqual(status, "NONE", "fresh producer at rolling cap should return NONE")
        self.assertIn("producer is fresh", message)
        self.assertEqual(len(launches), 0, "No launch with fresh producer even at cap")
        self.assertFalse((self.team / "watchdog-waiting-owner").is_file(),
                        "No WAITING marker with fresh producer")

        # Test through watchdog_check
        result = team_kit.watchdog_check(self.repo, self.team, NOW, launcher=fake_launcher)
        self.assertIn("NONE", result)
        self.assertIn("producer is fresh", result)
        self.assertEqual(len(launches), 0, "watchdog_check: no launch with fresh producer at cap")
        self.assertFalse((self.team / "watchdog-waiting-owner").is_file(),
                        "watchdog_check: no WAITING marker with fresh producer")

        # Test through run_once
        result = watchdog.run_once(
            self.repo,
            self.root,
            launcher=None,
            producer_launcher=fake_launcher,
            now=NOW
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(launches), 0, "run_once: no launch with fresh producer at cap")
        self.assertFalse((self.team / "watchdog-waiting-owner").is_file(),
                        "run_once: no WAITING marker with fresh producer")

    def test_waiting_clear_after_successful_launch(self):
        """Kills: team_kit.py line ~504 `(team / "watchdog-waiting-owner").unlink(missing_ok=True)` deleted.

        After a successful takeover launch, the WAITING marker must be cleared.
        Without this line, a stale WAITING marker would persist incorrectly.
        """
        # Set up IDLE state
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Write a stale WAITING marker from a previous failed launch
        waiting_marker = self.team / "watchdog-waiting-owner"
        team_kit._write_private(
            waiting_marker,
            team_kit.json.dumps({"reason": "stale failure", "at": NOW.isoformat()}) + "\n"
        )
        self.assertTrue(waiting_marker.is_file(), "Precondition: WAITING marker exists")

        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        # Successful launch should clear the marker
        status, _ = team_kit.takeover_launch(self.repo, self.team, NOW, fake_launcher)
        self.assertEqual(status, "LAUNCHED")
        self.assertEqual(len(launches), 1)
        self.assertFalse(waiting_marker.is_file(),
                        "WAITING marker must be cleared after successful launch")

    def test_once_passes_launch_producer_wake(self):
        """Kills: watchdog.py line ~316 `producer_launcher=launch_producer_wake` mutated to a no-op.

        When --once is used, run_once must be called with producer_launcher=launch_producer_wake,
        not None or a fake. This ensures the real producer wake launcher is used.
        """
        # Set up IDLE state
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        with mock.patch.object(watchdog, "run_once", return_value=0) as run_once_mock, \
                mock.patch.object(watchdog, "cleanup_worktrees"):
            watchdog.main(["--once", "--repo", str(self.repo), "--private-root", str(self.root)])

        run_once_mock.assert_called_once()
        call_kwargs = run_once_mock.call_args[1]
        self.assertIs(call_kwargs["producer_launcher"], watchdog.launch_producer_wake,
                     "main --once must pass the real launch_producer_wake function")

    def test_log_file_mode_0600_on_create(self):
        """Kills: watchdog.py line ~173 in launch_producer_wake: os.open mode 0o600 mutated to 0o644.

        When launch_producer_wake creates watchdog.log for the first time, it must use mode 0o600.
        This prevents other users from reading producer launch logs.
        """
        import stat as stat_module

        log_path = self.team / "watchdog.log"
        self.assertFalse(log_path.exists(), "Precondition: log file does not exist yet")

        fake_claude = self.repo / "fake-claude"
        fake_claude.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
        fake_claude.chmod(0o700)

        with mock.patch.dict("os.environ", {"WORKSHOP_VALIDATOR_CLAUDE": str(fake_claude)}), \
                mock.patch.object(watchdog.subprocess, "Popen", return_value=mock.Mock()):
            watchdog.launch_producer_wake(self.repo, log_path)

        self.assertTrue(log_path.is_file(), "Log file should be created")
        mode = stat_module.S_IMODE(log_path.stat().st_mode)
        self.assertEqual(mode, 0o600,
                        f"Log file mode must be 0o600, got {oct(mode)}")



if __name__ == "__main__":
    unittest.main()
