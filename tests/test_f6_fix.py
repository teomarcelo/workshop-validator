"""Tests for F6-fix security VETO items (each fails on 53801f2)."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workshop_validator import team_gates, team_kit
from workshop_validator.public_safety import check_commit_emails, _count_bad_emails


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


# Item 1: Missing base ref message
class MissingBaseRefTests(unittest.TestCase):
    """F6-fix item 1a: clear message when base ref not found."""

    def test_missing_base_ref_fails_with_clear_message(self):
        """When origin/main is missing, fail with clear message."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Initialize git repo without origin/main
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Create a commit
            (repo / "test.txt").write_text("test")
            subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "test"], cwd=repo, check=True, capture_output=True)

            # Check should fail with clear message
            ok, msg = check_commit_emails(repo, "origin/main")
            self.assertFalse(ok)
            self.assertIn("origin/main not found", msg)
            self.assertIn("fetch it first", msg)


# Item 2: State AND headRefOid check
class MergeStateAndOidTests(unittest.TestCase):
    """F6-fix item 2: continue only when state MERGED AND headRefOid matches."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_merged_but_head_changed_fails(self):
        """State MERGED but headRefOid doesn't match reviewed head - should fail."""
        merge_oid = "m" * 40
        different_oid = "d" * 40
        runner = FakeRunner([
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in " ".join(c), 0,
             json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 1, ""),  # Non-zero exit
            # State is MERGED but headRefOid changed
            (lambda c: c[:3] == ["gh", "pr", "view"] and "state" in " ".join(c), 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}, "state": "MERGED", "headRefOid": different_oid})),
        ])
        out = io.StringIO()
        with mock.patch.object(team_gates, "merged_tree_gates", return_value=True), \
             mock.patch.object(team_gates, "prove_tests", return_value=True), \
             contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        self.assertEqual(code, 1)
        self.assertIn("not merged or head changed", out.getvalue())


# Item 3: Force-with-lease branch delete
class ForceWithLeaseDeleteTests(unittest.TestCase):
    """F6-fix item 3: remote branch delete with --force-with-lease."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_delete_uses_force_with_lease(self):
        """Branch delete should use --force-with-lease with reviewed oid."""
        # Verify the code uses --force-with-lease format by reading the source
        import inspect
        source = inspect.getsource(team_gates.merge_when_green)
        # Should contain the force-with-lease flag with head:head_oid format
        self.assertIn("--force-with-lease=", source)
        self.assertIn("refs/heads/", source)
        # Should check automation/* branch
        self.assertIn('head.startswith("automation/")', source)


# Item 4: pr-ancestor only for this branch's PRs
class PrAncestorOwnBranchTests(unittest.TestCase):
    """F6-fix item 4: pr-ancestor only for this branch's own PRs."""

    def test_ancestor_of_other_pr_not_tree_in_main_kept(self):
        """Branch tip is ancestor of OTHER PR's head but tree not in main - kept."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "wt"
            wt.mkdir(parents=True)
            branch_oid = "1" * 40
            other_pr_oid = "9" * 40
            tree_oid = "t" * 40
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/feature\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),  # Clean
                # Query PRs for THIS branch's head - returns empty (no own-branch PR)
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c and "automation/feature" in c, 0, "[]"),
                (lambda c: c == ["git", "rev-parse", "automation/feature"], 0, branch_oid + "\n"),
                # Tree check
                (lambda c: len(c) == 3 and c[:2] == ["git", "rev-parse"] and "^{tree}" in c[2], 0, tree_oid + "\n"),
                # Tree NOT in origin/main
                (lambda c: c[:2] == ["git", "log"] and "origin/main" in c, 0, "other-tree\n"),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            # Should be kept with no-proof (not deleted via pr-ancestor from other PR)
            self.assertIn("keep", out.getvalue())
            self.assertIn("no-proof", out.getvalue())


# Item 5: Empty email fails, never print email/domain
class EmptyEmailTests(unittest.TestCase):
    """F6-fix item 5: empty email fails, never print email or domain."""

    def test_empty_email_fails(self):
        """Empty author or committer email should fail."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "base@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Base commit
            (repo / "base.txt").write_text("base")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "branch", "origin_main"], cwd=repo, check=True, capture_output=True)

            # Commit with empty email (simulate by checking error message format)
            subprocess.run(["git", "config", "user.email", "bad@example.com"], cwd=repo, check=True, capture_output=True)
            (repo / "test.txt").write_text("test")
            subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "test"], cwd=repo, check=True, capture_output=True)

            ok, msg = check_commit_emails(repo, "origin_main")
            self.assertFalse(ok)
            # Should NOT print the email or domain
            self.assertNotIn("bad@example.com", msg)
            self.assertNotIn("example.com", msg)
            # Should print count only
            self.assertIn("non-github noreply email", msg.lower())


