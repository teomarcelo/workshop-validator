"""Tests for F13-fix: merge-when-green prints redacted stderr when gh pr merge fails."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workshop_validator import team_gates, team_kit


OID = "a" * 40
GOOD_PR = {"headRefName": "automation/2026-01-01-x", "headRefOid": OID, "isCrossRepository": False,
           "files": [{"path": "docs/a.md"}], "state": "OPEN", "baseRefName": "main"}


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


class StderrRedactionTests(unittest.TestCase):
    """F13-fix: stderr with email and token is printed redacted; empty stderr still prints FAIL."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_stderr_with_email_and_token_is_redacted(self):
        """When gh pr merge fails with stderr containing email and token, print redacted."""
        noreply_email = "teomarcelo@users.noreply.github.com"
        # Build fake token at runtime with neutral characters (no real provider prefix)
        fake_token = "tok" + "0" * 41  # 44 chars total, no ghp_/gho_/sk-/etc. prefix
        # Simulate a real gh error with email and token
        stderr_with_secrets = (
            "GraphQL: Pull request is not mergeable (mergePullRequest)\n"
            "Contact support@github.com for help\n"
            f"Token: {fake_token}\n"
            "User: user@example.com tried to merge\n"
        )

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n", ""),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c) and "files" in " ".join(c), 0,
             json.dumps(GOOD_PR), ""),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}]), ""),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n", ""),
            # gh pr merge fails with stderr
            (lambda c: c[:3] == ["gh", "pr", "merge"], 1, "", stderr_with_secrets),
            # After failed merge: pr view to check state
            (lambda c: c[:3] == ["gh", "pr", "view"] and "state" in " ".join(c), 0,
             json.dumps({"state": "OPEN", "headRefOid": OID}), ""),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should fail
        self.assertEqual(code, 1)
        output = out.getvalue()

        # Should contain the FAIL line with exit code
        self.assertIn("FAIL gh pr merge exited 1", output)

        # Should contain redacted email addresses
        self.assertIn("<email>", output)
        # Should NOT contain actual email addresses
        self.assertNotIn("support@github.com", output)
        self.assertNotIn("user@example.com", output)

        # Should contain redacted token
        self.assertIn("<redacted>", output)
        # Should NOT contain actual token
        self.assertNotIn(fake_token, output)

    def test_stderr_first_five_lines_only(self):
        """When stderr has many lines, only first 5 are printed."""
        noreply_email = "teomarcelo@users.noreply.github.com"
        # 10 lines of stderr
        stderr_many_lines = "\n".join([f"Error line {i}" for i in range(1, 11)])

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n", ""),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "files" in " ".join(c), 0, json.dumps(GOOD_PR), ""),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}]), ""),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n", ""),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 1, "", stderr_many_lines),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "state" in " ".join(c), 0,
             json.dumps({"state": "OPEN", "headRefOid": OID}), ""),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        self.assertEqual(code, 1)
        output = out.getvalue()

        # Should contain FAIL line
        self.assertIn("FAIL gh pr merge exited 1", output)

        # Should contain first 5 lines
        for i in range(1, 6):
            self.assertIn(f"Error line {i}", output)

        # Should NOT contain lines 6-10
        for i in range(6, 11):
            self.assertNotIn(f"Error line {i}", output)

    def test_empty_stderr_still_prints_fail_line(self):
        """When gh pr merge fails with empty stderr, still print FAIL line."""
        noreply_email = "teomarcelo@users.noreply.github.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n", ""),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "files" in " ".join(c), 0, json.dumps(GOOD_PR), ""),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}]), ""),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n", ""),
            # gh pr merge fails with empty stderr
            (lambda c: c[:3] == ["gh", "pr", "merge"], 1, "", ""),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "state" in " ".join(c), 0,
             json.dumps({"state": "OPEN", "headRefOid": OID}), ""),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        self.assertEqual(code, 1)
        output = out.getvalue()

        # Should contain FAIL line
        self.assertIn("FAIL gh pr merge exited 1", output)
        # Should not crash or print extra lines when stderr is empty

    def test_token_like_strings_are_redacted(self):
        """40+ char strings of [A-Za-z0-9_-] are redacted as tokens."""
        noreply_email = "teomarcelo@users.noreply.github.com"
        # Various token-like strings
        stderr_with_tokens = (
            "Token: " + "a" * 40 + "\n"
            "API key: " + "B" * 50 + "\n"
            "Short: " + "x" * 39 + "\n"  # Should NOT be redacted (too short)
            "Mixed: abc_DEF-123_xyz-" + "0" * 20 + "\n"  # 40+ chars total
        )

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n", ""),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "files" in " ".join(c), 0, json.dumps(GOOD_PR), ""),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}]), ""),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n", ""),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 1, "", stderr_with_tokens),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "state" in " ".join(c), 0,
             json.dumps({"state": "OPEN", "headRefOid": OID}), ""),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        self.assertEqual(code, 1)
        output = out.getvalue()

        # Should redact 40+ char tokens
        self.assertNotIn("a" * 40, output)
        self.assertNotIn("B" * 50, output)
        self.assertIn("<redacted>", output)

        # Should NOT redact short strings (39 chars)
        self.assertIn("x" * 39, output)


class RedactStderrUnitTests(unittest.TestCase):
    """Unit tests for _redact_stderr function."""

    def test_redacts_email_addresses(self):
        """Email addresses are replaced with <email>."""
        stderr = "Error: contact support@github.com or admin@example.org"
        result = team_gates._redact_stderr(stderr)
        self.assertIn("<email>", result)
        self.assertNotIn("support@github.com", result)
        self.assertNotIn("admin@example.org", result)

    def test_redacts_tokens(self):
        """40+ char alphanumeric strings are replaced with <redacted>."""
        token = "a" * 40
        stderr = f"Token: {token}"
        result = team_gates._redact_stderr(stderr)
        self.assertIn("<redacted>", result)
        self.assertNotIn(token, result)

    def test_preserves_short_strings(self):
        """Strings shorter than 40 chars are not redacted."""
        short = "x" * 39
        stderr = f"Short: {short}"
        result = team_gates._redact_stderr(stderr)
        self.assertIn(short, result)

    def test_returns_first_five_lines(self):
        """Only first 5 lines are returned."""
        stderr = "\n".join([f"Line {i}" for i in range(1, 11)])
        result = team_gates._redact_stderr(stderr)
        lines = result.splitlines()
        self.assertEqual(len(lines), 5)
        for i in range(1, 6):
            self.assertIn(f"Line {i}", result)

    def test_empty_stderr_returns_empty_string(self):
        """Empty stderr returns empty string."""
        result = team_gates._redact_stderr("")
        self.assertEqual(result, "")

    def test_whitespace_only_stderr(self):
        """Whitespace-only stderr is preserved (counted as empty lines)."""
        stderr = "\n\n\n"
        result = team_gates._redact_stderr(stderr)
        # Should have 3 empty lines (or up to 5 if more newlines)
        self.assertEqual(result, "\n\n")  # 3 lines = 2 newlines


if __name__ == "__main__":
    unittest.main()
