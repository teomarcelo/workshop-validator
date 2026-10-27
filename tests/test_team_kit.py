"""Fixture tests for the producer-team kit, hooks and gates (synthetic data only)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from workshop_validator import team_gates, team_hooks, team_kit
from workshop_validator.public_safety import inspect_tracked
from workshop_validator.team_dashboard import render
from workshop_validator.team_gates import (
    clock24_hits, deleted_decision_files, em_dash_hits, is_review_stale, merge_blockers,
    missing_register_rows, removed_rows, required_reviewers, review_verdicts,
    worktree_idle_time,
)
from workshop_validator.team_hooks import guard_decision, stop_decision

NOW = datetime(2026, 10, 26, 18, 0, tzinfo=timezone.utc)


def plan(*steps):
    return {"steps": [dict(s) for s in steps]}


class ChainStatusTests(unittest.TestCase):
    def status(self, p, quiet_min=0, disk=100):
        return team_kit.chain_status(p, activity=(NOW - timedelta(minutes=quiet_min)).timestamp(),
                                     now=NOW, disk_free_gb=disk)[0]

    def test_states(self):
        self.assertEqual(self.status(plan({"id": "a", "state": "running"})), "WORKING")
        self.assertEqual(self.status(plan({"id": "a", "state": "running"}), quiet_min=31), "STALLED")
        self.assertEqual(self.status(plan({"id": "a", "state": "next"})), "IDLE")
        self.assertEqual(self.status(plan({"id": "a", "state": "waiting", "blocker": "Owner: x"})), "WAITING")
        self.assertEqual(self.status(plan({"id": "a", "state": "done"})), "DONE")
        self.assertEqual(self.status(plan()), "DONE")

    def test_low_disk_is_stalled(self):
        self.assertEqual(self.status(plan({"id": "a", "state": "done"}), disk=14), "STALLED")

    def test_blocked_or_unmet_next_step_is_not_idle(self):
        self.assertEqual(self.status(plan({"id": "a", "state": "next", "blocker": "Owner: y"})), "WAITING")
        self.assertEqual(self.status(plan({"id": "b", "state": "next", "prereqs": ["step:a"]},
                                          {"id": "a", "state": "waiting", "blocker": "Owner"})), "WAITING")
        self.assertEqual(self.status(plan({"id": "b", "state": "next", "prereqs": ["step:a"]},
                                          {"id": "a", "state": "done"})), "IDLE")

    def test_unknown_prereq_kind_is_unmet(self):
        self.assertEqual(team_kit.unmet_prereqs({"prereqs": ["magic:x"]}, plan()), ["magic:x"])

    def test_owner_ask_not_doable(self):
        p = plan({"id": "a", "state": "waiting", "blocker": "Owner: sign in", "prereqs": ["step:b"]},
                 {"id": "b", "state": "next"})
        self.assertEqual(team_kit.owner_asks_not_doable(p), ["a"])


class PlanAndLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.team = Path(self.tmp.name) / "team"

    def test_set_step_and_bad_state(self):
        team_kit.save_plan(self.team, plan({"id": "a", "state": "next"}), NOW)
        team_kit.set_step(self.team, "a", "running", NOW, branch="automation/x")
        self.assertEqual(team_kit.load_plan(self.team)["steps"][0]["branch"], "automation/x")
        with self.assertRaises(ValueError):
            team_kit.save_plan(self.team, plan({"id": "a", "state": "busy"}))
        self.assertEqual(os.stat(team_kit.now_path(self.team)).st_mode & 0o777, 0o600)

    def test_ledger_appends_and_validates(self):
        team_kit.append_ledger(self.team, {"role": "qa", "task": "t1", "verdict": "PASS"}, NOW)
        team_kit.append_ledger(self.team, {"role": "qa", "task": "t2"}, NOW)
        lines = team_kit.ledger_path(self.team).read_text().splitlines()
        self.assertEqual([json.loads(x)["task"] for x in lines], ["t1", "t2"])
        with self.assertRaises(ValueError):
            team_kit.append_ledger(self.team, {"role": "qa"})
        with self.assertRaises(ValueError):
            team_kit.append_ledger(self.team, {"role": "qa", "task": "t", "secret": "x"})

    def test_producer_claim_binds_once(self):
        team_kit.claim_producer(self.team, now=NOW)
        self.assertTrue(team_kit.bind_producer(self.team, "s1"))
        self.assertFalse(team_kit.bind_producer(self.team, "s2"))
        self.assertEqual(team_kit.load_producer(self.team)["session_id"], "s1")
        team_kit.release_producer(self.team)
        self.assertIsNone(team_kit.load_producer(self.team))


class WatchdogActionTests(unittest.TestCase):
    def act(self, state="IDLE", live=True, active_min=5, nudged_min=None):
        return team_kit.watchdog_action(
            state, {"session_id": "s"} if live else None, now=NOW, idle_since=NOW,
            nudged_at=None if nudged_min is None else NOW - timedelta(minutes=nudged_min),
            session_active_at=NOW - timedelta(minutes=active_min))

    def test_actions(self):
        self.assertEqual(self.act(state="WORKING"), "NONE")
        self.assertEqual(self.act(state="WAITING"), "NONE")
        self.assertEqual(self.act(), "NUDGE")
        self.assertEqual(self.act(nudged_min=10), "NONE")
        self.assertEqual(self.act(nudged_min=31), "TAKEOVER")
        self.assertEqual(self.act(live=False), "TAKEOVER")
        self.assertEqual(self.act(active_min=121), "TAKEOVER")


class WriteGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home = base / "home"
        self.repo = base / "repo"
        self.tree = self.repo / ".claude" / "worktrees" / "agent-1"
        self.scratch = base / "scratch"
        for d in (self.home, self.tree, self.scratch):
            d.mkdir(parents=True)

    def decide(self, target, cwd=None, root="auto"):
        cwd = cwd or self.repo
        lookup = (lambda c: self.tree if Path(c).resolve() == self.tree else self.repo) if root == "auto" else (lambda c: root)
        return guard_decision({"cwd": str(cwd), "tool_input": {"file_path": str(target)}},
                              home=self.home, git_root=lookup, scratch=[self.scratch])[0]

    def test_inside_own_workspace_allowed(self):
        self.assertTrue(self.decide(self.repo / "docs" / "x.md"))
        self.assertTrue(self.decide("docs/x.md"))
        self.assertTrue(self.decide(self.tree / "a.py", cwd=self.tree))

    def test_outside_denied(self):
        self.assertFalse(self.decide(self.home / "elsewhere.txt"))
        self.assertFalse(self.decide(self.repo / "a.py", cwd=self.tree))

    def test_producer_cannot_write_into_agent_worktree(self):
        self.assertFalse(self.decide(self.tree / "a.py"))

    def test_unresolved_workspace_denied(self):
        self.assertFalse(self.decide(self.repo / "a.py", root=None))
        self.assertFalse(self.decide(self.repo / "a.py", cwd=self.repo / "missing"))
        self.assertFalse(guard_decision({"cwd": str(self.repo), "tool_input": {}},
                                        home=self.home, git_root=lambda c: self.repo)[0])

    def test_private_team_and_scratch_allowed_credentials_denied(self):
        self.assertTrue(self.decide(self.home / ".workshop-validator" / "team" / "DECISIONS.md"))
        self.assertTrue(self.decide(self.scratch / "notes.txt"))
        self.assertFalse(self.decide(self.repo / "verification-orgs.json"))
        self.assertFalse(self.decide(self.home / ".workshop-validator" / "verification-orgs.json"))


class StopHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.team = Path(self.tmp.name) / "team"
        team_kit.claim_producer(self.team, "prod")

    def stop(self, state="WORKING", session="prod", active=False):
        return stop_decision({"session_id": session, "stop_hook_active": active}, team=self.team,
                             repo=Path(self.tmp.name), status_fn=lambda r, t: (state, "why"))

    def test_idle_and_stalled_block_the_producer(self):
        self.assertIn("IDLE", self.stop("IDLE"))
        self.assertIn("STALLED", self.stop("STALLED"))
        self.assertIsNone(self.stop("WAITING"))

    def test_second_stop_in_a_row_is_let_through(self):
        self.assertIsNone(self.stop("IDLE", active=True))

    def test_other_sessions_are_not_blocked(self):
        self.assertIsNone(self.stop("IDLE", session="someone-else"))

    def test_stale_dashboard_blocks(self):
        team_kit.save_plan(self.team, plan({"id": "a", "state": "done"}))
        self.assertIn("Dashboard is stale", self.stop("DONE"))
        dash = self.team / "dashboard.html"
        dash.write_text("x")
        later = time.time() + 5
        os.utime(dash, (later, later))
        self.assertIsNone(self.stop("DONE"))


class GateTests(unittest.TestCase):
    DIFF = ("diff --git a/docs/a.md b/docs/a.md\n--- a/docs/a.md\n+++ b/docs/a.md\n"
            "@@ -1,2 +1,2 @@\n-| W-901 | 2026-01-01 | NOTED | old |\n+new line — with dash\n"
            "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n+s = '—'\n")

    def test_em_dash_only_in_added_doc_lines(self):
        self.assertEqual(em_dash_hits(self.DIFF), ["docs/a.md"])

    def test_removed_rows(self):
        self.assertEqual(removed_rows(self.DIFF), ["W-901"])
        moved = "-| W-902 | a |\n+| W-902 | a, superseded by W-903 |\n"
        self.assertEqual(removed_rows(moved), [])
        self.assertEqual(deleted_decision_files("D\tdocs/decisions/x.md\nM\tREADME.md"), ["docs/decisions/x.md"])

    def test_register_snapshot(self):
        snap = "| W-901 | d | NOTED | a |\n| W-902 | d | NOTED | b |\n"
        self.assertEqual(missing_register_rows(snap, "| W-902 | d | NOTED | b |\n"), ["W-901"])
        self.assertEqual(missing_register_rows(snap, snap + "| W-903 | d | NOTED | c |\n"), [])

    def test_clock24(self):
        self.assertEqual(clock24_hits("Updated Oct 26, 3:07 PM"), [])
        self.assertEqual(clock24_hits("at 15:07"), ["15:07"])
        self.assertTrue(clock24_hits("2026-10-26T18:00:00+00:00"))

    def test_merge_blockers(self):
        green = [{"name": "unit", "bucket": "pass"}]
        ok = {"code-reviewer": "APPROVE", "safety-scanner": "CLEAR"}
        self.assertEqual(merge_blockers(green, ok, list(ok)), [])
        self.assertTrue(merge_blockers([], ok, list(ok)))
        self.assertTrue(merge_blockers([{"name": "unit", "bucket": "fail"}], ok, list(ok)))
        self.assertTrue(merge_blockers(green, {"code-reviewer": "APPROVE"}, list(ok)))
        self.assertTrue(merge_blockers(green, {**ok, "visual-qa": "VETO"}, list(ok)))

    def test_required_reviewers(self):
        self.assertEqual(required_reviewers(["docs/a.md"]), ["code-reviewer", "safety-scanner"])
        self.assertIn("rule-auditor", required_reviewers(["workshop_validator/a.py"]))

    def test_review_verdicts_read_last_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pr-7-code-reviewer.md"
            path.write_text("HEAD: abc123\nnotes\nVERDICT: VETO\nfixed\nVERDICT: APPROVE\n")
            (Path(tmp) / "pr-7-safety-scanner.md").write_text("no verdict here")
            self.assertEqual(review_verdicts(Path(tmp), 7, "abc123"),
                             {"code-reviewer": "APPROVE", "safety-scanner": "MISSING"})


class PrivacyAndDashboardTests(unittest.TestCase):
    def test_private_team_files_rejected_by_public_safety(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("DECISIONS.md", "standups/2026-10-26.md", "progress/now.json",
                         "briefs/task-1.md", "briefs/TEMPLATE.md", "ops/runs.jsonl"):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text("synthetic")
            for name in ("DECISIONS.md", "standups/2026-10-26.md", "progress/now.json",
                         "briefs/task-1.md", "ops/runs.jsonl"):
                with self.subTest(name=name):
                    self.assertEqual(inspect_tracked(root, [name]), ("private_team_file",))
            self.assertEqual(inspect_tracked(root, ["briefs/TEMPLATE.md"]), ())

    def test_dashboard_has_no_24_hour_times(self):
        p = plan({"id": "a", "title": "Synthetic", "state": "running", "changed": NOW.isoformat()})
        html = render(p, "WORKING", "a running", [{"number": 1, "title": "t"}],
                      [["W-901", "2026-10-26", "NOTED", "synthetic ruling"]], NOW)
        self.assertEqual(clock24_hits(html), [])
        self.assertIn("Working", html)


class HeadBindingTests(unittest.TestCase):
    def test_stale_review_without_head_marker(self):
        from workshop_validator.team_gates import is_review_stale
        self.assertTrue(is_review_stale("No HEAD marker here", "abc1234"))
        self.assertFalse(is_review_stale("HEAD: abc1234\nVERDICT: APPROVE", "abc1234"))
        self.assertFalse(is_review_stale("HEAD: abc1234567\nVERDICT: APPROVE", "abc1234567890"))
        self.assertTrue(is_review_stale("HEAD: abc1234\nVERDICT: APPROVE", "def5678"))


class SecurityReviewerRequiredTests(unittest.TestCase):
    def test_security_reviewer_for_sensitive_paths(self):
        from workshop_validator.team_gates import required_reviewers
        self.assertIn("security-reviewer", required_reviewers([".github/workflows/ci.yml"]))
        self.assertIn("security-reviewer", required_reviewers([".claude/settings.json"]))
        self.assertIn("security-reviewer", required_reviewers(["workshop_validator/team_hooks.py"]))
        self.assertIn("security-reviewer", required_reviewers(["workshop_validator/team_gates.py"]))
        self.assertIn("security-reviewer", required_reviewers(["workshop_validator/watchdog.py"]))
        self.assertNotIn("security-reviewer", required_reviewers(["docs/a.md"]))


class WorktreeIdleTimeTests(unittest.TestCase):
    def test_worktree_idle_time_is_newest_of_commit_and_file_mtimes(self):
        from workshop_validator.team_gates import worktree_idle_time
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "file.txt").write_text("test")
            file_mtime = (root / "file.txt").stat().st_mtime
            commit_time = file_mtime - 3600
            idle = worktree_idle_time(root, commit_time)
            self.assertAlmostEqual(idle, (time.time() - file_mtime) / 3600, delta=0.1)


class BindProducerBlankSessionTests(unittest.TestCase):
    def test_bind_producer_ignores_blank_session_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            team = Path(tmp) / "team"
            team_kit.claim_producer(team, now=NOW)
            self.assertFalse(team_kit.bind_producer(team, ""))
            self.assertFalse(team_kit.bind_producer(team, "  "))
            self.assertTrue(team_kit.bind_producer(team, "valid-id"))


class WatchdogTakeoverLimitTests(unittest.TestCase):
    def test_takeover_cooldown_and_limit(self):
        from workshop_validator import team_kit
        with tempfile.TemporaryDirectory() as tmp:
            team = Path(tmp) / "team"
            repo = Path(tmp) / "repo"
            team.mkdir()
            repo.mkdir()
            team_kit.save_plan(team, plan({"id": "a", "state": "next"}), NOW)

            # First takeover
            result = team_kit.watchdog_check(repo, team, NOW)
            self.assertIn("TAKEOVER", result)

            # Within 60 minutes: should return NONE
            result = team_kit.watchdog_check(repo, team, NOW + timedelta(minutes=30))
            self.assertIn("NONE", result)

            # After 60 minutes: should allow another takeover
            result = team_kit.watchdog_check(repo, team, NOW + timedelta(minutes=61))
            self.assertIn("TAKEOVER", result)


class WriteGuardSettingsProtectionTests(unittest.TestCase):
    def test_write_guard_denies_settings_from_main_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            repo = base / "repo"
            worktree = repo / ".claude" / "worktrees" / "agent-1"
            for d in (home, repo, worktree):
                d.mkdir(parents=True)

            lookup = lambda c: worktree if Path(c).resolve() == worktree else repo

            # Main checkout cannot write settings
            self.assertFalse(guard_decision(
                {"cwd": str(repo), "tool_input": {"file_path": str(repo / ".claude" / "settings.json")}},
                home=home, git_root=lookup)[0])

            # Worktree can write settings
            self.assertTrue(guard_decision(
                {"cwd": str(worktree), "tool_input": {"file_path": str(worktree / ".claude" / "settings.json")}},
                home=home, git_root=lookup, main_root=lambda c: repo)[0])


# ---- F2: round-2 findings (synthetic data, injected runners, no network) ----

OID = "a" * 40


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


GOOD_PR = {"headRefName": "automation/2026-01-01-x", "headRefOid": OID, "isCrossRepository": False,
           "files": [{"path": "docs/a.md"}], "state": "OPEN", "baseRefName": "main"}


class MergeWhenGreenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def run_merge(self, info):
        runner = FakeRunner([
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in c, 0,
             json.dumps({"mergeCommit": {"oid": "b" * 40}})),
            (lambda c: c[:3] == ["gh", "pr", "view"], 0, json.dumps(info)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:3] == ["gh", "run", "list"], 0,
             json.dumps([{"headSha": "b" * 40, "status": "completed", "conclusion": "success"}])),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
        ])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)
        return code, out.getvalue(), runner

    def test_missing_or_empty_head_oid_is_refused(self):
        for oid in (None, "", "  "):
            info = dict(GOOD_PR, headRefOid=oid)
            if oid is None:
                info.pop("headRefOid")
            with self.subTest(oid=oid):
                code, out, runner = self.run_merge(info)
                self.assertEqual(code, 2)
                self.assertEqual(len(out.strip().splitlines()), 1)
                self.assertIn("REFUSED", out)
                self.assertFalse(runner.find("git", "fetch"))
                self.assertFalse(runner.find("gh", "pr", "merge"))

    def test_cross_repository_pr_is_refused(self):
        code, out, runner = self.run_merge(dict(GOOD_PR, isCrossRepository=True))
        self.assertEqual(code, 2)
        self.assertIn("cross-repository", out)
        self.assertFalse(runner.find("git", "fetch"))
        self.assertFalse(runner.find("gh", "pr", "merge"))

    def test_fetches_main_and_head_then_pins_merge_to_head_oid(self):
        code, out, runner = self.run_merge(GOOD_PR)
        self.assertEqual(code, 0, out)
        fetches = runner.find("git", "fetch")
        self.assertEqual(fetches[0][0], ["git", "fetch", "origin", "main", GOOD_PR["headRefName"]])
        merge = runner.find("gh", "pr", "merge")
        self.assertEqual(len(merge), 1)
        self.assertEqual(merge[0][0][-2:], ["--match-head-commit", OID])
        names = [c[:2] for c, _ in runner.calls]
        self.assertLess(names.index(["git", "fetch"]), names.index(["git", "worktree"]))


class MergedTreeGatesTests(unittest.TestCase):
    def test_merges_the_exact_oid_in_a_temp_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            runner = FakeRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(team_gates.merged_tree_gates(repo, OID, runner=runner))
            merges = runner.find("git", "merge")
            self.assertEqual(merges[0][0], ["git", "merge", "--no-edit", "--no-ff", OID])
            self.assertNotEqual(merges[0][1], repo)
            self.assertEqual(runner.find("git", "worktree", "add")[0][0][-1], "origin/main")


class RevertByPrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name).resolve() / "owner-checkout"
        self.repo.mkdir()

    def revert(self, rules=()):
        runner = FakeRunner(rules)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = team_gates.revert_by_pr(self.repo, 9, "c" * 40, runner=runner)
        return code, out.getvalue(), runner

    def test_runs_in_temp_worktree_and_deletes_branch_after_push(self):
        code, out, runner = self.revert(
            [(lambda c: c[:3] == ["gh", "pr", "create"], 0, "https://example.invalid/pr/10\n")])
        self.assertEqual(code, 0, out)
        self.assertIn("REVERTING", out)
        for prefix in (("git", "checkout"), ("git", "revert"), ("git", "push")):
            cwd = runner.find(*prefix)[0][1]
            self.assertNotEqual(cwd, self.repo)
            self.assertNotIn(self.repo, cwd.parents)
        delete = runner.find("git", "branch", "-D")
        self.assertEqual(len(delete), 1)
        self.assertEqual(delete[0][1], self.repo)
        names = [c[:3] for c, _ in runner.calls]
        self.assertLess(names.index(["git", "worktree", "remove"]), names.index(["git", "branch", "-D"]))

    def test_git_failure_returns_nonzero_with_revert_failed_and_cleans_branch(self):
        code, out, runner = self.revert([(lambda c: c[:2] == ["git", "revert"], 1, "")])
        self.assertNotEqual(code, 0)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("REVERT FAILED", out)
        self.assertFalse(runner.find("git", "push"))
        self.assertTrue(runner.find("git", "worktree", "remove"))
        self.assertTrue(runner.find("git", "branch", "-D"))


class WorktreeCleanupTests(unittest.TestCase):
    def test_keeps_moved_branch_and_deletes_merged_tip(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {repo}/gone-keep\nHEAD {'1' * 40}\nbranch refs/heads/automation/keep\n\n"
                         f"worktree {repo}/gone-del\nHEAD {'2' * 40}\nbranch refs/heads/automation/del\n")
            merged = json.dumps([{"number": 1, "headRefOid": "9" * 40}])
            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["gh", "pr"], 0, merged),
                (lambda c: c == ["git", "rev-parse", "automation/keep"], 0, "1" * 40 + "\n"),
                (lambda c: c == ["git", "rev-parse", "automation/del"], 0, "9" * 40 + "\n"),
                # Ancestor check: 1111... is NOT ancestor of 9999...
                (lambda c: c[:3] == ["git", "merge-base", "--is-ancestor"] and c[3] == "1" * 40, 1, ""),
            ])
            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner)
            deleted = [c[-1] for c, _ in runner.find("git", "branch", "-D")]
            self.assertEqual(deleted, ["automation/del"])


class WatchdogWaitingOwnerTests(unittest.TestCase):
    def test_three_takeovers_then_waiting_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            team, repo = Path(tmp) / "team", Path(tmp) / "repo"
            repo.mkdir()
            team_kit.save_plan(team, plan({"id": "a", "state": "next"}), NOW)
            results = [team_kit.watchdog_check(repo, team, NOW + timedelta(minutes=61 * i))
                       for i in range(4)]
            self.assertEqual([r.split()[0] for r in results[:3]], ["TAKEOVER"] * 3)
            self.assertIn("WAITING owner", results[3])
            self.assertIn("3 takeovers", results[3])


class SaveReviewTests(unittest.TestCase):
    def test_writes_head_first_line_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            team = Path(tmp) / "team"
            path = team_kit.save_review(team, 3, "code-reviewer", OID.upper(), "\nok\nVERDICT: APPROVE\n")
            self.assertEqual(path.name, "pr-3-code-reviewer.md")
            self.assertEqual(path.read_text().splitlines()[0], f"HEAD: {OID}")
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(review_verdicts(team / "reviews", 3, OID), {"code-reviewer": "APPROVE"})

    def test_bad_input_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            team = Path(tmp) / "team"
            for args in ((3, "../x", OID, "VERDICT: APPROVE"), (3, "code-reviewer", "not-hex", "x"),
                         (3, "code-reviewer", OID, "  "), (0, "code-reviewer", OID, "x")):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    team_kit.save_review(team, *args)

    def test_cli_reads_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("sys.stdin", io.StringIO("VERDICT: CLEAR\n")), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = team_kit.main(["--private-root", tmp, "review", "--pr", "4",
                                      "--role", "safety-scanner", "--head", OID])
            self.assertEqual(code, 0)
            saved = Path(tmp) / "team" / "reviews" / "pr-4-safety-scanner.md"
            self.assertTrue(saved.read_text().startswith(f"HEAD: {OID}\n"))


class HeadLineFirstOnlyTests(unittest.TestCase):
    def test_head_line_counts_only_as_first_non_empty_line(self):
        self.assertFalse(is_review_stale(f"\n\nHEAD: {OID}\nVERDICT: APPROVE", OID))
        self.assertTrue(is_review_stale(f"notes\nHEAD: {OID}\nVERDICT: APPROVE", OID))
        self.assertTrue(is_review_stale(f"HEAD: {'b' * 40}\nHEAD: {OID}\nVERDICT: APPROVE", OID))
        self.assertTrue(is_review_stale(f"HEAD: {OID}", ""))


class RequiredSecurityReviewerTests(unittest.TestCase):
    def test_more_sensitive_paths_need_security_reviewer(self):
        for name in (".claude/settings.local.json", ".claude/agents/producer.md",
                     ".claude/workflows/feature.js", "workshop_validator/public_safety.py",
                     "workshop_validator/team_state.py", "workshop_validator/team_kit.py"):
            with self.subTest(name=name):
                self.assertIn("security-reviewer", required_reviewers([name]))
        self.assertNotIn("security-reviewer", required_reviewers(["workshop_validator/pdf_inline.py"]))


class WriteGuardF2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home, self.repo = base / "home", base / "repo"
        self.tree = self.repo / ".claude" / "worktrees" / "agent-1"
        self.lookalike = base / "x" / ".claude" / "worktrees" / "fake"
        for d in (self.home, self.tree, self.lookalike):
            d.mkdir(parents=True)

    def decide(self, target, cwd, root):
        return guard_decision({"cwd": str(cwd), "tool_input": {"file_path": str(target)}},
                              home=self.home, git_root=lambda c: root, scratch=[],
                              main_root=lambda c: self.repo)[0]

    def test_reviews_folder_denied_for_every_session(self):
        reviews = self.home / ".workshop-validator" / "team" / "reviews"
        self.assertFalse(self.decide(reviews / "pr-1-code-reviewer.md", self.repo, self.repo))
        self.assertFalse(self.decide(reviews / "pr-1-code-reviewer.md", self.tree, self.tree))
        self.assertTrue(self.decide(self.home / ".workshop-validator" / "team" / "briefs" / "b.md",
                                    self.repo, self.repo))

    def test_more_files_protected_in_main_checkout_only(self):
        for rel in ("workshop_validator/team_kit.py", ".claude/agents/producer.md",
                    ".claude/workflows/feature.js"):
            with self.subTest(rel=rel):
                self.assertFalse(self.decide(self.repo / rel, self.repo, self.repo))
                self.assertTrue(self.decide(self.tree / rel, self.tree, self.tree))
        self.assertTrue(self.decide(self.repo / "workshop_validator" / "pdf_inline.py", self.repo, self.repo))

    def test_worktree_test_uses_path_containment_not_substring(self):
        # A checkout whose path merely contains /.claude/worktrees/ is not an agent worktree.
        self.assertFalse(self.decide(self.lookalike / ".claude" / "settings.json", self.lookalike, self.lookalike))
        self.assertFalse(team_hooks.is_agent_worktree(self.repo / ".claude" / "worktrees", self.repo))
        self.assertTrue(team_hooks.is_agent_worktree(self.tree, self.repo))
        self.assertFalse(team_hooks.is_agent_worktree(self.tree, None))


NODE = shutil.which("node")
FEATURE_JS = Path(__file__).resolve().parents[1] / ".claude" / "workflows" / "feature.js"


class FeatureWorkflowFindingsTests(unittest.TestCase):
    def source(self):
        text = FEATURE_JS.read_text(encoding="utf-8")
        return text[text.index("// BEGIN collectFindings"):text.index("// END collectFindings")]

    def test_missing_reviewer_is_named_in_source(self):
        self.assertIn("missing reviewer: ${role}", self.source())

    @unittest.skipUnless(NODE, "node not installed")
    def test_collect_findings_never_empty_when_not_ready(self):
        approve = {"verdict": "APPROVE", "findings": []}
        green = {"green": True, "failures": []}
        cases = [
            (green, [approve, None], "missing reviewer: rule-auditor"),
            (green, [None, None], "missing reviewer: code-reviewer"),
            (green, [{"verdict": "VETO", "findings": []}, approve], "code-reviewer: VETO"),
            ({"green": False, "failures": []}, [approve, approve], "test: not green"),
            (None, [approve, approve], "test-runner returned nothing"),
            (green, [approve, approve], None),
        ]
        script = self.source() + (
            "const cases = JSON.parse(process.argv[1]);"
            "console.log(JSON.stringify(cases.map(([t, r]) => "
            "collectFindings(t, r, ['code-reviewer', 'rule-auditor']))));")
        out = subprocess.run([NODE, "-e", script, json.dumps([[t, r] for t, r, _ in cases])],
                             capture_output=True, text=True, check=True).stdout
        for (_, _, expected), got in zip(cases, json.loads(out)):
            with self.subTest(expected=expected):
                if expected is None:
                    self.assertTrue(got["ready"])
                    self.assertEqual(got["findings"], [])
                else:
                    self.assertFalse(got["ready"])
                    self.assertTrue(any(expected in f for f in got["findings"]), got["findings"])


# ---- F3: write-guard and cleanup follow-ups from PR #1 reviews ------------


class WriteGuardCaseFoldingTests(unittest.TestCase):
    """Finding 1: Write guard should be case-insensitive on case-insensitive filesystems."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home, self.repo = base / "home", base / "repo"
        self.tree = self.repo / ".claude" / "worktrees" / "agent-1"
        for d in (self.home, self.repo, self.tree):
            d.mkdir(parents=True)

    def decide(self, target, cwd=None, root="auto"):
        cwd = cwd or self.repo
        lookup = (lambda c: self.tree if Path(c).resolve() == self.tree else self.repo) if root == "auto" else (lambda c: root)
        return guard_decision({"cwd": str(cwd), "tool_input": {"file_path": str(target)}},
                              home=self.home, git_root=lookup, scratch=[],
                              main_root=lambda c: self.repo)[0]

    def test_case_variants_blocked_in_never_write(self):
        """Verification-Orgs.json should be blocked (case variant of verification-orgs.json)."""
        self.assertFalse(self.decide(self.repo / "Verification-Orgs.json"))
        self.assertFalse(self.decide(self.repo / "VERIFICATION-ORGS.JSON"))
        self.assertFalse(self.decide(self.repo / "Browser-State.json"))

    def test_case_variants_blocked_in_protected_files(self):
        """Team_Hooks.py should be blocked from main checkout (case variant)."""
        self.assertFalse(self.decide(self.repo / "workshop_validator" / "Team_Hooks.py"))
        self.assertFalse(self.decide(self.repo / "workshop_validator" / "TEAM_HOOKS.PY"))
        self.assertFalse(self.decide(self.repo / "workshop_validator" / "team_gates.PY"))

    def test_case_variants_blocked_in_settings(self):
        """.CLAUDE/settings.json should be blocked from main (case variant of .claude)."""
        self.assertFalse(self.decide(self.repo / ".CLAUDE" / "settings.json"))
        self.assertFalse(self.decide(self.repo / ".Claude" / "SETTINGS.JSON"))

    def test_case_variants_blocked_in_reviews_path(self):
        """team/REVIEWS/pr-1-x.md should be blocked (case variant of reviews)."""
        reviews = self.home / ".workshop-validator" / "team" / "REVIEWS"
        reviews.mkdir(parents=True, exist_ok=True)
        self.assertFalse(self.decide(reviews / "pr-1-x.md"))
        self.assertFalse(self.decide(self.home / ".WORKSHOP-VALIDATOR" / "team" / "reviews" / "pr-2-x.md"))


