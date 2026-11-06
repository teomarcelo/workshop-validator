"""Tests for F22: worktree-cleanup counts only real removals and reports stale locks."""

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workshop_validator import team_gates


class FakeRunner:
    """Records (cmd, cwd); answers from a list of (predicate, returncode, stdout, stderr)."""

    def __init__(self, rules=()):
        self.calls, self.rules = [], list(rules)

    def __call__(self, cmd, cwd):
        self.calls.append((list(cmd), Path(cwd)))
        for match, code, out, err in self.rules:
            if match(cmd):
                return subprocess.CompletedProcess(cmd, code, out, err)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def find(self, *prefix):
        return [(c, cwd) for c, cwd in self.calls if c[:len(prefix)] == list(prefix)]


class RemoveFailureTests(unittest.TestCase):
    """F22: worktree remove failures are counted as kept, not removed."""

    def test_remove_failed_not_counted_as_removed(self):
        """When git worktree remove fails, count as kept with remove-failed reason."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                # All merged PRs - exact match
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{branch_oid}"}}]', ""),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, "", ""),  # Clean
                # This branch has own PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c and "automation/2026-01-01-test" in c, 0, "[]", ""),
                (lambda c: c == ["git", "rev-parse", "automation/2026-01-01-test"], 0, branch_oid + "\n", ""),
                # Worktree remove fails (e.g., locked)
                (lambda c: c[:3] == ["git", "worktree", "remove"], 128, "",
                 "fatal: 'agent-wt' is locked\nUse 'remove -f -f' to override or unlock first"),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            # Should report 0 removed, 1 kept with remove-failed reason
            self.assertIn("removed 0", output)
            self.assertIn("kept 1", output)
            self.assertIn("remove-failed", output)
            self.assertIn("agent-wt", output)
            self.assertIn("is locked", output)

            # Branch delete should not have been called
            branch_deletes = runner.find("git", "branch", "-D")
            self.assertEqual(len(branch_deletes), 0)


class StaleLockTests(unittest.TestCase):
    """F22: locked worktrees with dead PIDs are reported as stale-lock."""

    def test_locked_worktree_with_dead_pid_reports_stale_lock(self):
        """Locked worktree with PID that is not running is reported as stale-lock."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40
            # Use a PID under the cap that is unlikely to be running
            dead_pid = 999999

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked claimed by user@example.com pid {dead_pid}\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                # All merged PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, "", ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]", ""),
                (lambda c: c == ["git", "rev-parse", "automation/2026-01-01-test"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertIn("removed 0", output)
            self.assertIn("kept 1", output)
            self.assertIn("stale-lock", output)
            self.assertIn(f"pid {dead_pid} not running", output)
            self.assertNotIn("user@example.com", output)


class DryRunConsistencyTests(unittest.TestCase):
    """F22: dry-run reports the same decisions as real run."""

    def test_dry_run_matches_real_decisions(self):
        """Dry run reports same decisions without removing."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                # All merged PRs - exact match
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{branch_oid}"}}]', ""),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, "", ""),  # Clean
                # This branch has own PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c and "automation/2026-01-01-test" in c, 0, "[]", ""),
                (lambda c: c == ["git", "rev-parse", "automation/2026-01-01-test"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            # Dry run should report would remove
            self.assertIn("would remove 1", output)
            self.assertIn("remove agent-wt: pr-exact", output)

            # No actual git remove or branch delete should have been called
            removes = runner.find("git", "worktree", "remove")
            self.assertEqual(len(removes), 0)
            branch_deletes = runner.find("git", "branch", "-D")
            self.assertEqual(len(branch_deletes), 0)

    def test_dry_run_reports_stale_lock_same_as_real_run(self):
        """Dry run reports stale-lock same as real run."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40
            dead_pid = 999999

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked pid {dead_pid}\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            self.assertIn("stale-lock", output)
            self.assertIn(f"pid {dead_pid} not running", output)
            self.assertIn("kept 1", output)

    def test_dry_run_locked_worktree_not_reported_as_would_remove(self):
        """Dry run does not report locked worktree as would remove."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--limit" in c, 0, f'[{{"headRefOid": "{branch_oid}"}}]', ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            self.assertIn("would remove 0", output)
            self.assertIn("kept 1", output)
            self.assertIn("locked", output)
            self.assertNotIn("remove agent-wt", output)


class PidParsingTests(unittest.TestCase):
    """F22: pid is extracted only from 'pid N' pattern, with guards for edge cases."""

    def test_live_pid_kept_not_stale(self):
        """Worktree locked with live PID is kept as locked, not stale-lock."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40
            live_pid = os.getpid()

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked pid {live_pid}\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertNotIn("stale-lock", output)
            self.assertIn("kept 1", output)
            self.assertIn("locked", output)

    def test_first_number_not_taken_as_pid_when_no_pid_prefix(self):
        """Date or other first number in lock reason is not taken as PID."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked claimed 2026-11-05 by agent pid 23640\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            # Should report stale-lock with correct pid 23640, not 2026
            self.assertIn("stale-lock", output)
            self.assertIn("pid 23640 not running", output)
            self.assertNotIn("pid 2026", output)

    def test_permission_error_counts_as_live(self):
        """PermissionError when checking PID means process is running."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked pid 1\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            with mock.patch("os.kill") as mock_kill:
                mock_kill.side_effect = PermissionError("Operation not permitted")

                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

                output = out.getvalue()
                self.assertNotIn("stale-lock", output)
                self.assertIn("kept 1", output)
                self.assertIn("locked", output)

    def test_huge_pid_no_crash(self):
        """Huge PID that causes OverflowError does not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40
            huge_pid = "99999999999999999999"

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked pid {huge_pid}\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertNotIn("stale-lock", output)
            self.assertIn("kept 1", output)
            self.assertIn("locked", output)

    def test_bare_locked_line_no_crash(self):
        """Bare 'locked' line with no reason does not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--limit" in c, 0, f'[{{"headRefOid": "{branch_oid}"}}]', ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertIn("kept 1", output)
            self.assertIn("locked", output)


class RedactionTests(unittest.TestCase):
    """F22: lock reasons containing sensitive data are not printed."""

    def test_lock_reason_with_email_and_token_redacted(self):
        """Lock reason containing email and token-like string is not printed."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40
            dead_pid = 999999

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked by user@example.com with token xoxb-1234567890abcdef1234567890abcdef pid {dead_pid}\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertIn("stale-lock", output)
            self.assertIn(f"pid {dead_pid} not running", output)
            self.assertNotIn("user@example.com", output)
            self.assertNotIn("xoxb-1234567890abcdef1234567890abcdef", output)

    def test_empty_stderr_after_redaction_no_crash(self):
        """Stderr that becomes empty after redaction does not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--limit" in c, 0, f'[{{"headRefOid": "{branch_oid}"}}]', ""),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, "", ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]", ""),
                (lambda c: c == ["git", "rev-parse", "automation/2026-01-01-test"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "remove"], 128, "", "\n"),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertIn("remove-failed", output)
            self.assertIn("unknown error", output)

    def test_control_char_only_stderr_no_crash(self):
        """Stderr with only control chars does not crash."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--limit" in c, 0, f'[{{"headRefOid": "{branch_oid}"}}]', ""),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, "", ""),
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]", ""),
                (lambda c: c == ["git", "rev-parse", "automation/2026-01-01-test"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "remove"], 128, "", "\x1b"),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertIn("remove-failed", output)
            self.assertIn("unknown error", output)
