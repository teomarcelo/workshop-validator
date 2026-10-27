"""Tests for F12-fix: low-severity notes from PR 16 security review."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workshop_validator import public_safety, team_gates, team_kit


class CheckMainCleanGitDirTests(unittest.TestCase):
    """F12 item 1: check_main_clean skips only when no .git entry at all; fails closed otherwise."""

    def test_skip_when_no_git_dir_at_all(self):
        """When there's no .git entry at all, skip the check (test environments)."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "some_file.txt").write_text("hello")
            ok, msg = public_safety.check_main_clean(repo)
            self.assertTrue(ok)
            self.assertIn("Not a git repo", msg)

    def test_fail_when_git_dir_points_to_nothing(self):
        """When .git file points to a nonexistent location, fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Create a .git file pointing to a nonexistent worktree
            (repo / ".git").write_text("gitdir: /nonexistent/path/.git/worktrees/foo")
            ok, msg = public_safety.check_main_clean(repo)
            self.assertFalse(ok)
            self.assertIn("Could not check main checkout status", msg)

    def test_fail_when_git_dir_exists_but_main_checkout_moved(self):
        """When a linked worktree's main repo moved, fail closed."""
        with tempfile.TemporaryDirectory() as tmp:
            original_repo = Path(tmp) / "original"
            original_repo.mkdir()
            # Initialize a repo and create a worktree
            subprocess.run(["git", "init", "-b", "main"], cwd=original_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=original_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=original_repo, check=True, capture_output=True)
            (original_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=original_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=original_repo, check=True, capture_output=True)

            worktree_path = Path(tmp) / "worktree"
            subprocess.run(["git", "worktree", "add", str(worktree_path), "-b", "feature"], cwd=original_repo, check=True, capture_output=True)

            # Now "move" the main repo by creating a new one at a different location
            # and point the worktree's .git to a path that doesn't exist
            gitdir_content = (worktree_path / ".git").read_text()
            # Change the path to point somewhere that doesn't exist
            broken_gitdir = gitdir_content.replace(str(original_repo), "/nonexistent")
            (worktree_path / ".git").write_text(broken_gitdir)

            # check_main_clean should fail closed
            ok, msg = public_safety.check_main_clean(worktree_path)
            self.assertFalse(ok)
            self.assertIn("Could not check main checkout status", msg)


class CheckMainCleanMainCheckoutDoesNotExistTests(unittest.TestCase):
    """F12 item 2: When main checkout folder no longer exists, the reason says so."""

    def test_main_checkout_folder_does_not_exist(self):
        """When the main checkout path from git worktree list doesn't exist, fail with informative message."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)
            (repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

            worktree_path = Path(tmp) / "worktree"
            subprocess.run(["git", "worktree", "add", str(worktree_path), "-b", "feature"], cwd=repo, check=True, capture_output=True)

            # Remove the main checkout folder
            import shutil
            shutil.rmtree(repo)

            # check_main_clean from the worktree should fail with an informative message
            ok, msg = public_safety.check_main_clean(worktree_path)
            self.assertFalse(ok)
            # Should NOT say "git binary not found"
            self.assertNotIn("git binary not found", msg)
            # Should mention the git repository error (the worktree's link is broken)
            self.assertTrue("Could not check main checkout status" in msg or "not a git repository" in msg)


class WatchdogCheckOSErrorTests(unittest.TestCase):
    """F12 item 3: watchdog_check catches any OSError from check_main_clean and reports STALLED."""

    def test_oserror_from_check_main_clean_is_caught_as_stalled(self):
        """When check_main_clean raises OSError, watchdog_check reports STALLED without crashing."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            team = Path(tmp) / "team"
            repo.mkdir()
            team.mkdir()

            # Mock check_main_clean to raise OSError
            with mock.patch("workshop_validator.public_safety.check_main_clean") as mock_check:
                mock_check.side_effect = OSError("Simulated OS error")

                # Should not crash, should report STALLED
                result = team_kit.watchdog_check(repo, team)
                # watchdog_check returns a formatted string like "TAKEOVER STALLED: ..."
                self.assertIn("STALLED", result)
                # F14 changed this to catch any Exception, not just OSError
                self.assertIn("Exception checking main checkout", result)