class ProtectedFilesF3Tests(unittest.TestCase):
    """Finding 2: team_gates.py, team_state.py, watchdog.py should be protected in main checkout."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.home, self.repo = base / "home", base / "repo"
        self.tree = self.repo / ".claude" / "worktrees" / "agent-1"
        for d in (self.home, self.repo, self.tree):
            d.mkdir(parents=True)

    def decide(self, target, cwd, root):
        return guard_decision({"cwd": str(cwd), "tool_input": {"file_path": str(target)}},
                              home=self.home, git_root=lambda c: root, scratch=[],
                              main_root=lambda c: self.repo)[0]

    def test_team_gates_protected_from_main_checkout(self):
        """team_gates.py cannot be written from main checkout."""
        self.assertFalse(self.decide(self.repo / "workshop_validator" / "team_gates.py", self.repo, self.repo))
        # But can be written from worktree
        self.assertTrue(self.decide(self.tree / "workshop_validator" / "team_gates.py", self.tree, self.tree))

    def test_team_state_protected_from_main_checkout(self):
        """team_state.py cannot be written from main checkout."""
        self.assertFalse(self.decide(self.repo / "workshop_validator" / "team_state.py", self.repo, self.repo))
        self.assertTrue(self.decide(self.tree / "workshop_validator" / "team_state.py", self.tree, self.tree))

    def test_watchdog_protected_from_main_checkout(self):
        """watchdog.py cannot be written from main checkout."""
        self.assertFalse(self.decide(self.repo / "workshop_validator" / "watchdog.py", self.repo, self.repo))
        self.assertTrue(self.decide(self.tree / "workshop_validator" / "watchdog.py", self.tree, self.tree))


class ReviewVerdictsFailClosedTests(unittest.TestCase):
    """Finding 3: review_verdicts must fail closed when head_oid is empty."""

    def test_empty_head_oid_marks_passing_verdicts_stale(self):
        """When head_oid is empty or None, all PASS verdicts should be STALE."""
        with tempfile.TemporaryDirectory() as tmp:
            reviews = Path(tmp)
            (reviews / "pr-5-code-reviewer.md").write_text("HEAD: abc123\nVERDICT: APPROVE")
            (reviews / "pr-5-safety-scanner.md").write_text("HEAD: def456\nVERDICT: CLEAR")

            # With empty string
            verdicts = review_verdicts(reviews, 5, head_oid="")
            self.assertEqual(verdicts["code-reviewer"], "STALE")
            self.assertEqual(verdicts["safety-scanner"], "STALE")

            # With None
            verdicts = review_verdicts(reviews, 5, head_oid=None)
            self.assertEqual(verdicts["code-reviewer"], "STALE")
            self.assertEqual(verdicts["safety-scanner"], "STALE")

    def test_whitespace_head_oid_marks_passing_verdicts_stale(self):
        """Whitespace-only head_oid should also mark verdicts stale."""
        with tempfile.TemporaryDirectory() as tmp:
            reviews = Path(tmp)
            (reviews / "pr-6-code-reviewer.md").write_text("HEAD: abc123\nVERDICT: APPROVE")
            verdicts = review_verdicts(reviews, 6, head_oid="  ")
            self.assertEqual(verdicts["code-reviewer"], "STALE")


class WriteGuardF3Fix1Tests(unittest.TestCase):
    """F3-fix1: split allow and deny containment in the write guard."""

    def test_case_sensitive_layout_denies_miscased_workspace(self):
        """Workspace base/repo does not grant writes to base/REPO/x.py (no mocks).

        On a case-sensitive volume base/REPO is a different folder; on a
        case-insensitive one it is the same folder spelled differently. Either
        way an allow decision must not depend on casefold.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            home.mkdir()
            repo = base / "repo"
            repo.mkdir()
            (base / "REPO").mkdir(exist_ok=True)  # same folder on case-insensitive volumes
            target = base / "REPO" / "x.py"
            allowed, reason = guard_decision(
                {"cwd": str(repo), "tool_input": {"file_path": str(target)}},
                home=home, git_root=lambda c: repo, scratch=[],
                main_root=lambda c: None)
            self.assertFalse(allowed)
            self.assertIn("outside this agent's workspace", reason)

    def test_inside_allow_is_case_exact(self):
        """_inside_allow never matches a path that differs from the root only in case."""
        base = Path(tempfile.gettempdir()).resolve() / "wv-case-helper"
        self.assertFalse(team_hooks._inside_allow(base / "REPO" / "x.py", base / "repo"))
        self.assertTrue(team_hooks._inside_allow(base / "repo" / "x.py", base / "repo"))

    def test_inside_deny_is_case_insensitive(self):
        """_inside_deny matches a path that differs from the root only in case."""
        base = Path(tempfile.gettempdir()).resolve() / "wv-case-helper"
        self.assertTrue(team_hooks._inside_deny(base / "REPO" / "x.py", base / "repo"))
        self.assertFalse(team_hooks._inside_deny(base / "repox" / "x.py", base / "repo"))

    def test_miscased_root_denied_with_clear_reason(self):
        """Miscased root target should be denied with clear reason, not ValueError."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            repo = base / "repo"
            repo.mkdir(parents=True)
            home.mkdir(parents=True)

            # Directly test the scenario where relative_to would fail
            # Mock to make _inside_allow return True but Path.relative_to raise ValueError
            class PathWithFailingRelativeTo(type(Path())):
                """Path that fails relative_to to simulate case mismatch."""
                def __init__(self, *args):
                    self._path = Path(*args)

                def __truediv__(self, other):
                    return self._path / other

                def resolve(self):
                    return self._path.resolve()

                def relative_to(self, other):
                    # Simulate case-sensitive FS where this would fail
                    raise ValueError("path case mismatch")

                @property
                def name(self):
                    return self._path.name

            # Create target that will fail relative_to
            failing_target = PathWithFailingRelativeTo(repo / "x.py")

            # Patch _resolve_target to return our special path
            def mock_resolve_target(raw, cwd):
                return failing_target

            # Mock _inside_allow to return True (so we enter the in_own_workspace block)
            with mock.patch.object(team_hooks, '_resolve_target', mock_resolve_target):
                with mock.patch.object(team_hooks, '_inside_allow', return_value=True):
                    with mock.patch.object(team_hooks, '_inside_deny', return_value=False):
                        allowed, reason = guard_decision(
                            {"cwd": str(repo), "tool_input": {"file_path": "x.py"}},
                            home=home, git_root=lambda c: repo, scratch=[]
                        )

                        self.assertFalse(allowed)
                        # Should have clear error message about case mismatch
                        self.assertIn("case", reason.lower())
                        # Should not expose ValueError in user-facing message
                        self.assertNotIn("ValueError", reason)

    def test_case_variants_still_denied(self):
        """Case variants of protected names should still be denied (existing tests stay green)."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp).resolve()
            home = base / "home"
            repo = base / "repo"
            repo.mkdir(parents=True)
            home.mkdir(parents=True)

            # These tests verify the existing case-insensitive protection works
            def decide(target):
                return guard_decision(
                    {"cwd": str(repo), "tool_input": {"file_path": str(target)}},
                    home=home, git_root=lambda c: repo, scratch=[]
                )[0]

            # Case variants of NEVER_WRITE should be denied
            self.assertFalse(decide(repo / "Verification-Orgs.json"))
            self.assertFalse(decide(repo / "BROWSER-STATE.JSON"))

            # Case variants of protected paths should be denied from main
            self.assertFalse(decide(repo / "workshop_validator" / "TEAM_HOOKS.PY"))
            self.assertFalse(decide(repo / ".CLAUDE" / "settings.json"))

            # Reviews folder with case variants should be denied
            reviews = home / ".workshop-validator" / "team" / "REVIEWS"
            reviews.mkdir(parents=True, exist_ok=True)
            self.assertFalse(decide(reviews / "pr-1-x.md"))


