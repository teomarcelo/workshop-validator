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

    def test_not_fresh_guard_in_run_once(self):
        """Kills: watchdog.py `not fresh` mutated to `True`.

        This guard is defensive: normally if action is idle, fresh is False. We test
        by mocking decide() to return "idle" even when fresh is True, forcing the
        guard to be the only thing preventing a launch.
        """
        # Set up IDLE state (no job, ready step)
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Mock producer to be fresh
        session_id = "active-producer"
        team_kit.claim_producer(self.team, session_id=session_id, now=NOW)
        transcript = self.transcripts / f"{session_id}.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        import os
        os.utime(transcript, (NOW.timestamp(), NOW.timestamp()))

        launches = []
        def fake_launcher(repo, log_path):
            launches.append((repo, log_path))

        # Mock decide() to return "idle" even though producer is fresh
        # This forces the `not fresh` guard to be the only defense
        with mock.patch.object(watchdog, "decide", return_value="idle"):
            # With the guard, no launch happens (fresh=True blocks it)
            result = watchdog.run_once(
                self.repo,
                self.root,
                launcher=None,
                producer_launcher=fake_launcher,
                now=NOW
            )
            self.assertEqual(result, 0)
            self.assertEqual(len(launches), 0, "Not-fresh guard should prevent launch when fresh is True")


if __name__ == "__main__":
    unittest.main()
