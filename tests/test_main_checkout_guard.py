"""F9: Tests for main checkout branch guard in team_hooks and team_kit."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


class TeamHooksMainCheckoutTests(unittest.TestCase):
    """F9 item 2: team_hooks stop blocks when main checkout is not on main."""

    def test_stop_blocks_when_main_on_other_branch(self):
        """stop_decision should block when main checkout is on a different branch."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()
            team = Path(tmp) / "team"
            team.mkdir(parents=True)

            # Initialize repo
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Switch to other branch
            subprocess.run(["git", "checkout", "-b", "review"], cwd=main_repo, check=True, capture_output=True)

            # Create producer-live
            (team / "producer-live").write_text(json.dumps({"session_id": "test-session"}), encoding="utf-8")
            (team / "producer-live").chmod(0o600)

            from workshop_validator.team_hooks import stop_decision
            payload = {"session_id": "test-session"}
            reason = stop_decision(payload, team=team, repo=main_repo, status_fn=lambda r, t: ("WORKING", "test"))

            self.assertIsNotNone(reason)
            self.assertIn("review", reason)
            self.assertIn("not main", reason.lower())

    def test_stop_blocks_when_main_detached(self):
        """stop_decision should block when main checkout HEAD is detached."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()
            team = Path(tmp) / "team"
            team.mkdir(parents=True)

            # Initialize repo
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Detach HEAD
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=main_repo, check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "checkout", "--detach", sha[:7]], cwd=main_repo, check=True, capture_output=True)

            # Create producer-live
            (team / "producer-live").write_text(json.dumps({"session_id": "test-session"}), encoding="utf-8")
            (team / "producer-live").chmod(0o600)

            from workshop_validator.team_hooks import stop_decision
            payload = {"session_id": "test-session"}
            reason = stop_decision(payload, team=team, repo=main_repo, status_fn=lambda r, t: ("WORKING", "test"))

            self.assertIsNotNone(reason)
            self.assertIn("detached", reason.lower())

    def test_stop_passes_when_main_on_main(self):
        """stop_decision should not block when main checkout is on main branch."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()
            team = Path(tmp) / "team"
            team.mkdir(parents=True)

            # Initialize repo on main
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Create producer-live
            (team / "producer-live").write_text(json.dumps({"session_id": "test-session"}), encoding="utf-8")
            (team / "producer-live").chmod(0o600)

            from workshop_validator.team_hooks import stop_decision
            payload = {"session_id": "test-session"}
            reason = stop_decision(payload, team=team, repo=main_repo, status_fn=lambda r, t: ("WORKING", "test"))

            self.assertIsNone(reason)


class TeamKitWatchdogMainCheckoutTests(unittest.TestCase):
    """F9 item 3: team_kit watchdog reports STALLED when main checkout is not on main."""

    def test_watchdog_reports_stalled_when_main_on_other_branch(self):
        """watchdog_check should report STALLED when main checkout is on a different branch."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()
            team = Path(tmp) / "team"
            team.mkdir(parents=True)

            # Initialize repo
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Switch to other branch
            subprocess.run(["git", "checkout", "-b", "review"], cwd=main_repo, check=True, capture_output=True)

            # Create minimal plan
            (team / "progress").mkdir(parents=True)
            (team / "progress" / "now.json").write_text(json.dumps({"steps": []}), encoding="utf-8")
            (team / "progress" / "now.json").chmod(0o600)

            from workshop_validator.team_kit import watchdog_check
            result = watchdog_check(main_repo, team)

            self.assertIn("NONE STALLED", result)
            self.assertIn("review", result)

    def test_watchdog_reports_stalled_when_main_detached(self):
        """watchdog_check should report STALLED when main checkout HEAD is detached."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()
            team = Path(tmp) / "team"
            team.mkdir(parents=True)

            # Initialize repo
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Detach HEAD
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=main_repo, check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "checkout", "--detach", sha[:7]], cwd=main_repo, check=True, capture_output=True)

            # Create minimal plan
            (team / "progress").mkdir(parents=True)
            (team / "progress" / "now.json").write_text(json.dumps({"steps": []}), encoding="utf-8")
            (team / "progress" / "now.json").chmod(0o600)

            from workshop_validator.team_kit import watchdog_check
            result = watchdog_check(main_repo, team)

            self.assertIn("NONE STALLED", result)
            self.assertIn("detached", result.lower())


if __name__ == "__main__":
    unittest.main()
