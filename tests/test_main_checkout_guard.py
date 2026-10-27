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
    """F9: team_kit watchdog reports STALLED through watchdog_action (NUDGE or TAKEOVER)."""

    def test_watchdog_reports_takeover_when_main_on_other_branch_no_live_producer(self):
        """watchdog_check should report TAKEOVER when main checkout is on a different branch and no live producer."""
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

            # Should report TAKEOVER (not NONE) because no live producer
            self.assertIn("TAKEOVER", result)
            self.assertIn("STALLED", result)
            self.assertIn("review", result)
            self.assertNotIn("NONE STALLED", result)

    def test_watchdog_reports_takeover_when_main_detached_no_live_producer(self):
        """watchdog_check should report TAKEOVER when main checkout HEAD is detached and no live producer."""
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

            # Should report TAKEOVER (not NONE) because no live producer
            self.assertIn("TAKEOVER", result)
            self.assertIn("STALLED", result)
            self.assertIn("detached", result.lower())
            self.assertNotIn("NONE STALLED", result)

    def test_watchdog_with_untracked_file_still_handles_real_stalled(self):
        """watchdog_check ignores dirty files; a real STALLED status still yields NUDGE/TAKEOVER."""
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

            # Add an untracked file (watchdog should ignore this)
            (main_repo / "untracked.txt").write_text("untracked content")

            # Create team state with stale heartbeat
            from workshop_validator.team_state import start_job, _write_private
            start_job(team.parent, "guide.pdf", "quick", now="2026-10-27T10:00:00+00:00")
            state_path = team.parent / "team" / "state.json"
            state = json.loads(state_path.read_text())
            state["heartbeat"] = "2026-10-27T10:00:00+00:00"
            _write_private(state_path, json.dumps(state, indent=2) + "\n")

            # Create a plan with a running step
            (team / "progress").mkdir(parents=True)
            plan_path = team / "progress" / "now.json"
            plan_path.write_text(json.dumps({
                "steps": [{"id": "test-step", "state": "running"}],
                "updated": "2026-10-27T10:00:00+00:00"
            }), encoding="utf-8")
            plan_path.chmod(0o600)

            # Set the mtime of now.json to an old time (35+ minutes ago)
            import os
            from datetime import datetime, timezone
            old_time = datetime(2026, 10, 27, 10, 0, 0, tzinfo=timezone.utc).timestamp()
            os.utime(plan_path, (old_time, old_time))

            from workshop_validator.team_kit import watchdog_check
            now = datetime(2026, 10, 27, 10, 35, 0, tzinfo=timezone.utc)
            result = watchdog_check(main_repo, team, now=now)

            # Should report TAKEOVER for real STALLED status (stale heartbeat), not blocked by untracked file
            self.assertIn("TAKEOVER", result)
            self.assertIn("STALLED", result)