class WorktreeCleanupAncestorTests(unittest.TestCase):
    """Finding 4: worktree_cleanup should treat branch as merged when tip is ancestor of merged PR."""

    def test_deletes_when_branch_tip_is_ancestor_of_merged_pr(self):
        """Branch should be deleted when its tip is an ancestor of a merged PR's headRefOid."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            branch_oid = "1" * 40
            ancestor_oid = "a" * 40  # branch_oid is ancestor of this
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {repo}/wt\nHEAD {branch_oid}\nbranch refs/heads/automation/feature\n")
            merged = json.dumps([{"number": 10, "headRefOid": ancestor_oid}])

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["gh", "pr"], 0, merged),
                (lambda c: c == ["git", "rev-parse", "automation/feature"], 0, branch_oid + "\n"),
                # Fetch the PR oid
                (lambda c: c == ["git", "fetch", "origin", ancestor_oid], 0, ""),
                # merge-base --is-ancestor returns 0 when true
                (lambda c: c == ["git", "merge-base", "--is-ancestor", branch_oid, ancestor_oid], 0, ""),
            ])
            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner)

            # Should have deleted the branch
            deleted = [c[-1] for c, _ in runner.find("git", "branch", "-D")]
            self.assertIn("automation/feature", deleted)

    def test_keeps_when_branch_tip_not_ancestor(self):
        """Branch should be kept when tip is neither exact match nor ancestor of merged PR."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            branch_oid = "1" * 40
            unrelated_oid = "9" * 40
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {repo}/wt\nHEAD {branch_oid}\nbranch refs/heads/automation/other\n")
            merged = json.dumps([{"number": 11, "headRefOid": unrelated_oid}])

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["gh", "pr"], 0, merged),
                (lambda c: c == ["git", "rev-parse", "automation/other"], 0, branch_oid + "\n"),
                (lambda c: c == ["git", "fetch", "origin", unrelated_oid], 0, ""),
                # merge-base --is-ancestor returns 1 when false
                (lambda c: c == ["git", "merge-base", "--is-ancestor", branch_oid, unrelated_oid], 1, ""),
            ])
            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner)

            # Should NOT have deleted the branch
            deleted = [c[-1] for c, _ in runner.find("git", "branch", "-D")]
            self.assertEqual(deleted, [])