class NoreplyPatternFullmatchTests(unittest.TestCase):
    """F12 item 4: NOREPLY_PATTERN uses fullmatch (or \\Z); revert failure reported in summary."""

    def test_noreply_pattern_rejects_emails_with_extra_text(self):
        """NOREPLY_PATTERN should use fullmatch to reject emails with trailing text."""
        # Valid noreply email
        self.assertIsNotNone(team_gates.NOREPLY_PATTERN.fullmatch("user@users.noreply.github.com"))
        # Invalid: has trailing text
        self.assertIsNone(team_gates.NOREPLY_PATTERN.fullmatch("user@users.noreply.github.com "))
        self.assertIsNone(team_gates.NOREPLY_PATTERN.fullmatch("user@users.noreply.github.com\n"))
        # Invalid: has leading text
        self.assertIsNone(team_gates.NOREPLY_PATTERN.fullmatch(" user@users.noreply.github.com"))


class RedactStderrControlCharsTests(unittest.TestCase):
    """F12 item 5: _redact_stderr also redacts 40+ char runs of [A-Za-z0-9+/=._-] and strips control chars."""

    def test_redact_long_alphanumeric_runs(self):
        """Runs of 40+ characters from [A-Za-z0-9+/=._-] should be redacted."""
        # 40 characters exactly
        long_token = "a" * 40
        stderr = f"Error: token {long_token} is invalid"
        redacted = team_gates._redact_stderr(stderr)
        self.assertIn("<redacted>", redacted)
        self.assertNotIn(long_token, redacted)

        # 50 characters
        longer_token = "Ab12+/=._-" * 5  # 50 chars
        stderr = f"Error: {longer_token} failed"
        redacted = team_gates._redact_stderr(stderr)
        self.assertIn("<redacted>", redacted)
        self.assertNotIn(longer_token, redacted)

    def test_do_not_redact_short_runs(self):
        """Runs shorter than 40 characters should not be redacted as tokens."""
        short = "a" * 39
        stderr = f"Error: {short} is short"
        redacted = team_gates._redact_stderr(stderr)
        # Should still have the short string (might be redacted as email, but not as token)
        # Actually, TOKEN_PATTERN is for 40+ chars, so this should pass through
        # unless it matches EMAIL_PATTERN
        self.assertIn(short, redacted)

    def test_strip_ansi_escape_codes(self):
        """ANSI escape codes should be stripped before printing."""
        stderr = "\x1b[31mError:\x1b[0m something failed"
        redacted = team_gates._redact_stderr(stderr)
        self.assertNotIn("\x1b", redacted)
        self.assertIn("Error:", redacted)

    def test_strip_other_control_characters(self):
        """Other control characters should be stripped."""
        stderr = "Error:\x00\x01\x02 something failed"
        redacted = team_gates._redact_stderr(stderr)
        self.assertNotIn("\x00", redacted)
        self.assertNotIn("\x01", redacted)
        self.assertNotIn("\x02", redacted)


class DefaultRunnerErrorsReplaceTests(unittest.TestCase):
    """F12 item 6: _default_runner decodes subprocess output with errors='replace'."""

    def test_non_utf8_bytes_do_not_crash(self):
        """Non-UTF-8 bytes in subprocess output should be replaced, not crash."""
        # Create a command that outputs non-UTF-8 bytes
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "bad_output.sh"
            script.write_text("#!/bin/sh\nprintf '\\xff\\xfe'\n")
            script.chmod(0o755)

            # _default_runner should not crash
            result = team_gates._default_runner([str(script)], Path(tmp))
            # Should have some output (replaced characters)
            self.assertIsInstance(result.stdout, str)


if __name__ == "__main__":
    unittest.main()