class PublicSafetyCheckMainCleanTests(unittest.TestCase):
    """F9 fix1: check_main_clean fails closed on git errors and missing git binary."""

    def test_check_main_clean_catches_file_not_found_error(self):
        """check_main_clean should catch FileNotFoundError when git binary is missing."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            from unittest import mock
            from workshop_validator import public_safety

            # Mock subprocess.run to raise FileNotFoundError
            with mock.patch("workshop_validator.public_safety.subprocess.run", side_effect=FileNotFoundError("git not found")):
                ok, msg = public_safety.check_main_clean(repo)

            self.assertFalse(ok)
            self.assertIn("git binary not found", msg)

    def test_check_main_clean_fails_on_git_errors(self):
        """check_main_clean should fail on git errors (except 'not a git repository')."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()

            # Initialize a real git repo
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            from unittest import mock
            from workshop_validator import public_safety

            # Mock subprocess.run to raise CalledProcessError after the initial git-dir check
            original_run = subprocess.run
            call_count = [0]

            def mock_run(*args, **kwargs):
                call_count[0] += 1
                # Let the first call (git rev-parse --git-dir) succeed
                if call_count[0] == 1:
                    return original_run(*args, **kwargs)
                # Second call (git worktree list) fails
                raise subprocess.CalledProcessError(1, args[0], stderr="git error")

            with mock.patch("workshop_validator.public_safety.subprocess.run", side_effect=mock_run):
                ok, msg = public_safety.check_main_clean(main_repo)

            self.assertFalse(ok)
            self.assertIn("Could not check main checkout status", msg)

    def test_check_main_clean_with_check_dirty_false_ignores_untracked_files(self):
        """check_main_clean with check_dirty=False should ignore untracked files."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()

            # Initialize repo on main
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Add an untracked file
            (main_repo / "untracked.txt").write_text("untracked content")

            from workshop_validator import public_safety

            # With check_dirty=False, should pass despite untracked file
            ok, msg = public_safety.check_main_clean(main_repo, check_dirty=False)
            self.assertTrue(ok)

            # With check_dirty=True (default), should fail due to untracked file
            ok, msg = public_safety.check_main_clean(main_repo, check_dirty=True)
            self.assertFalse(ok)
            self.assertIn("untracked or modified files", msg)


def _make_repo(path: Path) -> None:
    """Synthetic temp repo on branch main with one commit."""
    path.mkdir(parents=True)
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "config", "user.name", "Test"],
                ["git", "config", "user.email", "test@users.noreply.github.com"]):
        subprocess.run(cmd, cwd=path, check=True, capture_output=True)
    (path / "file.txt").write_text("content")
    subprocess.run(["git", "add", "file.txt"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True, capture_output=True)


class F9Fix2Tests(unittest.TestCase):
    """F9 fix2: fail closed on git errors, no root fallback, chain status before dirty files."""

    def test_check_main_clean_fails_when_rev_parse_errors_with_broken_global_config(self):
        """A rev-parse failure that is not 'not a git repository' must fail, not skip."""
        import os
        from unittest import mock
        from workshop_validator import public_safety
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _make_repo(repo)
            subprocess.run(["git", "checkout", "-q", "--detach"], cwd=repo, check=True, capture_output=True)
            broken = Path(tmp) / "broken.gitconfig"
            broken.write_text("[core\n\tthis is = = not valid\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(broken)}):
                ok, msg = public_safety.check_main_clean(repo)
        self.assertFalse(ok)
        self.assertIn("Could not check main checkout status:", msg)
        self.assertNotIn("skipped", msg)

    def test_check_main_clean_fails_when_rev_parse_exits_128_with_other_text(self):
        from unittest import mock
        from workshop_validator import public_safety
        failed = subprocess.CompletedProcess(["git"], 128, stdout="", stderr="fatal: unsafe repository\n")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("workshop_validator.public_safety.subprocess.run", return_value=failed):
            ok, msg = public_safety.check_main_clean(Path(tmp))
        self.assertFalse(ok)
        self.assertEqual(msg, "Could not check main checkout status: fatal: unsafe repository")

    def test_check_main_clean_skips_only_outside_a_git_repository(self):
        from workshop_validator import public_safety
        with tempfile.TemporaryDirectory() as tmp:
            ok, msg = public_safety.check_main_clean(Path(tmp))
        self.assertTrue(ok)
        self.assertIn("skipped", msg)

    def test_get_main_checkout_returns_none_when_worktree_list_fails(self):
        from unittest import mock
        from workshop_validator import public_safety
        err = subprocess.CalledProcessError(128, ["git"], stderr="fatal: boom")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch("workshop_validator.public_safety.subprocess.run", side_effect=err):
                self.assertIsNone(public_safety.get_main_checkout(root))
            # A real non-git folder also fails, and must not fall back to the root.
            self.assertIsNone(public_safety.get_main_checkout(root))

    def test_get_main_checkout_returns_first_worktree_in_a_real_repo(self):
        from workshop_validator import public_safety
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            _make_repo(repo)
            self.assertEqual(public_safety.get_main_checkout(repo).resolve(), repo.resolve())

    def _stop(self, tmp: Path, state: str):
        from workshop_validator.team_hooks import stop_decision
        repo = tmp / "main"
        _make_repo(repo)
        (repo / "stray.txt").write_text("stray")
        team = tmp / "team"
        team.mkdir()
        (team / "producer-live").write_text(json.dumps({"session_id": "s"}), encoding="utf-8")
        (team / "producer-live").chmod(0o600)
        return stop_decision({"session_id": "s"}, team=team, repo=repo,
                             status_fn=lambda r, t: (state, "step x is ready"))

    def test_stop_reports_idle_chain_even_with_stray_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            reason = self._stop(Path(tmp), "IDLE")
        self.assertIsNotNone(reason)
        self.assertIn("Chain is IDLE", reason)

    def test_stop_reports_stalled_chain_even_with_stray_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            reason = self._stop(Path(tmp), "STALLED")
        self.assertIn("Chain is STALLED", reason)

    def test_stop_still_blocks_on_stray_file_when_chain_working(self):
        with tempfile.TemporaryDirectory() as tmp:
            reason = self._stop(Path(tmp), "WORKING")
        self.assertIsNotNone(reason)
        self.assertIn("untracked or modified files", reason)


if __name__ == "__main__":
    unittest.main()