# ---- F6: team tooling fixes ------------------------------------------------


class TeamKitDecideTests(unittest.TestCase):
    """F6 item 1: team_kit decide command appends register row and renders dashboard."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.team = Path(self.tmp.name) / "team"
        self.team.mkdir()
        # Create a minimal plan
        team_kit.save_plan(self.team, plan({"id": "a", "state": "done"}), NOW)

    def test_decide_appends_row_and_renders_dashboard(self):
        """decide command should append one row and render dashboard in same call."""
        register = self.team / "DECISIONS.md"
        dashboard = self.team / "dashboard.html"

        # Run decide command
        code = team_kit.main(["--private-root", str(self.team.parent), "decide",
                              "--id", "W-001", "--status", "NOTED",
                              "--text", "Synthetic decision for test"])

        self.assertEqual(code, 0)
        self.assertTrue(register.exists())
        self.assertIn("W-001", register.read_text())
        self.assertTrue(dashboard.exists())
        self.assertIn("W-001", dashboard.read_text())

    def test_decide_refuses_duplicate_id(self):
        """decide should refuse a duplicate id."""
        register = self.team / "DECISIONS.md"
        register.parent.mkdir(parents=True, exist_ok=True)
        register.write_text("| W-002 | 2026-10-27 | NOTED | First |\n")

        code = team_kit.main(["--private-root", str(self.team.parent), "decide",
                              "--id", "W-002", "--status", "NOTED",
                              "--text", "Duplicate"])

        self.assertNotEqual(code, 0)

    def test_decide_refuses_bad_status(self):
        """decide should refuse an invalid status."""
        code = team_kit.main(["--private-root", str(self.team.parent), "decide",
                              "--id", "W-003", "--status", "INVALID",
                              "--text", "Bad status"])

        self.assertNotEqual(code, 0)


class SavePlanDuplicateStepTests(unittest.TestCase):
    """F6 item 2: save_plan rejects duplicate step ids."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.team = Path(self.tmp.name) / "team"

    def test_save_plan_rejects_duplicate_step_ids(self):
        """save_plan should raise ValueError when duplicate step ids are present."""
        p = plan({"id": "a", "state": "next"}, {"id": "a", "state": "done"})
        with self.assertRaises(ValueError) as ctx:
            team_kit.save_plan(self.team, p)
        self.assertIn("duplicate", str(ctx.exception).lower())


