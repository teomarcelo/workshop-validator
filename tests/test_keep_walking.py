"""W-100 and W-117: keep walking after a failure, task by task. Synthetic guides and fake pages only."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from workshop_validator import guide_walk
from workshop_validator.automate import (
    FAILED, _summary, gate_outcome_of, load_gate, main as automate_main, outcome_of, save_gate,
)
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide
from workshop_validator.report import ReportStep
from workshop_validator.step_board import (
    build_board, main as board_main, outcomes_from_report_steps, render_board, write_results_pdf,
    write_run_report,
)

from tests.test_guide_walk import Clock, Page

EX_A = "Exercise 1: Sample Alpha"
EX_B = "Exercise 2: Sample Beta"
HOME = "https://example.lightning.force.com/lightning/page/home"
AFTER_FAILURE_FLAG = "after-earlier-failure"  # the suspect token the report and quick-mode verifier read


def _step(step_id, exercise, task, number):
    return GuideStep(step_id, 1, exercise, task, number, f"Do sample action {step_id}.",
                     (90.0, 10.0, 400.0, 24.0), None, ())


def _ok(state, step):
    return CheckResult("PASS", "done", (f"{step.step_id}:sample-done",))


def _walk(page, items, results):
    """RESULTS maps a step id to a run(state, step) function; every other step passes."""
    calls = []

    def run(state, step):
        calls.append(step.step_id)
        return results.get(step.step_id, _ok)(state, step)

    clock = Clock()
    outcome = walk_guide(
        page, GuideInventory(1, tuple(items), (), ()),
        checks=(GuideCheck("sample", lambda item: True, run),),
        timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
    )
    return outcome, calls


def _frozen(status, note="Sample control was not found."):
    return lambda state, step: CheckResult(status, note, (), freeze=True)


TWO_TASKS = (
    _step("a1", EX_A, "Task 1: Sample one", "1"),
    _step("a2", EX_A, "Task 1: Sample one", "2"),
    _step("a3", EX_A, "Task 1: Sample one", "3"),
    _step("b1", EX_A, "Task 2: Sample two", "1"),
    _step("b2", EX_A, "Task 2: Sample two", "2"),
)


class KeepWalkingTests(unittest.TestCase):
    def test_failure_blocks_only_the_rest_of_its_task(self):
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {"a2": _frozen("UI_MISMATCH")})
        self.assertEqual(outcome.statuses["a1"], "PASS")
        self.assertEqual(outcome.statuses["a2"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        self.assertTrue(outcome.notes["a3"].startswith("blocked by a2"), outcome.notes["a3"])
        self.assertNotIn("a3", calls)
        self.assertNotIn("a3", outcome.executed)
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertEqual(outcome.statuses["b2"], "PASS")
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])

    def test_functional_fail_blocks_its_task_too(self):
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {"a1": _frozen("FUNCTIONAL_FAIL")})
        self.assertEqual([outcome.statuses[s] for s in ("a2", "a3")], ["NOT_RUN", "NOT_RUN"])
        self.assertEqual(calls, ["a1", "b1", "b2"])

    def test_later_pass_in_the_same_exercise_is_suspect_and_other_exercise_is_not(self):
        items = (*TWO_TASKS, _step("c1", EX_B, "Task 1: Sample three", "1"))
        outcome, _calls = _walk(Page(HOME), items, {"a2": _frozen("UI_MISMATCH")})
        self.assertIn(AFTER_FAILURE_FLAG, outcome.suspects.get("b1", ()))
        self.assertIn(AFTER_FAILURE_FLAG, outcome.suspects.get("b2", ()))
        self.assertNotIn(AFTER_FAILURE_FLAG, outcome.suspects.get("c1", ()))
        # A pass before the failure is not flagged.
        self.assertNotIn(AFTER_FAILURE_FLAG, outcome.suspects.get("a1", ()))

    def test_a_non_stopping_failure_also_flags_later_tasks(self):
        fail = lambda state, step: CheckResult("FUNCTIONAL_FAIL", "Sample value was not saved.", ())
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {"a1": fail})
        # Unchanged: a non-stopping failure lets its own task go on.
        self.assertEqual(calls, ["a1", "a2", "a3", "b1", "b2"])
        self.assertNotIn(AFTER_FAILURE_FLAG, outcome.suspects.get("a2", ()))
        self.assertIn(AFTER_FAILURE_FLAG, outcome.suspects.get("b1", ()))

    def test_two_failures_in_different_tasks_are_both_listed_in_order(self):
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("UI_MISMATCH", "First sample miss."),
            "b1": _frozen("FUNCTIONAL_FAIL", "Second sample miss."),
        })
        steps = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       note=outcome.notes.get(item.step_id, ""))
            for item in TWO_TASKS
        ) + (ReportStep("z9", 1, "Outside step", "NOT_RUN", note="Sample step outside the browser."),)
        summary = _summary(SimpleNamespace(signed_in=True, report_steps=steps), 0, 3, FAILED, "x", Path("r"))
        self.assertEqual(summary["failures"], [
            {"step_id": "a2", "status": "UI_MISMATCH", "note": "First sample miss."},
            {"step_id": "b1", "status": "FUNCTIONAL_FAIL", "note": "Second sample miss."},
        ])
        self.assertEqual(summary["first_failure"]["step_id"], "a2")
        self.assertEqual(summary["blocked_steps"], 2)  # a3 and b2


class StopBehaviorKeptTests(unittest.TestCase):
    """ENV_BLOCKED, AUTH_BLOCKED, review stops and a lost session still end the walk."""

    def assertWalkStopped(self, results, stop="a2"):
        outcome, calls = _walk(Page(HOME), TWO_TASKS, results)
        self.assertEqual(calls[-1], stop)
        for later in ("a3", "b1", "b2"):
            self.assertEqual(outcome.statuses[later], "NOT_RUN")
            self.assertFalse(outcome.notes[later].startswith("blocked by"), outcome.notes[later])
        return outcome

    def test_env_blocked_stops(self):
        self.assertWalkStopped({"a2": _frozen("ENV_BLOCKED")})

    def test_env_blocked_without_freeze_stops(self):
        self.assertWalkStopped({"a2": lambda state, step: CheckResult("ENV_BLOCKED", "Sample block.", ())})

    def test_auth_blocked_stops(self):
        self.assertWalkStopped({"a2": _frozen("AUTH_BLOCKED")})

    def test_mandatory_own_value_needs_review_stops(self):
        """Mandatory own-value NEEDS_REVIEW with stop_walk=True stops the walk."""
        self.assertWalkStopped({
            "a2": lambda state, step: CheckResult(
                "NEEDS_REVIEW",
                "Sample Field is empty. The guide asks for the learner's own value.",
                (), freeze=True, stop_walk=True
            )
        })

    def test_failure_that_closed_the_browser_stops(self):
        def closed(state, step):
            state.page.url = "about:blank"
            return CheckResult("UI_MISMATCH", "Sample control was not found.", (), freeze=True)

        outcome = self.assertWalkStopped({"a2": closed})
        # Page-lost: a2 gets the failure note + page-lost sentence, later steps name it
        self.assertIn("Sample control was not found.", outcome.notes["a2"])
        self.assertIn("The page left the org (blank or closed page), so the walk stopped here.", outcome.notes["a2"])
        self.assertEqual(outcome.notes["b1"], "Not run: the page left the org at a2, so the walk stopped.")


def _add_popup(page, title, *buttons):
    dialog = ("dialog", f"{title}\nA short synthetic tip for a new org")
    inside = [("h2", title), *(("button", name) for name in buttons)]
    page.roles.add(dialog)
    page.roles.update(inside)
    page.children[dialog] = inside
    return dialog


class TaskBoundaryPopupTests(unittest.TestCase):
    def test_unrelated_popup_is_closed_before_the_next_task(self):
        page = Page(HOME, body="Home")
        shown = {}

        def fail(state, step):
            shown["dialog"] = _add_popup(page, "Sample Tip", "Get started", "Close")
            return CheckResult("UI_MISMATCH", "Sample control was not found.", (), freeze=True)

        page.on_click = lambda kind, name: page.roles.discard(shown["dialog"]) if name == "Close" else None
        outcome, calls = _walk(page, TWO_TASKS, {"a2": fail})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        self.assertEqual([e for e in page.events if e[0] == "click"], [("click", "button", "Close")])
        self.assertIn('closed "Sample Tip" pop-up', outcome.notes["b1"])
        self.assertIn("W-080", outcome.notes["b1"])
        # Closing is never evidence.
        self.assertEqual(outcome.evidence["b1"], ("b1:sample-done",))

    def test_popup_without_a_dismiss_control_stops_with_a_clear_note(self):
        page = Page(HOME, body="Home")

        def fail(state, step):
            _add_popup(page, "Sample Tip", "Get started")
            return CheckResult("UI_MISMATCH", "Sample control was not found.", (), freeze=True)

        outcome, calls = _walk(page, TWO_TASKS, {"a2": fail})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual([e for e in page.events if e[0] == "click"], [])
        for later in ("b1", "b2"):
            self.assertEqual(outcome.statuses[later], "NOT_RUN")
            self.assertIn('"Sample Tip" still covered the page after the failure in a2', outcome.notes[later])

    def walk_with_guide_dialog(self, items, modal=True):
        class Dialog:
            def get_attribute(self, name, timeout=None):
                return "true" if modal and name == "aria-modal" else None

            def is_visible(self):
                return True

        dialog = Dialog()
        scan = lambda _page: [(dialog, "Sample Editor", "sample editor fields")]

        def fail(state, step):
            guide_walk._open_guide_dialog(state, "Sample Editor")
            return CheckResult("UI_MISMATCH", "Sample control was not found.", (), freeze=True)

        with patch.object(guide_walk, "_visible_dialogs", scan):
            return _walk(Page(HOME, body="Home"), items, {"a2": fail})

    def test_guide_dialog_left_open_by_the_failed_task_stops_and_is_not_closed(self):
        outcome, calls = self.walk_with_guide_dialog(TWO_TASKS)
        self.assertEqual(calls, ["a1", "a2"])
        self.assertIn('"Sample Editor" still covered the page after the failure in a2', outcome.notes["b1"])

    def test_guide_dialog_that_is_not_modal_does_not_stop(self):
        _outcome, calls = self.walk_with_guide_dialog(TWO_TASKS, modal=False)
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])

    def test_guide_dialog_the_next_step_names_does_not_stop(self):
        named = GuideStep("b1", 1, EX_A, "Task 2: Sample two", "1", "In the Sample Editor, click Done.",
                          (90.0, 10.0, 400.0, 24.0), None, ())
        _outcome, calls = self.walk_with_guide_dialog((*TWO_TASKS[:3], named, TWO_TASKS[4]))
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])

    def test_unreadable_page_after_a_failure_stops_with_a_note(self):
        def broken(*_args, **_kwargs):
            raise RuntimeError("synthetic read failure")

        outcome, calls = _walk(Page(HOME), TWO_TASKS, {"a2": _frozen("UI_MISMATCH")})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        with patch.object(guide_walk, "_unnamed_modal", side_effect=broken):
            outcome, calls = _walk(Page(HOME), TWO_TASKS, {"a2": _frozen("UI_MISMATCH")})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertIn("could not be read after the failure in a2", outcome.notes["b1"])

    def test_repeated_task_label_gets_unique_keys(self):
        # Two tasks with the same label but different positions get different keys,
        # so the second task's steps get the suspect flag when the first task fails.
        REPEATED_TASKS = (
            _step("t1a1", EX_A, "Task 1: Shared name", "1"),
            _step("t1a2", EX_A, "Task 1: Shared name", "2"),
            _step("t2b1", EX_A, "Task 2: Different", "1"),  # Intervening task
            _step("t3a1", EX_A, "Task 1: Shared name", "1"),  # Same label as task 1, new occurrence
            _step("t3a2", EX_A, "Task 1: Shared name", "2"),
        )
        outcome, calls = _walk(Page(HOME), REPEATED_TASKS, {"t1a2": _frozen("UI_MISMATCH")})
        self.assertEqual(outcome.statuses["t1a1"], "PASS")
        self.assertEqual(outcome.statuses["t1a2"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["t2b1"], "PASS")
        # t3a1 and t3a2 are in a different task occurrence (after task 2), so they run
        self.assertEqual(outcome.statuses["t3a1"], "PASS")
        # And they get the suspect flag because an earlier task in the same exercise failed
        self.assertIn(AFTER_FAILURE_FLAG, outcome.suspects.get("t3a1", ()))

    def test_blocked_note_preserves_failure_note_ending(self):
        # The rstrip fix: a failure note that ends with ":" or space is not trimmed
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": lambda state, step: CheckResult("UI_MISMATCH", "Sample miss:", (), freeze=True)
        })
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        # The ":" at the end of "Sample miss:" should be preserved
        self.assertEqual(outcome.notes["a3"], "blocked by a2: Sample miss:")

    def test_blocked_note_after_a_failure_with_an_empty_note_reads_cleanly(self):
        # A failure with no note gives "blocked by a2", with no trailing ": ".
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": lambda state, step: CheckResult("UI_MISMATCH", "", (), freeze=True)
        })
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        self.assertEqual(outcome.notes["a3"], "blocked by a2")
        self.assertEqual(outcome.statuses["b1"], "PASS")


def _run(*statuses, notes=None):
    notes = notes or {}
    steps = tuple(
        ReportStep(f"s{i}", 1, f"Step {i}", status, note=notes.get(i, ""))
        for i, status in enumerate(statuses)
    )
    return SimpleNamespace(signed_in=True, message="", report_steps=steps)


class GateTests(unittest.TestCase):
    def test_failures_in_two_tasks_are_failed_for_outcome_and_gate(self):
        result = _run("PASS", "UI_MISMATCH", "NOT_RUN", "PASS", "FUNCTIONAL_FAIL", "NOT_RUN",
                      notes={1: "First sample miss.", 2: "blocked by s1: First sample miss.",
                             4: "Second sample miss.", 5: "blocked by s4: Second sample miss."})
        self.assertEqual(outcome_of(result), (FAILED, "First sample miss."))
        self.assertEqual(gate_outcome_of(result), FAILED)
        # Added (PR 65 review): the shape above passes on the old gate too. Here the
        # second task stops on a review step, which the old gate (stop step only) called
        # inconclusive; the first task's failure must still make it FAILED.
        result = _run("PASS", "UI_MISMATCH", "NOT_RUN", "PASS", "NEEDS_REVIEW", "NOT_RUN",
                      notes={1: "First sample miss.", 2: "blocked by s1: First sample miss.",
                             4: "Second sample review."})
        self.assertEqual(outcome_of(result), (FAILED, "First sample miss."))
        self.assertEqual(gate_outcome_of(result), FAILED)

    def test_failure_then_passes_to_the_end_is_failed_for_the_gate(self):
        result = _run("PASS", "UI_MISMATCH", "NOT_RUN", "PASS", "PASS",
                      notes={1: "First sample miss.", 2: "blocked by s1: First sample miss."})
        self.assertEqual(gate_outcome_of(result), FAILED)
        # Added (PR 65 review): the shape above passes on the old gate too. Here the walk
        # passes on and then stops out of time, which the old gate called inconclusive.
        result = _run("PASS", "UI_MISMATCH", "NOT_RUN", "PASS", "PASS", "NOT_RUN", "NOT_RUN",
                      notes={1: "First sample miss.", 2: "blocked by s1: First sample miss.",
                             5: guide_walk.OUT_OF_TIME_NOTE})
        self.assertEqual(gate_outcome_of(result), FAILED)


def _keep_root(folder, *, failures, stop, verdicts):
    root = Path(folder)
    run_id = "20990101T000000Z-org2"
    run_dir = root / "runs" / "org-0000000000" / run_id
    run_dir.mkdir(parents=True)
    summary = {"org": 2, "orgs": 3, "outcome": "failed", "run_dir": str(run_dir),
               "stop_step": stop, "failures": failures}
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    rows = [{"step_id": s, "verdict": v, "frames": "f.png"} for s, v in verdicts]
    (run_dir / "verdicts.json").write_text(json.dumps({"run_id": run_id, "verdicts": rows}), encoding="utf-8")
    save_gate(root, {"org": 2, "frozen": True, "reason": "x", "last_run": run_id})
    return root, run_dir


def _fail(step_id):
    return {"step_id": step_id, "status": "UI_MISMATCH", "note": "Sample miss."}


class KeepOrgTests(unittest.TestCase):
    def keep(self, root, run_dir):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = automate_main(["--reset-gate", "--keep-org", str(run_dir), "--private-root", str(root)])
        return code, out.getvalue()

    def test_stop_on_a_pass_judges_the_first_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_root(folder, failures=[_fail("s1")], stop="s5",
                                       verdicts=[("s1", "FALSE_FAILURE"), ("s5", "CONFIRMED_PASS")])
            code, _out = self.keep(root, run_dir)
            self.assertEqual(code, 0)
            gate = load_gate(root)
            self.assertEqual((gate["org"], gate["frozen"]), (1, False))
            self.assertEqual(gate["kept_org_reason"]["verified_false_failures"], ["s1"])

    def test_unverified_first_failure_refuses_even_when_the_stop_is_false(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_root(folder, failures=[_fail("s1"), _fail("s4")], stop="s4",
                                       verdicts=[("s4", "FALSE_FAILURE")])
            before = Path(root, "gate.json").read_bytes()
            code, out = self.keep(root, run_dir)
            self.assertEqual(code, 2)
            self.assertIn("failure step s1 is not verified", out)
            self.assertEqual(Path(root, "gate.json").read_bytes(), before)

    def test_a_later_real_failure_keeps_the_org_move(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_root(folder, failures=[_fail("s1"), _fail("s4")], stop="s6",
                                       verdicts=[("s1", "FALSE_FAILURE"), ("s4", "REAL")])
            code, out = self.keep(root, run_dir)
            self.assertEqual(code, 2)
            self.assertIn("step s4 is judged REAL", out)

    def test_empty_failures_judge_the_stop_step(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_root(folder, failures=[], stop="s2",
                                       verdicts=[("s2", "FALSE_FAILURE")])
            code, out = self.keep(root, run_dir)
            self.assertEqual(code, 0, out)
            self.assertEqual(load_gate(root)["kept_org_reason"]["verified_false_failures"], ["s2"])

    def test_malformed_failures_refuse(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_root(folder, failures=[{"status": "UI_MISMATCH"}], stop="s1",
                                       verdicts=[("s1", "FALSE_FAILURE")])
            code, out = self.keep(root, run_dir)
            self.assertEqual(code, 2)
            self.assertIn("malformed summary.json", out)


class ReportTests(unittest.TestCase):
    def test_render_lists_every_failure_the_blocked_count_and_the_suspect_chip(self):
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("UI_MISMATCH", "First sample miss."),
        })
        steps = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       evidence_ids=outcome.evidence.get(item.step_id, ()),
                       note=outcome.notes.get(item.step_id, ""),
                       suspect=outcome.suspects.get(item.step_id, ()))
            for item in TWO_TASKS
        )
        inventory = GuideInventory(1, TWO_TASKS, (), ())
        board = build_board(inventory, outcomes=outcomes_from_report_steps(steps), notice="Synthetic",
                            run_id="r1")
        with tempfile.TemporaryDirectory() as folder:
            run_dir = Path(folder) / "runs" / "org-a" / "r1"
            run_dir.mkdir(parents=True)
            summary = {**_summary(SimpleNamespace(signed_in=True, report_steps=steps), 0, 2, FAILED,
                                  "First sample miss.", run_dir), "guide_name": "guide.pdf"}
            (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            write_run_report(board, run_dir, summary=summary, guide_name="guide.pdf")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(board_main(["--render", str(run_dir)]), 0)
            html = (run_dir / "report.html").read_text(encoding="utf-8")
            write_results_pdf(board, run_dir / "results.pdf")
            self.assertTrue((run_dir / "results.pdf").read_bytes().startswith(b"%PDF"))
        page = render_board(board)
        self.assertIn("After an earlier failure in this exercise", page)
        self.assertIn("<strong>2</strong>", page.split("passes need a check")[0][-200:])
        self.assertIn("All Failures (1)", html)
        self.assertIn("<span class='stat-value'>1</span><span class='stat-label'>Blocked by a failure", html)
        self.assertIn("After an earlier failure in this exercise", html)
        self.assertIn("<span class='stat-value'>2</span><span class='stat-label'>passes need a check", html)


if __name__ == "__main__":
    unittest.main()
