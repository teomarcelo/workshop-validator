"""Tests for F11-fix security findings (each fails on d759049)."""

import contextlib
import io
import json
import re
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
    """Records (cmd, cwd); answers from a list of (predicate, returncode, stdout)."""

    def __init__(self, rules=()):
        self.calls, self.rules = [], list(rules)

    def __call__(self, cmd, cwd):
        self.calls.append((list(cmd), Path(cwd)))
        for match, code, out in self.rules:
            if match(cmd):
                return subprocess.CompletedProcess(cmd, code, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def find(self, *prefix):
        return [(c, cwd) for c, cwd in self.calls if c[:len(prefix)] == list(prefix)]


# Finding 1: Post-merge author read must not go through _gh_json
class PlainTextAuthorReadTests(unittest.TestCase):
    """F11-fix finding 1: gh api returns plain text, not JSON."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_author_read_uses_plain_text_output(self):
        """gh api with --jq returns plain text, not JSON - test expects plain text."""
        merge_oid = "b" * 40  # Valid hex
        noreply_email = "teomarcelo@users.noreply.github.com"
        # Real gh outputs plain text without quotes
        plain_text_email = noreply_email

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c) and "files" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            # After merge: pr view to get merge commit (without state field, happens in else branch)
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in " ".join(c) and "state" not in " ".join(c), 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}, "headRefOid": OID})),
            # Plain text output (no JSON wrapping)
            (lambda c: c[:2] == ["gh", "api"] and "commits" in " ".join(c), 0, plain_text_email + "\n"),
            (lambda c: c[:3] == ["gh", "run", "list"], 0, json.dumps([{"headSha": merge_oid, "status": "completed", "conclusion": "success"}])),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should succeed with plain text email
        self.assertEqual(code, 0)
        api_calls = runner.find("gh", "api")
        self.assertTrue(len(api_calls) >= 1, "Should call gh api for commit author")


# Finding 2: Non-noreply author still waits for CI and reverts if red
class NonNoreplyAuthorFlowTests(unittest.TestCase):
    """F11-fix finding 2: non-noreply author records FAIL, waits for CI, reverts if red."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_non_noreply_author_waits_for_ci_and_reverts_if_red(self):
        """Non-noreply merge author: FAIL, but still wait for CI and revert if red."""
        merge_oid = "b" * 40  # Valid hex
        noreply_email = "teomarcelo@users.noreply.github.com"
        bad_email = "bad@example.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c) and "files" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            # After merge: pr view to get merge commit
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in " ".join(c) and "state" not in " ".join(c), 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}, "headRefOid": OID})),
            # Bad email (not noreply)
            (lambda c: c[:2] == ["gh", "api"] and "commits" in " ".join(c), 0, bad_email + "\n"),
            # CI fails
            (lambda c: c[:3] == ["gh", "run", "list"], 0, json.dumps([{"headSha": merge_oid, "status": "completed", "conclusion": "failure"}])),
            # Revert operations
            (lambda c: c[:2] == ["git", "fetch"] and "main" in c, 0, ""),
            (lambda c: c[:3] == ["git", "worktree", "add"], 0, ""),
            (lambda c: c[:3] == ["git", "checkout", "-b"], 0, ""),
            (lambda c: c == ["git", "revert", "--no-edit", merge_oid], 0, ""),
            (lambda c: c[:3] == ["git", "push", "-u"], 0, ""),
            (lambda c: c[:3] == ["gh", "pr", "create"], 0, "https://github.com/test/test/pull/99\n"),
            (lambda c: c[:3] == ["git", "worktree", "remove"], 0, ""),
            (lambda c: c[:3] == ["git", "branch", "-D"], 0, ""),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should exit non-zero after revert
        self.assertEqual(code, 1)
        output = out.getvalue()
        # Should mention non-noreply author problem
        self.assertIn("FAIL", output)
        # Should still revert when CI is red
        self.assertIn("REVERTING", output)
        # Should have called revert
        revert_calls = runner.find("git", "revert")
        self.assertTrue(len(revert_calls) >= 1, "Should call git revert")


# Finding 3: Fail closed - unreadable SHA or author is FAIL
class FailClosedTests(unittest.TestCase):
    """F11-fix finding 3: fail closed when SHA or author cannot be read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_unreadable_author_is_fail(self):
        """If author cannot be read (gh error), that's FAIL."""
        merge_oid = "b" * 40  # Valid hex
        noreply_email = "teomarcelo@users.noreply.github.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c) and "files" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            # After merge: pr view to get merge commit
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in " ".join(c) and "state" not in " ".join(c), 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}, "headRefOid": OID})),
            # gh api fails with non-zero exit
            (lambda c: c[:2] == ["gh", "api"] and "commits" in " ".join(c), 1, ""),
            # CI is green (shouldn't get here if we fail closed properly)
            (lambda c: c[:3] == ["gh", "run", "list"], 0, json.dumps([{"headSha": merge_oid, "status": "completed", "conclusion": "success"}])),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should fail
        self.assertEqual(code, 1)
        output = out.getvalue()
        self.assertIn("FAIL", output)

    def test_empty_sha_is_fail(self):
        """If merge SHA is empty, that's FAIL."""
        noreply_email = "teomarcelo@users.noreply.github.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            # No mergeCommit oid
            (lambda c: c[:3] == ["gh", "pr", "view"] and "state" in " ".join(c), 0,
             json.dumps({"state": "MERGED", "headRefOid": OID})),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should fail
        self.assertEqual(code, 1)
        output = out.getvalue()
        self.assertIn("FAIL", output)

    def test_invalid_sha_is_fail(self):
        """If merge SHA is invalid (not 40 hex chars), that's FAIL."""
        noreply_email = "teomarcelo@users.noreply.github.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            # Invalid SHA
            (lambda c: c[:3] == ["gh", "pr", "view"] and "state" in " ".join(c), 0,
             json.dumps({"mergeCommit": {"oid": "not-a-valid-sha"}, "state": "MERGED", "headRefOid": OID})),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should fail
        self.assertEqual(code, 1)
        output = out.getvalue()
        self.assertIn("FAIL", output)