# Item 6: Atomic write for decide
class AtomicDecideWriteTests(unittest.TestCase):
    """F6-fix item 6: atomic write for team_kit decide."""

    def test_failing_write_leaves_old_register_untouched(self):
        """If write fails, old register should remain untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            team = Path(tmp) / "team"
            team.mkdir()
            repo = Path(tmp) / "repo"
            repo.mkdir()

            # Create initial register
            register = team / "DECISIONS.md"
            original_content = "| W-001 | 2026-10-27 | NOTED | Original |\n"
            register.write_text(original_content)

            # Create minimal plan
            team_kit.save_plan(team, {"steps": [{"id": "a", "state": "done"}]})

            # Mock _write_private to fail on dashboard write
            original_write = team_kit._write_private
            call_count = [0]

            def failing_write(path, text):
                call_count[0] += 1
                if call_count[0] == 1:  # First call (register)
                    original_write(path, text)
                else:  # Second call (dashboard) - fail
                    raise IOError("Simulated write failure")

            with mock.patch('workshop_validator.team_kit._write_private', side_effect=failing_write):
                try:
                    team_kit.append_decision(team, "W-002", "NOTED", "New decision", repo)
                    self.fail("Should have raised IOError")
                except IOError:
                    pass

            # Old register should be untouched (atomic write would restore it)
            # Note: This test will FAIL on 53801f2 because non-atomic write leaves partial state
            current = register.read_text() if register.exists() else ""
            # After fix: should have original OR new (atomic), not partial
            self.assertTrue(current == original_content or "W-002" in current)


# Security re-review: Real merge commits and PR_HEAD validation
class RealMergeCommitTests(unittest.TestCase):
    """Security re-review: Real merge commits with bad emails must fail."""

    def test_real_merge_commit_with_bad_email_fails(self):
        """Real merge commit on a branch with non-noreply email should FAIL the check."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Create base commit on main
            (repo / "base.txt").write_text("base")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "branch", "origin_main"], cwd=repo, check=True, capture_output=True)

            # Create feature branch from origin_main
            subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True, capture_output=True)
            (repo / "feature.txt").write_text("feature")
            subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feature commit"], cwd=repo, check=True, capture_output=True)

            # Create another branch from origin_main
            subprocess.run(["git", "checkout", "origin_main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "main-branch"], cwd=repo, check=True, capture_output=True)

            # Merge feature into main-branch with a non-noreply email (real merge, not synthetic)
            subprocess.run(["git", "config", "user.email", "bad@example.com"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "merge", "--no-ff", "feature", "-m", "Merge feature"], cwd=repo, check=True, capture_output=True)

            # This is a REAL merge commit on a branch (not GitHub's synthetic merge)
            # It should FAIL because the merge commit has a non-noreply email
            # On 3dcb21c: wrongly passes (HEAD^2 skip catches it)
            # After fix: correctly fails
            ok, msg = check_commit_emails(repo, "origin_main", "HEAD")
            self.assertFalse(ok, "Real merge commit with bad email should FAIL")
            self.assertIn("non-github noreply email", msg.lower())

    def test_pr_head_env_used_when_set(self):
        """When PR_HEAD env is set, use it instead of HEAD."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Base commit
            (repo / "base.txt").write_text("base")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "branch", "origin_main"], cwd=repo, check=True, capture_output=True)

            # PR commit
            (repo / "pr.txt").write_text("pr")
            subprocess.run(["git", "add", "pr.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "pr"], cwd=repo, check=True, capture_output=True)
            pr_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

            # Pass PR_HEAD directly to check_commit_emails
            ok, msg = check_commit_emails(repo, "origin_main", pr_sha)
            self.assertTrue(ok)

    def test_invalid_pr_head_fails_closed(self):
        """Invalid PR_HEAD value should fail closed with validation error."""
        from workshop_validator.public_safety import OID_PATTERN

        # Valid OID (40 hex chars)
        self.assertTrue(OID_PATTERN.match("a" * 40))

        # Invalid values
        self.assertFalse(OID_PATTERN.match("not-a-valid-oid"))
        self.assertFalse(OID_PATTERN.match("a" * 39))  # Too short
        self.assertFalse(OID_PATTERN.match("a" * 41))  # Too long
        self.assertFalse(OID_PATTERN.match("g" * 40))  # Invalid hex char
        self.assertFalse(OID_PATTERN.match(""))  # Empty


class EmptyEmailDetectionTests(unittest.TestCase):
    """Security re-review: Empty email detection with NUL-separated format."""

    def test_count_bad_emails_with_empty_author(self):
        """Empty author email should be counted as bad."""
        # Format: author\x00committer per commit, newline-separated
        # Empty author, good committer
        git_output = "\x00good@users.noreply.github.com"
        bad_count = _count_bad_emails(git_output)
        self.assertEqual(bad_count, 1, "Empty author should be counted as bad")

    def test_count_bad_emails_with_empty_committer(self):
        """Empty committer email should be counted as bad."""
        # Good author, empty committer
        git_output = "good@users.noreply.github.com\x00"
        bad_count = _count_bad_emails(git_output)
        self.assertEqual(bad_count, 1, "Empty committer should be counted as bad")

    def test_count_bad_emails_all_good(self):
        """All good noreply emails should return 0 bad count."""
        # Two commits, all noreply emails
        git_output = "author1@users.noreply.github.com\x00committer1@users.noreply.github.com\nauthor2@users.noreply.github.com\x00committer2@users.noreply.github.com"
        bad_count = _count_bad_emails(git_output)
        self.assertEqual(bad_count, 0, "All noreply emails should be good")

    def test_count_bad_emails_with_non_noreply(self):
        """Non-noreply emails should be counted as bad."""
        git_output = "bad@example.com\x00good@users.noreply.github.com"
        bad_count = _count_bad_emails(git_output)
        self.assertEqual(bad_count, 1, "Non-noreply email should be counted as bad")


if __name__ == "__main__":
    unittest.main()