class MergeWhenGreenF6Tests(unittest.TestCase):
    """F6 item 3: merge_when_green reads PR state after non-zero gh pr merge."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.team = Path(self.tmp.name) / "team"
        self.repo.mkdir()
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(self.team, 5, role, OID, f"notes\nVERDICT: {verdict}")

    def test_merge_reads_state_after_nonzero_gh_pr_merge(self):
        """After non-zero gh pr merge, read PR state; if MERGED, continue."""
        merge_oid = "m" * 40
        runner = FakeRunner([
            # First pr view call in merge_when_green
            (lambda c: c[:3] == ["gh", "pr", "view"] and "headRefOid" in c and "mergeCommit" not in c, 0,
             json.dumps(GOOD_PR)),
            # pr checks call
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            # fetch call
            (lambda c: c[:2] == ["git", "fetch"], 0, ""),
            # merge-base call for merged_tree_gates
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            # worktree add/remove for merged_tree_gates
            (lambda c: c[:3] == ["git", "worktree", "add"], 0, ""),
            (lambda c: c[:2] == ["git", "merge"], 0, ""),
            (lambda c: c[:3] == ["git", "worktree", "remove"], 0, ""),
            # prove_tests - use mock.patch instead
            # gh pr merge - returns non-zero
            (lambda c: c[:3] == ["gh", "pr", "merge"], 1, ""),
            # Second pr view call after non-zero merge (with state check)
            (lambda c: c[:3] == ["gh", "pr", "view"] and ("state" in c or "mergeCommit" in c), 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}, "state": "MERGED"})),
            # run list for CI check
            (lambda c: c[:3] == ["gh", "run", "list"], 0,
             json.dumps([{"headSha": merge_oid, "status": "completed", "conclusion": "success"}])),
            # git push to delete branch
            (lambda c: c[:3] == ["git", "push", "origin"], 0, ""),
        ])
        out = io.StringIO()
        with mock.patch.object(team_gates, "prove_tests", return_value=True):
            with contextlib.redirect_stdout(out):
                code = team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should continue and succeed since PR is MERGED
        self.assertEqual(code, 0)
        self.assertIn("MERGED", out.getvalue())

    def test_merge_deletes_remote_branch_after_merge_not_local_in_worktree(self):
        """Should delete remote branch with git push origin --delete after merge, not local in worktree."""
        merge_oid = "m" * 40
        runner = FakeRunner([
            # pr view calls
            (lambda c: c[:3] == ["gh", "pr", "view"], 0, json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
            (lambda c: c[:2] == ["git", "fetch"], 0, ""),
            (lambda c: c[:2] == ["git", "merge-base"], 0, "c" * 40 + "\n"),
            (lambda c: c[:3] == ["git", "worktree", "add"], 0, ""),
            (lambda c: c[:2] == ["git", "merge"], 0, ""),
            (lambda c: c[:3] == ["git", "worktree", "remove"], 0, ""),
            (lambda c: c[:3] == ["gh", "pr", "merge"], 0, ""),
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in c, 0,
             json.dumps({"mergeCommit": {"oid": merge_oid}})),
            (lambda c: c[:3] == ["gh", "run", "list"], 0,
             json.dumps([{"headSha": merge_oid, "status": "completed", "conclusion": "success"}])),
            (lambda c: c[:3] == ["git", "push", "origin"], 0, ""),
        ])

        with mock.patch.object(team_gates, "prove_tests", return_value=True):
            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.merge_when_green(self.repo, self.team, 5, runner=runner)

        # Should NOT use --delete-branch flag
        merge_calls = runner.find("gh", "pr", "merge")
        self.assertEqual(len(merge_calls), 1)
        self.assertNotIn("--delete-branch", merge_calls[0][0])

        # Should use git push origin --delete after merge
        deletes = [c for c, _ in runner.calls if c[:2] == ["git", "push"] and "--delete" in c]
        self.assertEqual(len(deletes), 1)


class WorktreeCleanupF6Tests(unittest.TestCase):
    """F6 item 4: worktree_cleanup handles worktree-agent-* branches and origin/main ancestors."""

    def test_worktree_agent_branches_never_match_pr_head(self):
        """Worktree-agent-* branches should be checked against origin/main ancestry."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            branch_oid = "1" * 40
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {repo}/wt\nHEAD {branch_oid}\nbranch refs/heads/worktree-agent-abc123\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["gh", "pr"] and "--head" in c, 0, "[]"),  # No PR for this branch
                (lambda c: c == ["git", "rev-parse", "worktree-agent-abc123"], 0, branch_oid + "\n"),
                # Check if it's ancestor of origin/main
                (lambda c: c == ["git", "merge-base", "--is-ancestor", branch_oid, "origin/main"], 0, ""),
            ])
            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner)

            # Should have deleted the worktree
            deleted = [c[-1] for c, _ in runner.find("git", "branch", "-D")]
            self.assertIn("worktree-agent-abc123", deleted)