# Finding 4: Never print an email address
class NoEmailInMessagesTests(unittest.TestCase):
    """F11-fix finding 4: FAIL messages must not contain email addresses."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_fail_messages_contain_no_at_sign(self):
        """FAIL messages for author checks must not contain @ symbol."""
        merge_oid = "b" * 40  # Valid hex
        noreply_email = "teomarcelo@users.noreply.github.com"
        bad_email = "bad@example.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, noreply_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c) and "files" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            # After merge: pr view to get merge commit
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in " ".join(c) and "state" not in " ".join(c), 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}, "headRefOid": OID})),
            # Bad email
            (lambda c: c[:2] == ["gh", "api"] and "commits" in " ".join(c), 0, bad_email + "\n"),
            # CI is green (still need to handle this path)
            (lambda c: c[:3] == ["gh", "run", "list"], 0, json.dumps([{"headSha": merge_oid, "status": "completed", "conclusion": "success"}])),
        ])

        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        output = out.getvalue()
        # Must not contain @ symbol in FAIL messages
        fail_lines = [line for line in output.splitlines() if "FAIL" in line]
        for line in fail_lines:
            self.assertNotIn("@", line, f"FAIL message must not contain email address: {line}")


# Finding 5: Pre-merge check must use full-match regex
class PreMergeEmailCheckTests(unittest.TestCase):
    """F11-fix finding 5: pre-merge email check uses full-match regex."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_empty_local_part_refused(self):
        """Email with empty local part (@users.noreply.github.com) is refused."""
        bad_email = "@users.noreply.github.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, bad_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
        ])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should fail pre-merge check
        self.assertEqual(code, 1)
        output = out.getvalue()
        self.assertIn("FAIL", output)

    def test_spaces_in_email_refused(self):
        """Email with spaces is refused."""
        bad_email = "user name@users.noreply.github.com"

        runner = FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, bad_email + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
        ])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should fail pre-merge check
        self.assertEqual(code, 1)
        output = out.getvalue()
        self.assertIn("FAIL", output)


class F11Fix2Tests(unittest.TestCase):
    """F11 fix round 2: normalized SHA, merged-but-unreadable SHA, author-fail CI timeout."""

    NOREPLY = "teomarcelo@users.noreply.github.com"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def runner(self, merge_oid, api_out):
        return FakeRunner([
            (lambda c: c == ["git", "config", "user.email"], 0, self.NOREPLY + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "files" in " ".join(c), 0, json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in " ".join(c), 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}, "headRefOid": OID})),
            (lambda c: c[:2] == ["gh", "api"], 0, api_out),
            (lambda c: c[:3] == ["gh", "pr", "create"], 0, "https://github.com/test/test/pull/99\n"),
        ])

    def merge(self, runner, ci):
        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             mock.patch.object(team_gates, "wait_main_ci", return_value=ci) as wait, \
             mock.patch.object(team_gates.time, "sleep", side_effect=AssertionError("real sleep")), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)
        return code, out.getvalue(), wait

    def test_normalized_sha_goes_to_gh_api_ci_and_git_revert(self):
        raw = "  " + "B" * 40 + "\n"
        runner = self.runner(raw, "bad@example.com\n")
        code, out, wait = self.merge(runner, "failure")
        self.assertEqual(code, 1, out)
        api = runner.find("gh", "api")
        self.assertEqual(api[0][0][2], "repos/{owner}/{repo}/commits/" + "b" * 40)
        self.assertEqual(wait.call_args.args[1], "b" * 40)
        self.assertEqual(runner.find("git", "revert")[0][0], ["git", "revert", "--no-edit", "b" * 40])

    def test_unreadable_sha_says_already_merged_check_ci_by_hand(self):
        for oid in ("", "not-a-valid-sha"):
            with self.subTest(oid=oid):
                runner = self.runner(oid, self.NOREPLY + "\n")
                code, out, wait = self.merge(runner, "success")
                self.assertEqual(code, 1)
                self.assertIn("FAIL", out)
                self.assertIn("already merged", out)
                self.assertIn("check main CI by hand", out)
                self.assertFalse(runner.find("gh", "api"))
                wait.assert_not_called()

    def test_author_fail_with_ci_timeout_prints_not_finished(self):
        for api_out, problem in (("bad@example.com\n", "non-noreply author"),
                                 ("", "cannot read merge commit author")):
            with self.subTest(problem=problem):
                runner = self.runner("b" * 40, api_out)
                code, out, _ = self.merge(runner, "timeout")
                self.assertEqual(code, 1)
                self.assertIn(problem, out)
                self.assertIn("main CI not finished", out)
                self.assertNotIn("@", out)
                self.assertFalse(runner.find("git", "revert"))


if __name__ == "__main__":
    unittest.main()
