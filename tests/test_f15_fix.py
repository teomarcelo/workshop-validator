"""Tests for F15: worktree-cleanup recognises fix-round bases and F15-fix1: fetch optimization."""

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




class FetchOptimizationTests(unittest.TestCase):
    """F15-fix1: fetch each oid at most once, only when not local."""

    def test_local_oids_not_fetched(self):
        """Several worktrees with several merged PRs whose commits are local - zero fetches."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt1 = repo / "agent-wt-1"
            wt2 = repo / "agent-wt-2"
            wt1.mkdir(parents=True)
            wt2.mkdir(parents=True)
            agent1_oid = "a" * 40
            agent2_oid = "b" * 40
            pr1_oid = "c" * 40
            pr2_oid = "d" * 40
            tree1 = "e" * 40
            tree2 = "f" * 40

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt1}\nHEAD {agent1_oid}\nbranch refs/heads/worktree-agent-one\n\n"
                         f"worktree {wt2}\nHEAD {agent2_oid}\nbranch refs/heads/worktree-agent-two\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                # Two merged PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{pr1_oid}"}}, {{"headRefOid": "{pr2_oid}"}}]'),
                # Both worktrees clean
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),
                # No own PRs for agent branches
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]"),
                # Branch oids
                (lambda c: c == ["git", "rev-parse", "worktree-agent-one"], 0, agent1_oid + "\n"),
                (lambda c: c == ["git", "rev-parse", "worktree-agent-two"], 0, agent2_oid + "\n"),
                # ALL cat-file checks return 0 (all oids are local)
                (lambda c: c[:2] == ["git", "cat-file"] and "-e" in c, 0, ""),
                # merge-base checks (none are ancestors)
                (lambda c: c[:2] == ["git", "merge-base"] and "--is-ancestor" in c, 1, ""),
                # Tree oids
                (lambda c: c[:2] == ["git", "rev-parse"] and agent1_oid in str(c) and "tree" in str(c), 0, tree1 + "\n"),
                (lambda c: c[:2] == ["git", "rev-parse"] and agent2_oid in str(c) and "tree" in str(c), 0, tree2 + "\n"),
                # Trees not in main
                (lambda c: c[:2] == ["git", "log"] and "origin/main" in c, 0, "other-tree\n"),
                (lambda c: c[:3] == ["git", "worktree", "prune"], 0, ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            # Verify zero fetches happened (all oids were local)
            fetch_calls = [c for c, cwd in runner.calls if c[:2] == ["git", "fetch"]]
            self.assertEqual(len(fetch_calls), 0, f"Expected zero fetches when all oids local, got {len(fetch_calls)}: {fetch_calls}")

    def test_missing_oid_fetched_once_across_worktrees(self):
        """Missing oid is fetched once even when several worktrees need it."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt1 = repo / "agent-wt-1"
            wt2 = repo / "agent-wt-2"
            wt3 = repo / "agent-wt-3"
            for wt in [wt1, wt2, wt3]:
                wt.mkdir(parents=True)
            
            agent1_oid = "a" * 40
            agent2_oid = "b" * 40
            agent3_oid = "c" * 40
            shared_pr_oid = "d" * 40  # This oid is needed by all three worktrees

            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt1}\nHEAD {agent1_oid}\nbranch refs/heads/worktree-agent-one\n\n"
                         f"worktree {wt2}\nHEAD {agent2_oid}\nbranch refs/heads/worktree-agent-two\n\n"
                         f"worktree {wt3}\nHEAD {agent3_oid}\nbranch refs/heads/worktree-agent-three\n")

            fetch_count = [0]  # Track fetches of shared_pr_oid

            class TrackingRunner(FakeRunner):
                def __call__(self, cmd, cwd):
                    self.calls.append((list(cmd), Path(cwd)))
                    # Track fetches of the shared oid
                    if cmd[:3] == ["git", "fetch", "origin"] and shared_pr_oid in cmd:
                        fetch_count[0] += 1
                    # cat-file for shared_pr_oid: NOT local before first fetch (code 1), local after (code 0)
                    if cmd[:2] == ["git", "cat-file"] and "-e" in cmd and shared_pr_oid in str(cmd):
                        code = 1 if fetch_count[0] == 0 else 0
                        return subprocess.CompletedProcess(cmd, code, "", "")
                    # Use parent logic for all other commands
                    for match, code, out in self.rules:
                        if match(cmd):
                            return subprocess.CompletedProcess(cmd, code, out, "")
                    return subprocess.CompletedProcess(cmd, 0, "", "")

            runner = TrackingRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                # One merged PR with the shared oid
                (lambda c: c[:2] == ["gh", "pr"] and "--state" in c and "merged" in c and "--limit" in c and "200" in c,
                 0, f'[{{"headRefOid": "{shared_pr_oid}"}}]'),
                # All worktrees clean
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),
                # No own PRs
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]"),
                # Branch oids
                (lambda c: c == ["git", "rev-parse", "worktree-agent-one"], 0, agent1_oid + "\n"),
                (lambda c: c == ["git", "rev-parse", "worktree-agent-two"], 0, agent2_oid + "\n"),
                (lambda c: c == ["git", "rev-parse", "worktree-agent-three"], 0, agent3_oid + "\n"),
                # Fetch of the shared oid (only happens once)
                (lambda c: c[:3] == ["git", "fetch", "origin"] and shared_pr_oid in c, 0, ""),
                # merge-base checks (all are ancestors after fetch succeeds)
                (lambda c: c[:2] == ["git", "merge-base"] and "--is-ancestor" in c, 0, ""),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0, dry_run=True)

            output = out.getvalue()
            # All three worktrees should be removed (merged-pr-ancestor)
            self.assertIn("remove", output)
            # Count how many worktrees were removed with merged-pr-ancestor
            removed_lines = [line for line in output.split('\n') if 'merged-pr-ancestor' in line]
            self.assertEqual(len(removed_lines), 3, f"Expected 3 worktrees removed, got {len(removed_lines)}")
            # Verify the shared oid was fetched exactly once
            fetch_calls = [c for c, cwd in runner.calls if c[:3] == ["git", "fetch", "origin"] and shared_pr_oid in c]
            self.assertEqual(len(fetch_calls), 1, f"Expected exactly 1 fetch of shared oid across 3 worktrees, got {len(fetch_calls)}")


if __name__ == "__main__":
    unittest.main()