class ValidateOidF6Tests(unittest.TestCase):
    """F6 item 5: validate every oid from gh as 40 hex before using in git commands."""

    def test_invalid_oid_rejected_in_merge_when_green(self):
        """Invalid oid from gh should be rejected before git commands."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            team = Path(tmp) / "team"
            repo.mkdir(parents=True, exist_ok=True)

            invalid_pr = dict(GOOD_PR, headRefOid="not-a-valid-oid")
            runner = FakeRunner([
                (lambda c: c[:3] == ["gh", "pr", "view"], 0, json.dumps(invalid_pr)),
            ])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = team_gates.merge_when_green(repo, team, 5, runner=runner)

            self.assertEqual(code, 2)
            self.assertIn("REFUSED", out.getvalue())

    def test_validates_oids_in_worktree_cleanup(self):
        """Worktree cleanup should validate oids from gh pr list."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            branch_oid = "1" * 40
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {repo}/wt\nHEAD {branch_oid}\nbranch refs/heads/automation/feature\n")
            # Invalid oid from gh pr list
            merged = json.dumps([{"number": 10, "headRefOid": "invalid-oid"}])

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["gh", "pr"], 0, merged),
                (lambda c: c == ["git", "rev-parse", "automation/feature"], 0, branch_oid + "\n"),
            ])

            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner)

            # Should not have tried to use the invalid oid
            fetches = runner.find("git", "fetch", "origin")
            for cmd, _ in fetches:
                self.assertNotIn("invalid-oid", cmd)


class WorktreeCleanupKeepDirtyF6Tests(unittest.TestCase):
    """F6 item 8: cleanup tests for dirty/recently active worktrees and miscased root."""

    def test_keeps_dirty_worktree(self):
        """Dirty worktrees should be kept even if old and merged."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "wt"
            wt.mkdir(parents=True)
            branch_oid = "1" * 40
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/feature\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, "M file.txt\n"),  # Dirty
                (lambda c: c[:2] == ["gh", "pr"], 0, json.dumps([{"number": 10, "headRefOid": branch_oid}])),
                (lambda c: c == ["git", "rev-parse", "automation/feature"], 0, branch_oid + "\n"),
            ])

            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0)

            # Should NOT have deleted dirty worktree
            deleted = [c[-1] for c, _ in runner.find("git", "branch", "-D")]
            self.assertEqual(deleted, [])

    def test_keeps_recently_active_worktree(self):
        """Recently active worktrees should be kept."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "wt"
            wt.mkdir(parents=True)
            # Create a recently modified file
            (wt / "recent.txt").write_text("active")
            os.utime(wt / "recent.txt", (time.time(), time.time()))

            branch_oid = "1" * 40
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/feature\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),  # Clean
                (lambda c: c[:2] == ["gh", "pr"], 0, json.dumps([{"number": 10, "headRefOid": branch_oid}])),
                (lambda c: c == ["git", "rev-parse", "automation/feature"], 0, branch_oid + "\n"),
            ])

            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=1.0)

            # Should NOT have deleted recently active worktree
            deleted = [c[-1] for c, _ in runner.find("git", "branch", "-D")]
            self.assertEqual(deleted, [])

    def test_denies_miscased_root(self):
        """Worktrees should be kept when ancestry cannot be proven."""
        # This test verifies that worktrees are kept when not provably merged
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp).resolve()
            wt = repo / "wt"
            wt.mkdir(parents=True)
            branch_oid = "1" * 40
            unrelated_oid = "9" * 40  # Different from branch
            porcelain = (f"worktree {repo}\nHEAD {'0' * 40}\nbranch refs/heads/main\n\n"
                         f"worktree {wt}\nHEAD {branch_oid}\nbranch refs/heads/automation/feature\n")

            runner = FakeRunner([
                (lambda c: c[:3] == ["git", "worktree", "list"], 0, porcelain),
                (lambda c: c[:2] == ["git", "status"] and "--porcelain" in c, 0, ""),
                (lambda c: c[:2] == ["gh", "pr"], 0, json.dumps([{"number": 10, "headRefOid": unrelated_oid}])),
                (lambda c: c == ["git", "rev-parse", "automation/feature"], 0, branch_oid + "\n"),
                (lambda c: c[:2] == ["git", "fetch"], 0, ""),
                # Simulate ancestor check failure for PR oid
                (lambda c: c[:3] == ["git", "merge-base", "--is-ancestor"] and c[3] == branch_oid and c[4] == unrelated_oid, 1, ""),
                # Simulate ancestor check failure for origin/main
                (lambda c: c[:3] == ["git", "merge-base", "--is-ancestor"] and c[4] == "origin/main", 1, ""),
            ])

            with contextlib.redirect_stdout(io.StringIO()):
                team_gates.worktree_cleanup(repo, runner=runner, idle_hours=0.0)

            # Should not have deleted unproven worktree
            deleted = [c[-1] for c, _ in runner.find("git", "branch", "-D")]
            self.assertEqual(deleted, [])


MB = "m" * 40
HEAD = "h" * 40


def _feature_diff(*methods, header="class FeatureTests(unittest.TestCase):"):
    """Synthetic diff adding test methods to an existing class whose line is outside the hunk."""
    body = "".join(f"+\n+    def {m}(self):\n+        self.assertTrue(feature.ok())\n" for m in methods)
    return ("diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
            "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n"
            f"@@ -40,3 +40,{3 + 3 * len(methods)} @@ {header}\n"
            "     def test_old(self):\n         pass\n \n" + body)


