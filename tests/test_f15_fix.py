"""Tests for F15: worktree-cleanup recognises fix-round bases (each fails on 5d1fe4d)."""

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from workshop_validator import team_gates


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


class MergedPrAncestorTests(unittest.TestCase):
    """F15: worktree-agent-* branches whose tip is ancestor of merged PR head are removed."""

    def test_agent_branch_ancestor_of_merged_pr_removed(self):
        """worktree-agent-* branch tip is ancestor of merged PR head - removed with merged-pr-ancestor."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            agent_branch_oid = "a" * 40
            pr_head_oid = "b" * 40
            tree_oid = "t" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {agent_branch_oid}\nbranch refs/heads/worktree-agent-xyz\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                # All merged PRs - includes the one whose head is a descendant
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{pr_head_oid}"}}]'),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),  # Clean
                # This branch has no own PRs (worktree-agent-*)
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c and "worktree-agent-xyz" in c, 0, "[]"),
                (lambda c: c == ["git", "rev-parse", "worktree-agent-xyz"], 0, agent_branch_oid + "\n"),
                # Fetch the PR head oid
                (lambda c: c[:3] == ["git", "fetch", "origin"] and pr_head_oid in c, 0, ""),
                # Check if agent_branch_oid is ancestor of pr_head_oid - YES
                (lambda c: c[:2] == ["git", "merge-base"] and "--is-ancestor" in c and agent_branch_oid in c and pr_head_oid in c,
                 0, ""),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            # Should be removed with merged-pr-ancestor reason
            self.assertIn("remove", output)
            self.assertIn("merged-pr-ancestor", output)
            self.assertNotIn("no-proof", output)

    def test_agent_branch_extra_commit_kept_no_proof(self):
        """worktree-agent-* branch with one extra commit not in any merged PR head - kept no-proof."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            agent_branch_oid = "a" * 40
            pr_head_oid = "b" * 40
            tree_oid = "t" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {agent_branch_oid}\nbranch refs/heads/worktree-agent-xyz\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                # All merged PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{pr_head_oid}"}}]'),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),  # Clean
                # This branch has no own PRs (worktree-agent-*)
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c and "worktree-agent-xyz" in c, 0, "[]"),
                (lambda c: c == ["git", "rev-parse", "worktree-agent-xyz"], 0, agent_branch_oid + "\n"),
                # Fetch the PR head oid
                (lambda c: c[:3] == ["git", "fetch", "origin"] and pr_head_oid in c, 0, ""),
                # Check if agent_branch_oid is ancestor of pr_head_oid - NO (not an ancestor)
                (lambda c: c[:2] == ["git", "merge-base"] and "--is-ancestor" in c and agent_branch_oid in c and pr_head_oid in c,
                 1, ""),
                # Tree check
                (lambda c: len(c) == 3 and c[:2] == ["git", "rev-parse"] and "^{tree}" in c[2], 0, tree_oid + "\n"),
                # Tree NOT in origin/main
                (lambda c: c[:2] == ["git", "log"] and "origin/main" in c, 0, "other-tree\n"),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            # Should be kept with no-proof
            self.assertIn("keep", output)
            self.assertIn("no-proof", output)
            self.assertNotIn("merged-pr-ancestor", output)

    def test_dirty_worktree_kept(self):
        """Dirty worktree is always kept, even if it would match merged-pr-ancestor."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            agent_branch_oid = "a" * 40
            pr_head_oid = "b" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {agent_branch_oid}\nbranch refs/heads/worktree-agent-xyz\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                # All merged PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{pr_head_oid}"}}]'),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, "M file.txt\n"),  # Dirty
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            # Should be kept because dirty
            self.assertIn("keep", output)
            self.assertIn("dirty", output)
            self.assertNotIn("merged-pr-ancestor", output)

    def test_non_agent_branch_not_checked_for_merged_pr_ancestor(self):
        """Non-worktree-agent-* branches are NOT checked for merged-pr-ancestor."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "feature-wt"
            wt.mkdir(parents=True)
            branch_oid = "a" * 40
            pr_head_oid = "b" * 40
            tree_oid = "t" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/feature\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                # All merged PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{pr_head_oid}"}}]'),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),  # Clean
                # This branch might have its own PRs (not worktree-agent-*)
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c and "automation/feature" in c, 0, "[]"),
                (lambda c: c == ["git", "rev-parse", "automation/feature"], 0, branch_oid + "\n"),
                # Tree check (merged-pr-ancestor is skipped for non-agent branches)
                (lambda c: len(c) == 3 and c[:2] == ["git", "rev-parse"] and "^{tree}" in c[2], 0, tree_oid + "\n"),
                # Tree NOT in origin/main
                (lambda c: c[:2] == ["git", "log"] and "origin/main" in c, 0, "other-tree\n"),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            # Should be kept with no-proof (merged-pr-ancestor doesn't apply to automation/* branches)
            self.assertIn("keep", output)
            self.assertIn("no-proof", output)
            # merged-pr-ancestor check should not run for non-agent branches
            # The runner should NOT have any merge-base --is-ancestor calls
            merge_base_calls = runner.find("git", "merge-base")
            self.assertEqual(len(merge_base_calls), 0, "merged-pr-ancestor should not check non-agent branches")

    def test_invalid_pr_oid_skipped(self):
        """Invalid PR OID is skipped, doesn't crash."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "agent-wt"
            wt.mkdir(parents=True)
            agent_branch_oid = "a" * 40
            tree_oid = "t" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {agent_branch_oid}\nbranch refs/heads/worktree-agent-xyz\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                # All merged PRs - includes invalid OID
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, '[{"headRefOid": "invalid-oid"}, {"headRefOid": null}]'),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),  # Clean
                # This branch has no own PRs (worktree-agent-*)
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c and "worktree-agent-xyz" in c, 0, "[]"),
                (lambda c: c == ["git", "rev-parse", "worktree-agent-xyz"], 0, agent_branch_oid + "\n"),
                # Tree check (invalid OIDs are skipped, so we get to tree-in-main)
                (lambda c: len(c) == 3 and c[:2] == ["git", "rev-parse"] and "^{tree}" in c[2], 0, tree_oid + "\n"),
                # Tree NOT in origin/main
                (lambda c: c[:2] == ["git", "log"] and "origin/main" in c, 0, "other-tree\n"),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            # Should be kept with no-proof (invalid OIDs skipped)
            self.assertIn("keep", output)
            self.assertIn("no-proof", output)
            # No fetch or merge-base calls should have been made for invalid OIDs
            fetch_calls = runner.find("git", "fetch")
            self.assertEqual(len(fetch_calls), 0, "Should not fetch invalid OIDs")


if __name__ == "__main__":
    unittest.main()
