"""Tests for F22: worktree-cleanup counts only real removals and reports stale locks."""

import contextlib
import io
import os
import subprocess
import tempfile
import unicodedata
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


MERGED_OID = "a" * 40
UNPROVEN_OID = "b" * 40


def _scenario(repo, specs):
    """Fake git for many worktrees; specs are (name, lock line or None, merged, remove stderr or None)."""
    blocks = [f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n"]
    per_tree = []
    for i, (name, lock, merged, remove_stderr) in enumerate(specs):
        path, branch = repo / name, f"automation/2026-01-01-w{i}"
        oid = MERGED_OID if merged else UNPROVEN_OID
        block = f"worktree {path}\nHEAD {oid}\nbranch refs/heads/{branch}\n"
        blocks.append(block + (f"{lock}\n" if lock is not None else ""))
        per_tree.append((lambda c, b=branch: c == ["git", "rev-parse", b], 0, oid + "\n", ""))
        if remove_stderr is not None:
            per_tree.append((lambda c, p=str(path): c == ["git", "worktree", "remove", p],
                             128, "", remove_stderr))
    porcelain = "\n".join(blocks)
    rules = [
        (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
        (lambda c: c[:2] == ["gh", "pr"] and "--limit" in c, 0, f'[{{"headRefOid": "{MERGED_OID}"}}]', ""),
        (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
    ] + per_tree
    return lambda: FakeRunner(rules)


def _fake_kill(pid, sig):
    """pid 4001 is not running; every other pid is running."""
    if pid == 4001:
        raise ProcessLookupError
    return None


def _run(repo, runner, dry_run, kill=_fake_kill):
    out = io.StringIO()
    with mock.patch.object(team_gates.os, "kill", side_effect=kill), contextlib.redirect_stdout(out):
        team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=dry_run)
    return out.getvalue(), runner


def _dry_decisions(output, names):
    """Map each name to the reason on its dry-run keep line, or 'remove' for a remove line."""
    decisions = {}
    for line in output.splitlines():
        for name in names:
            if line.startswith(f"  keep {name}: "):
                decisions[name] = line.split(": ", 1)[1]
            elif line.startswith(f"  remove {name}: "):
                decisions[name] = "remove"
    return decisions


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
            # os.kill is mocked, so the host's live pids do not matter
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
            with mock.patch.object(team_gates.os, "kill", side_effect=ProcessLookupError) as kill, \
                    contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)
            kill.assert_called_once_with(dead_pid, 0)

            output = out.getvalue()
            self.assertIn("removed 0", output)
            self.assertIn("kept 1", output)
            self.assertIn("stale-lock", output)
            self.assertIn(f"pid {dead_pid} not running", output)
            self.assertNotIn("user@example.com", output)


class DryRunConsistencyTests(unittest.TestCase):
    """F22: dry-run reports the same decisions as real run."""

    def test_dry_run_matches_real_decisions(self):
        """Dry and real runs make the same keep or remove decision for every worktree."""
        specs = [
            ("wt-dead", "locked agent pid 4001", True, None),
            ("wt-live", "locked agent pid 4002", True, None),
            ("wt-bare", "locked", True, None),
            ("wt-ok", None, True, None),
            ("wt-fail", None, True, "fatal: cannot remove\n"),
            ("wt-noproof", None, False, None),
        ]
        names = [spec[0] for spec in specs]
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            make_runner = _scenario(repo, specs)
            dry_out, dry_runner = _run(repo, make_runner(), dry_run=True)
            real_out, real_runner = _run(repo, make_runner(), dry_run=False)

        dry = _dry_decisions(dry_out, names)
        self.assertEqual(dry, {"wt-dead": "stale-lock", "wt-live": "locked", "wt-bare": "locked",
                               "wt-ok": "remove", "wt-fail": "remove", "wt-noproof": "no-proof"})
        self.assertIn("  remove wt-ok: pr-exact", dry_out)
        self.assertEqual(dry_runner.find("git", "worktree", "remove"), [])
        self.assertEqual(dry_runner.find("git", "branch", "-D"), [])

        attempted = {Path(c[3]).name for c, _ in real_runner.find("git", "worktree", "remove")}
        for name in names:
            with self.subTest(worktree=name):
                self.assertEqual(dry[name] == "remove", name in attempted)
                self.assertEqual(dry[name] == "stale-lock",
                                 f"  stale-lock {name} (pid 4001 not running)" in real_out)
        self.assertEqual([c for c, _ in real_runner.find("git", "branch", "-D")],
                         [["git", "branch", "-D", "automation/2026-01-01-w3"]])
        self.assertIn("would remove 2, kept 4 (2 locked, 1 no-proof, 1 stale-lock)", dry_out)
        self.assertIn("removed 1, kept 5 (2 locked, 1 no-proof, 1 remove-failed, 1 stale-lock)",
                      real_out)

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
            with mock.patch.object(team_gates.os, "kill", side_effect=ProcessLookupError) as kill, \
                    contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)
            kill.assert_called_once_with(dead_pid, 0)

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
            with mock.patch.object(team_gates.os, "kill", side_effect=None) as kill, \
                    contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)
            kill.assert_called_once_with(live_pid, 0)

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
            with mock.patch.object(team_gates.os, "kill", side_effect=ProcessLookupError) as kill, \
                    contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)
            kill.assert_called_once_with(23640, 0)

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
            with mock.patch.object(team_gates.os, "kill", side_effect=OverflowError) as kill, \
                    contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertNotIn("stale-lock", output)
            self.assertIn("kept 1", output)
            self.assertIn("locked", output)

    def test_pid_over_int_digit_limit_kept_locked(self):
        """A pid of 5,000 digits (over Python's int limit) is kept locked, dry and real."""
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp).resolve()
                wt = repo / "agent-wt"
                wt.mkdir(parents=True)
                branch_oid = "a" * 40
                long_pid = "7" * 5000

                porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                             f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                             f"locked pid {long_pid}\n")

                runner = FakeRunner([
                    (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                    (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                    (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                    (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                    (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
                ])

                out = io.StringIO()
                with mock.patch.object(team_gates.os, "kill", side_effect=ProcessLookupError) as kill, \
                        contextlib.redirect_stdout(out):
                    team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=dry_run)

                output = out.getvalue()
                kill.assert_not_called()
                self.assertNotIn("stale-lock", output)
                self.assertIn("kept 1 (1 locked)", output)
                self.assertEqual(runner.find("git", "worktree", "remove"), [])
                self.assertTrue(wt.is_dir())

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
            fake_token = "fake-token-" + "0" * 10

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/2026-01-01-test\n"
                         f"locked by user@example.com with token {fake_token} pid {dead_pid}\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, "[]", ""),
                (lambda c: c[:2] == ["git", "status"], 0, "", ""),
                (lambda c: c[:2] == ["git", "rev-parse"], 0, branch_oid + "\n", ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, "", ""),
            ])

            out = io.StringIO()
            with mock.patch.object(team_gates.os, "kill", side_effect=ProcessLookupError) as kill, \
                    contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=False)

            output = out.getvalue()
            self.assertIn("stale-lock", output)
            self.assertIn(f"pid {dead_pid} not running", output)
            self.assertNotIn("user@example.com", output)
            self.assertNotIn(fake_token, output)

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


def _control_chars(text):
    """Every C0, C1 or DEL character in text except the newline between lines."""
    return [ch for ch in text if unicodedata.category(ch) == "Cc" and ch != "\n"]


class R3GuardTests(unittest.TestCase):
    """F22 r3: printed names are sanitised, odd liveness answers stay locked, stderr is redacted."""

    def _one(self, lock=None, merged=True, remove_stderr=None, dry_run=False, kill=_fake_kill):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            runner = _scenario(repo, [("agent-wt", lock, merged, remove_stderr)])()
            return _run(repo, runner, dry_run=dry_run, kill=kill)

    def test_printed_names_drop_email_and_escape_in_both_modes(self):
        """Keep, stale-lock, remove-failed and would-remove lines never print an email or control character."""
        def name(i):
            return f"agent \x1b]0;pwn\x07 user{i}@example.com\tw{i}"
        specs = [
            (name(0), "locked agent pid 4001", True, None),
            (name(1), "locked", True, None),
            (name(2), None, True, None),
            (name(3), None, True, "fatal: x\n"),
            (name(4), None, False, None),
        ]
        shown = "agent ]0;pwn <email> w"
        for dry_run, expected in (
                (True, [f"  stale-lock {shown}0 (pid 4001 not running)", f"  keep {shown}0: stale-lock",
                        f"  keep {shown}1: locked", f"  remove {shown}2: pr-exact",
                        f"  remove {shown}3: pr-exact", f"  keep {shown}4: no-proof"]),
                (False, [f"  stale-lock {shown}0 (pid 4001 not running)",
                         f"  remove-failed {shown}3: fatal: x"])):
            with self.subTest(dry_run=dry_run), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp).resolve()
                output, _ = _run(repo, _scenario(repo, specs)(), dry_run=dry_run)
                self.assertNotIn("example.com", output)
                self.assertNotIn("@", output)
                self.assertEqual(_control_chars(output), [])
                for line in expected:
                    self.assertIn(line + "\n", output)

    def test_dead_pid_over_cap_is_locked(self):
        """A dead pid just over 4194304 is not a real pid: kept as locked, never stale-lock."""
        output, _ = self._one(lock="locked agent pid 5000000", kill=ProcessLookupError)
        self.assertNotIn("stale-lock", output)
        self.assertIn("kept 1 (1 locked)", output)

    def test_other_oserror_is_unknown_and_locked(self):
        """os.kill raising EINVAL means unknown, and unknown is locked, never stale-lock."""
        output, _ = self._one(lock="locked agent pid 4001", kill=OSError(22, "Invalid argument"))
        self.assertNotIn("stale-lock", output)
        self.assertIn("kept 1 (1 locked)", output)

    def test_remove_stderr_email_and_token_not_printed(self):
        """git worktree remove stderr with an email and a token is printed redacted."""
        fake_token = "fake-token-" + "0" * 40
        stderr = f"fatal: cannot remove for agent@example.com token {fake_token}\n"
        output, _ = self._one(remove_stderr=stderr)
        self.assertIn("  remove-failed agent-wt: fatal: cannot remove for <email> token <redacted>\n", output)
        self.assertNotIn("example.com", output)
        self.assertNotIn(fake_token, output)

    def test_dry_run_prints_keep_line_for_locked(self):
        """A dry run prints 'keep <name>: locked' for a locked worktree."""
        output, runner = self._one(lock="locked", dry_run=True)
        self.assertIn("  keep agent-wt: locked\n", output)
        self.assertEqual(runner.find("git", "worktree", "remove"), [])

    def test_remove_failed_prints_first_non_empty_stderr_line(self):
        """Stderr that starts with blank lines prints its first non-empty line."""
        output, _ = self._one(remove_stderr="\n   \nfatal: real reason\nsecond line\n")
        self.assertIn("  remove-failed agent-wt: fatal: real reason\n", output)
        self.assertNotIn("unknown error", output)