class ProveRunner(FakeRunner):
    """FakeRunner that fakes the temp worktree and answers the runner module with JSON.

    `git worktree add` writes "# head code" for each code file (none for `head_missing`).
    `outcome(swapped)` gets the set of code files not at their head version during that
    run and returns {test_id: status}. `snapshots` records {file: content or None} per run.
    """

    HEAD_CODE = "# head code\n"

    def __init__(self, code_files, outcome, rules=(), runner_code=0, base_missing=(), head_missing=()):
        base = [
            (lambda c: c[:2] == ["git", "merge-base"], 0, MB + "\n"),
            (lambda c: c[:3] == ["git", "diff", "--name-only"], 0, "".join(f + "\n" for f in code_files)),
            (lambda c: c[:2] == ["git", "show"] and c[2].split(":", 1)[1] in base_missing, 128, ""),
            (lambda c: c[:2] == ["git", "show"], 0, "# base code\n"),
        ]
        super().__init__(list(rules) + base)
        self.code_files, self.outcome, self.runner_code = code_files, outcome, runner_code
        self.head_missing, self.snapshots = set(head_missing), []

    @staticmethod
    def runner_index(cmd):
        """Index of the runner script in cmd, or None."""
        return next((i for i, a in enumerate(cmd[:3]) if i and a.endswith(team_gates.RUNNER_NAME)), None)

    def __call__(self, cmd, cwd):
        at = self.runner_index(cmd)
        if at is not None and len(cmd) > at + 1:
            self.calls.append((list(cmd), Path(cwd)))
            tree = Path(cmd[at]).parent
            snap = {f: (tree / f).read_text() if (tree / f).exists() else None for f in self.code_files}
            self.snapshots.append(snap)
            swapped = {f for f, text in snap.items()
                       if text != (None if f in self.head_missing else self.HEAD_CODE)}
            if self.runner_code == 0:
                Path(cmd[at + 1]).write_text(json.dumps(self.outcome(swapped)))
            return subprocess.CompletedProcess(cmd, self.runner_code, "", "")
        result = super().__call__(cmd, cwd)
        if cmd[:3] == ["git", "worktree", "add"] and result.returncode == 0:
            for f in self.code_files:
                if f not in self.head_missing:
                    (Path(cmd[-2]) / f).parent.mkdir(parents=True, exist_ok=True)
                    (Path(cmd[-2]) / f).write_text(self.HEAD_CODE)
        return result


