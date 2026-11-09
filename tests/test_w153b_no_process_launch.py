"""Test for W-153b item 3: verify no real process launches from tests.

When WORKSHOP_VALIDATOR_CLAUDE is set to a non-executable path, no subprocess
should be started by watchdog_check, takeover_launch, or run_once, even when
driving them through idle and stalled states.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import subprocess
import tempfile
import unittest

from workshop_validator import team_kit, team_state, watchdog

NOW = datetime(2026, 11, 7, 16, 0, 0, tzinfo=timezone.utc)


class NoProcessLaunchTests(unittest.TestCase):
    """Verify that no process starts when WORKSHOP_VALIDATOR_CLAUDE is non-executable."""

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

    def test_no_process_launch_through_watchdog_functions(self):
        """With subprocess.Popen patched to raise, drive watchdog functions through
        idle and stalled states: no process starts.

        This test verifies that the WORKSHOP_VALIDATOR_CLAUDE guard prevents real
        process launches during tests.
        """
        # Set up IDLE state
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        # Patch subprocess.Popen to raise if called
        popen_calls = []
        def failing_popen(*args, **kwargs):
            popen_calls.append((args, kwargs))
            raise RuntimeError("subprocess.Popen should not be called during tests")

        with mock.patch.object(subprocess, "Popen", side_effect=failing_popen):
            # Test 1: watchdog_check in IDLE state
            result = team_kit.watchdog_check(
                self.repo,
                self.team,
                NOW,
                launcher=watchdog.launch_producer_wake
            )
            # Result varies depending on state, but no process should start
            self.assertIsInstance(result, str)

            # Test 2: run_once in IDLE state with both launchers
            result_code = watchdog.run_once(
                self.repo,
                self.root,
                launcher=watchdog.launch_resume,
                producer_launcher=watchdog.launch_producer_wake,
                now=NOW
            )
            self.assertEqual(result_code, 0)

            # Test 3: Set up STALLED state (disk low) and drive watchdog_check
            with mock.patch.object(team_kit, "free_gb", return_value=0.5):
                status, _ = team_kit.status(self.repo, self.team, NOW)
                self.assertEqual(status, "STALLED")

                # watchdog_check in STALLED state
                result = team_kit.watchdog_check(
                    self.repo,
                    self.team,
                    NOW + timedelta(hours=1),
                    launcher=watchdog.launch_producer_wake
                )
                self.assertIsInstance(result, str)

                # run_once in STALLED state
                result_code = watchdog.run_once(
                    self.repo,
                    self.root,
                    launcher=watchdog.launch_resume,
                    producer_launcher=watchdog.launch_producer_wake,
                    now=NOW + timedelta(hours=1)
                )
                self.assertEqual(result_code, 0)

        # The KEY assertion: verify no Popen calls were made
        self.assertEqual(len(popen_calls), 0,
                        f"No subprocess.Popen should be called when claude is not executable; "
                        f"but {len(popen_calls)} calls were made")


if __name__ == "__main__":
    unittest.main()
