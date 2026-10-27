"""Fixture tests for the producer-team kit, hooks and gates (synthetic data only)."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import tempfile
import time
import unittest

from workshop_validator import team_kit
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
            path.write_text("notes\nVERDICT: VETO\nfixed\nVERDICT: APPROVE\n")
            (Path(tmp) / "pr-7-safety-scanner.md").write_text("no verdict here")
            self.assertEqual(review_verdicts(Path(tmp), 7),
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
                home=home, git_root=lookup)[0])


if __name__ == "__main__":
    unittest.main()