class ProveTestsTests(unittest.TestCase):
    """Tests for the prove-tests gate (W-034). Synthetic diffs only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        self.repo.mkdir()

    def prove(self, runner, diff):
        runner.rules.insert(0, (lambda c: c[:2] == ["git", "diff"] and "--name-only" not in c, 0, diff))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = team_gates.prove_tests(self.repo, "origin/main", HEAD, runner=runner)
        return ok, out.getvalue()

    # item 1: parse_new_test_ids

    def test_parse_takes_class_from_hunk_header(self):
        ids = team_gates.parse_new_test_ids(_feature_diff("test_new_a", "test_new_b"))
        self.assertEqual(ids, ["tests.test_feature.FeatureTests.test_new_a",
                               "tests.test_feature.FeatureTests.test_new_b"])

    def test_parse_def_line_wins_over_comment_with_class(self):
        diff = ("diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
                "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n"
                "@@ -40,2 +40,4 @@ class FeatureTests(unittest.TestCase):\n"
                "     def test_old(self):\n         pass\n"
                "+    def test_lookup(self):  # class lookup by name\n"
                "+        self.assertTrue(feature.ok())\n")
        self.assertEqual(team_gates.parse_new_test_ids(diff),
                         ["tests.test_feature.FeatureTests.test_lookup"])

    def test_parse_finds_changed_test_body_and_skips_strings(self):
        diff = ("diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
                "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n"
                "@@ -10,6 +10,6 @@ class FeatureTests(unittest.TestCase):\n"
                "     def test_changed(self):\n"
                '         text = """\n'
                " class NotAClass:\n"
                ' """\n'
                "-        self.assertTrue(feature.ok(text))\n"
                "+        self.assertFalse(feature.ok(text))\n"
                "     def test_untouched(self):\n"
                "         pass\n")
        self.assertEqual(team_gates.parse_new_test_ids(diff),
                         ["tests.test_feature.FeatureTests.test_changed"])

    # item 2

    def test_code_change_without_new_test_is_unproven_not_skip(self):
        runner = ProveRunner(["workshop_validator/x.py"], lambda swapped: {})
        ok, out = self.prove(runner, "")
        self.assertFalse(ok)
        self.assertIn("UNPROVEN", out)
        self.assertNotIn("SKIP", out)
        self.assertIn("workshop_validator/x.py", out)

    def test_docs_only_change_skips(self):
        runner = ProveRunner([], lambda swapped: {})
        ok, out = self.prove(runner, "")
        self.assertTrue(ok)
        self.assertIn("SKIP no code change", out)

    # item 3: one file swapped at a time

    def test_each_code_file_needs_its_own_failing_test(self):
        files = ["workshop_validator/a.py", "workshop_validator/b.py"]
        tid = "tests.test_feature.FeatureTests.test_new_a"
        runner = ProveRunner(files, lambda swapped: {tid: "fail" if "workshop_validator/a.py" in swapped else "ok"})
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertFalse(ok)
        self.assertIn("UNPROVEN per file: workshop_validator/b.py 0 fail on base", out)
        self.assertIn("workshop_validator/a.py 1/1 fail on base", out)
        # head run, then one run per file with exactly that file at base
        head, base = "# head code\n", "# base code\n"
        self.assertEqual(runner.snapshots, [{files[0]: head, files[1]: head},
                                            {files[0]: base, files[1]: head},
                                            {files[0]: head, files[1]: base}])

    def test_pass_when_every_code_file_has_a_failing_test(self):
        files = ["workshop_validator/a.py", "workshop_validator/b.py"]
        ids = ["tests.test_feature.FeatureTests.test_new_a", "tests.test_feature.FeatureTests.test_new_b"]

        def outcome(swapped):
            if not swapped:
                return {i: "ok" for i in ids}
            return {ids[0]: "fail" if "workshop_validator/a.py" in swapped else "ok",
                    ids[1]: "error" if "workshop_validator/b.py" in swapped else "ok"}

        ok, out = self.prove(ProveRunner(files, outcome), _feature_diff("test_new_a", "test_new_b"))
        self.assertTrue(ok, out)
        self.assertIn("PASS", out)
        self.assertIn("workshop_validator/a.py 1/2 fail on base", out)
        self.assertIn("workshop_validator/b.py 1/2 fail on base", out)

    # item 4: structured results

    def test_results_come_from_runner_json_not_text(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        fake_text = f"test_new_a ({tid})\ndoc ... FAIL\n"
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {tid: "ok"},
                             rules=[(lambda c: "-m" in c and "unittest" in c, 1, fake_text)])
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertFalse(ok)
        self.assertIn("UNPROVEN", out)
        self.assertTrue(any(ProveRunner.runner_index(c) is not None for c, _ in runner.calls))

    def test_skip_and_expected_failure_on_base_are_unproven(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        for status in ("skip", "expectedFailure", "unexpectedSuccess", "missing"):
            with self.subTest(status=status):
                runner = ProveRunner(["workshop_validator/a.py"],
                                     lambda swapped, s=status: {tid: s if swapped else "ok"})
                ok, out = self.prove(runner, _feature_diff("test_new_a"))
                self.assertFalse(ok)
                self.assertIn("UNPROVEN", out)

    def test_skip_on_head_is_unproven(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {tid: "fail" if swapped else "skip"})
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertFalse(ok)
        self.assertIn("UNPROVEN new tests not ok on head", out)

    def test_runner_nonzero_exit_is_unproven(self):
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {}, runner_code=2)
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertFalse(ok)
        self.assertIn("UNPROVEN on head: test runner exited 2", out)

    def test_runner_module_writes_per_test_statuses(self):
        pkg = Path(self.tmp.name) / "tree"
        (pkg / "tests").mkdir(parents=True)
        (pkg / "tests" / "__init__.py").write_text("")
        (pkg / "workshop_validator").mkdir()  # imported from the tree, even via a symlinked tmp path
        (pkg / "workshop_validator" / "__init__.py").write_text("")
        (pkg / "tests" / "test_syn.py").write_text(
            "import unittest\nimport workshop_validator\n"
            "class Syn(unittest.TestCase):\n"
            "    def test_ok(self): pass\n"
            "    def test_fail(self): self.fail('x')\n"
            "    def test_error(self): raise RuntimeError('x')\n"
            "    def test_skip(self): self.skipTest('x')\n"
            "    @unittest.expectedFailure\n"
            "    def test_xfail(self): self.fail('x')\n"
            "    @unittest.expectedFailure\n"
            "    def test_xpass(self): pass\n"
            "    def test_subfail(self):\n"
            "        with self.subTest(i=1): self.fail('x')\n")
        (pkg / "tests" / "test_broken.py").write_text("import no_such_module_for_prove\n")
        (pkg / team_gates.RUNNER_NAME).write_text(team_gates.RUNNER_SOURCE)
        names = ["ok", "fail", "error", "skip", "xfail", "xpass", "subfail"]
        ids = [f"tests.test_syn.Syn.test_{n}" for n in names] + ["tests.test_broken.X.test_y"]
        out_json = Path(self.tmp.name) / "r.json"
        done = subprocess.run([sys.executable, "-I", str(pkg / team_gates.RUNNER_NAME), str(out_json), *ids],
                              cwd=pkg, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        got = json.loads(out_json.read_text())
        self.assertEqual([got[i] for i in ids],
                         ["ok", "fail", "error", "skip", "expectedFailure", "unexpectedSuccess",
                          "fail", "error"])

    # G1-fix3: runner hardening

    def test_runner_launched_isolated(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {tid: "fail" if swapped else "ok"})
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertTrue(ok, out)
        launches = [c for c, _ in runner.calls if ProveRunner.runner_index(c) is not None]
        self.assertEqual(len(launches), 2)
        for cmd in launches:
            self.assertEqual(cmd[1], "-I")
            self.assertTrue(cmd[2].endswith(team_gates.RUNNER_NAME))

    def test_runner_ignores_root_stdlib_shadows_and_appends_tree_last(self):
        pkg = Path(self.tmp.name) / "tree"
        (pkg / "tests").mkdir(parents=True)
        (pkg / "tests" / "__init__.py").write_text("")
        for name in ("json.py", "unittest.py"):
            (pkg / name).write_text("raise SystemExit(7)\n")
        (pkg / "tests" / "test_path.py").write_text(
            "import json, os, sys, unittest\n"
            "HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))\n"
            "class P(unittest.TestCase):\n"
            "    def test_tree_last(self):\n"
            "        self.assertEqual(os.path.realpath(sys.path[-1]), HERE)\n"
            "        self.assertNotIn(HERE, [os.path.realpath(p) for p in sys.path[:-1]])\n"
            "        self.assertFalse(os.path.realpath(json.__file__).startswith(HERE + os.sep))\n")
        (pkg / team_gates.RUNNER_NAME).write_text(team_gates.RUNNER_SOURCE)
        out_json = Path(self.tmp.name) / "r.json"
        tid = "tests.test_path.P.test_tree_last"
        done = subprocess.run([sys.executable, "-I", str(pkg / team_gates.RUNNER_NAME), str(out_json), tid],
                              cwd=pkg, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(json.loads(out_json.read_text()), {tid: "ok"})

    def test_root_stdlib_named_file_is_unproven(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        for name in sorted(team_gates.STDLIB_SHADOW_NAMES):
            with self.subTest(name=name):
                listing = f"{name}\nworkshop_validator/a.py\n"
                runner = ProveRunner(["workshop_validator/a.py"],
                                     lambda swapped: {tid: "fail" if swapped else "ok"},
                                     rules=[(lambda c: c[:3] == ["git", "diff", "--name-only"]
                                             and "workshop_validator/" not in c, 0, listing)])
                ok, out = self.prove(runner, _feature_diff("test_new_a"))
                self.assertFalse(ok)
                self.assertIn(f"UNPROVEN root file named like a stdlib module added or changed: {name}", out)
                self.assertFalse([c for c, _ in runner.calls if ProveRunner.runner_index(c) is not None])

    def test_nested_stdlib_named_file_is_not_refused(self):
        self.assertEqual(team_gates.stdlib_shadow_files("tests/json.py\ndocs/os.py\nREADME.md\n"), [])
        self.assertEqual(team_gates.stdlib_shadow_files("Json.py\nre.py\n"), ["Json.py", "re.py"])

    def test_runner_keys_must_equal_requested_ids(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        cases = {
            "extra": lambda swapped: {tid: "fail" if swapped else "ok", "tests.other.X.test_y": "ok"},
            "missing": lambda swapped: {},
            "missing on base": lambda swapped: {"tests.other.X.test_y": "fail"} if swapped else {tid: "ok"},
        }
        for label, outcome in cases.items():
            with self.subTest(case=label):
                ok, out = self.prove(ProveRunner(["workshop_validator/a.py"], outcome),
                                     _feature_diff("test_new_a"))
                self.assertFalse(ok)
                self.assertIn("test runner results do not match the requested tests", out)

    def test_parse_removal_only_diff_yields_no_ids(self):
        diff = ("diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
                "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n"
                "@@ -10,7 +10,3 @@ class FeatureTests(unittest.TestCase):\n"
                "     def test_keep(self):\n"
                "         self.assertTrue(feature.ok())\n"
                " \n"
                "-    def test_gone(self):\n"
                "-        self.assertTrue(feature.old())\n"
                "-\n"
                "-        self.assertTrue(feature.older())\n")
        self.assertEqual(team_gates.parse_new_test_ids(diff), [])

    def test_parse_removal_after_unchanged_test_does_not_list_it(self):
        diff = ("diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
                "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n"
                "@@ -10,4 +10,3 @@ class FeatureTests(unittest.TestCase):\n"
                "     def test_keep(self):\n"
                "         self.assertTrue(feature.ok())\n"
                "-        self.assertTrue(feature.extra())\n"
                "@@ -40,2 +39,5 @@ class FeatureTests(unittest.TestCase):\n"
                "     def test_old(self):\n"
                "         pass\n"
                "+\n"
                "+    def test_new(self):\n"
                "+        self.assertTrue(feature.new())\n")
        self.assertEqual(team_gates.parse_new_test_ids(diff),
                         ["tests.test_feature.FeatureTests.test_new"])

    def test_parse_real_body_edit_still_found_next_to_removals(self):
        diff = ("diff --git a/tests/test_feature.py b/tests/test_feature.py\n"
                "--- a/tests/test_feature.py\n+++ b/tests/test_feature.py\n"
                "@@ -10,6 +10,5 @@ class FeatureTests(unittest.TestCase):\n"
                "     def test_edited(self):\n"
                "-        self.assertTrue(feature.ok())\n"
                "+        self.assertFalse(feature.ok())\n"
                "     def test_trimmed(self):\n"
                "         pass\n"
                "-        pass\n")
        self.assertEqual(team_gates.parse_new_test_ids(diff),
                         ["tests.test_feature.FeatureTests.test_edited"])

    # item 5: new and empty files

    def test_new_file_import_error_on_base_is_proof(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        new = "workshop_validator/new_mod.py"

        runner = ProveRunner([new], lambda swapped: {tid: "error" if swapped else "ok"},
                             base_missing=(new,))
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertTrue(ok, out)
        self.assertIn(f"{new} 1/1 fail on base", out)
        self.assertEqual(runner.snapshots, [{new: "# head code\n"}, {new: None}])  # removed on base

    def test_empty_base_file_is_written_not_removed(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        code = "workshop_validator/a.py"
        runner = ProveRunner([code], lambda swapped: {tid: "fail" if swapped else "ok"},
                             rules=[(lambda c: c[:2] == ["git", "show"], 0, "")])
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertTrue(ok, out)
        self.assertEqual(runner.snapshots, [{code: "# head code\n"}, {code: ""}])

    # item 6: merge-base everywhere

    def test_base_is_merge_base_for_diff_and_show(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {tid: "fail" if swapped else "ok"})
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertTrue(ok, out)
        self.assertEqual(runner.find("git", "merge-base")[0][0], ["git", "merge-base", "origin/main", HEAD])
        for cmd, _ in runner.find("git", "diff"):
            self.assertIn(MB, cmd)
            self.assertNotIn("origin/main", " ".join(cmd))
        self.assertEqual(runner.find("git", "show")[0][0], ["git", "show", f"{MB}:workshop_validator/a.py"])

    def test_merge_when_green_uses_pr_base_branch(self):
        team = Path(self.tmp.name) / "team"
        for role, verdict in (("code-reviewer", "APPROVE"), ("safety-scanner", "CLEAR")):
            team_kit.save_review(team, 5, role, OID, f"notes\nVERDICT: {verdict}")
        runner = FakeRunner([
            (lambda c: c[:3] == ["gh", "pr", "view"] and "mergeCommit" in c[-1], 0,
             json.dumps({"mergeCommit": {"oid": "f" * 40}})),
            (lambda c: c[:3] == ["gh", "pr", "view"], 0, json.dumps(GOOD_PR)),
            (lambda c: c[:3] == ["gh", "pr", "checks"], 0, json.dumps([{"name": "unit", "bucket": "pass"}])),
        ])
        with mock.patch.object(team_gates, "prove_tests", return_value=True) as prove, \
                contextlib.redirect_stdout(io.StringIO()):
            team_gates.merge_when_green(self.repo, team, 5, dry_run=True, runner=runner)
        self.assertEqual(prove.call_args.args[1:], ("origin/main", OID))

    def test_no_merge_base_is_unproven(self):
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {},
                             rules=[(lambda c: c[:2] == ["git", "merge-base"], 1, "")])
        ok, out = self.prove(runner, "")
        self.assertFalse(ok)
        self.assertIn("UNPROVEN no merge-base", out)

    # item 7: Chromium

    def test_chromium_check_launches_closes_and_stops(self):
        tid = "tests.test_feature.FeatureTests.test_new_a"
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {tid: "fail" if swapped else "ok"})
        self.prove(runner, _feature_diff("test_new_a"))
        checks = [c for c, _ in runner.calls if len(c) == 3 and c[1] == "-c"]
        self.assertEqual(len(checks), 1)
        for needle in ("chromium.launch(headless=True)", ".close()", ".stop()"):
            self.assertIn(needle, checks[0][2])

    def test_chromium_launch_failure_is_unproven(self):
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {},
                             rules=[(lambda c: len(c) == 3 and c[1] == "-c", 1, "")])
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertFalse(ok)
        self.assertIn("UNPROVEN headless Chromium", out)

    # item 8: cleanup

    def test_worktree_removed_then_pruned_even_when_add_fails(self):
        runner = ProveRunner(["workshop_validator/a.py"], lambda swapped: {},
                             rules=[(lambda c: c[:3] == ["git", "worktree", "add"], 1, "")])
        ok, out = self.prove(runner, _feature_diff("test_new_a"))
        self.assertFalse(ok)
        self.assertIn("UNPROVEN git worktree add", out)
        names = [c[:3] for c, _ in runner.calls]
        self.assertIn(["git", "worktree", "remove"], names)
        self.assertLess(names.index(["git", "worktree", "remove"]), names.index(["git", "worktree", "prune"]))


if __name__ == "__main__":
    unittest.main()
