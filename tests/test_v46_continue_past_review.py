"""W-135: a frozen NEEDS_REVIEW blocks only its task, except for mandatory own-value fields.

Synthetic guides and fake checks only. Real browser tests only where W-117 uses them.
"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workshop_validator.automate import FAILED, _summary, gate_outcome_of, load_gate, save_gate
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide
from workshop_validator.report import ReportStep

from tests.test_guide_walk import Clock, Page

EX_A = "Exercise 1: Sample Alpha"
EX_B = "Exercise 2: Sample Beta"
HOME = "https://example.lightning.force.com/lightning/page/home"
AFTER_FAILURE_FLAG = "after-earlier-failure"


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


class FrozenNeedsReviewTests(unittest.TestCase):
    def test_frozen_needs_review_blocks_only_its_task(self):
        """A frozen NEEDS_REVIEW in task A blocks A's later steps, but task B runs."""
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "Clicked Sample, but no change could be read from the page.")
        })
        self.assertEqual(outcome.statuses["a1"], "PASS")
        self.assertEqual(outcome.statuses["a2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        self.assertTrue(outcome.notes["a3"].startswith("blocked by a2"), outcome.notes["a3"])
        self.assertNotIn("a3", calls)
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertEqual(outcome.statuses["b2"], "PASS")
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])

    def test_later_pass_after_needs_review_is_flagged_suspect(self):
        """A PASS in a later task after a frozen NEEDS_REVIEW gets the suspect flag."""
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "Sample miss.")
        })
        self.assertIn(AFTER_FAILURE_FLAG, outcome.suspects.get("b1", ()))
        self.assertIn(AFTER_FAILURE_FLAG, outcome.suspects.get("b2", ()))
        # A pass before the NEEDS_REVIEW is not flagged.
        self.assertNotIn(AFTER_FAILURE_FLAG, outcome.suspects.get("a1", ()))

    def test_mandatory_own_value_needs_review_stops_everything(self):
        """Mandatory own-value NEEDS_REVIEW with stop_walk=True still stops the walk."""
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": lambda state, step: CheckResult(
                "NEEDS_REVIEW",
                "Sample Field is empty. The guide asks for the learner's own value, "
                "and the validator does not invent one.",
                (), freeze=True, stop_walk=True
            )
        })
        self.assertEqual(outcome.statuses["a2"], "NEEDS_REVIEW")
        self.assertEqual(calls[-1], "a2")
        for later in ("a3", "b1", "b2"):
            self.assertEqual(outcome.statuses[later], "NOT_RUN")
            self.assertFalse(outcome.notes[later].startswith("blocked by"), outcome.notes[later])

    def test_env_blocked_still_stops_everything(self):
        """ENV_BLOCKED keeps the full stop."""
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("ENV_BLOCKED", "Sample pop-up covered the page.")
        })
        self.assertEqual(calls[-1], "a2")
        for later in ("a3", "b1", "b2"):
            self.assertEqual(outcome.statuses[later], "NOT_RUN")
            self.assertFalse(outcome.notes[later].startswith("blocked by"), outcome.notes[later])

    def test_auth_blocked_still_stops_everything(self):
        """AUTH_BLOCKED keeps the full stop."""
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("AUTH_BLOCKED", "Sample session blocked.")
        })
        self.assertEqual(calls[-1], "a2")
        for later in ("a3", "b1", "b2"):
            self.assertEqual(outcome.statuses[later], "NOT_RUN")
            self.assertFalse(outcome.notes[later].startswith("blocked by"), outcome.notes[later])

    def test_non_frozen_needs_review_does_not_block(self):
        """A non-frozen NEEDS_REVIEW behaves as today (no task blocking)."""
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": lambda state, step: CheckResult("NEEDS_REVIEW", "Sample pick not shown.", ())
        })
        self.assertEqual(calls, ["a1", "a2", "a3", "b1", "b2"])
        self.assertEqual(outcome.statuses["a2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.statuses["a3"], "PASS")
        self.assertNotIn("blocked by", outcome.notes.get("a3", ""))

    def test_unfrozen_needs_review_not_in_failures_and_no_suspect(self):
        """Unfrozen NEEDS_REVIEW is not listed in failures and doesn't add suspect flag."""
        outcome, calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": lambda state, step: CheckResult("NEEDS_REVIEW", "Sample pick not shown.", ())
        })
        steps = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       note=outcome.notes.get(item.step_id, ""),
                       freeze=outcome.freezes.get(item.step_id, False))
            for item in TWO_TASKS
        )
        result = SimpleNamespace(signed_in=True, report_steps=steps)
        summary = _summary(result, 0, 3, FAILED, "", Path("r"))
        # Unfrozen NEEDS_REVIEW should NOT be in failures
        self.assertEqual(summary["failures"], [])
        # Later passes should NOT have the suspect flag
        self.assertNotIn(AFTER_FAILURE_FLAG, outcome.suspects.get("a3", ()))
        self.assertNotIn(AFTER_FAILURE_FLAG, outcome.suspects.get("b1", ()))
        self.assertNotIn(AFTER_FAILURE_FLAG, outcome.suspects.get("b2", ()))

    def test_frozen_needs_review_then_real_failure_lists_both(self):
        """Both failures are listed; the gate judges the real failure."""
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "First sample review."),
            "b1": _frozen("UI_MISMATCH", "Second sample miss."),
        })
        steps = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       note=outcome.notes.get(item.step_id, ""),
                       freeze=outcome.freezes.get(item.step_id, False))
            for item in TWO_TASKS
        )
        result = SimpleNamespace(signed_in=True, report_steps=steps)
        summary = _summary(result, 0, 3, FAILED, "First sample review.", Path("r"))
        self.assertEqual(len(summary["failures"]), 2)
        self.assertEqual(summary["failures"][0], {
            "step_id": "a2", "status": "NEEDS_REVIEW", "note": "First sample review."
        })
        self.assertEqual(summary["failures"][1], {
            "step_id": "b1", "status": "UI_MISMATCH", "note": "Second sample miss."
        })
        # The gate should still judge the real failure (UI_MISMATCH), not the NEEDS_REVIEW
        self.assertEqual(gate_outcome_of(result), FAILED)

    def test_no_step_becomes_pass_from_this_change(self):
        """No step is PASS because of this change."""
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "Sample review.")
        })
        # a3 is NOT_RUN (blocked by a2), not PASS
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        # b1 and b2 would have been PASS anyway (no change there)
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertEqual(outcome.statuses["b2"], "PASS")


class SourceTests(unittest.TestCase):
    """Test that _act_own sets stop_walk flag for mandatory fields."""

    def test_act_own_empty_mandatory_field_returns_stop_walk_true(self):
        """Direct call to _act_own with empty mandatory field returns stop_walk=True."""
        from workshop_validator.guide_walk import _act_own, WalkState
        from unittest.mock import Mock, patch

        # Create mocks
        page = Mock()
        state = WalkState(page=page)
        step = GuideStep("P001N001", 1, "Exercise 1", "Task 1", "1",
                        "First Name: (your first name) *mandatory",
                        (0, 0, 0, 0), None, ())

        # Mock labeled to return a field
        mock_field = Mock()
        mock_field.nth = lambda i: Mock()

        with patch('workshop_validator.guide_walk.labeled', return_value=mock_field), \
             patch('workshop_validator.guide_walk._count', return_value=1), \
             patch('workshop_validator.guide_walk._is_editable_field', return_value=True), \
             patch('workshop_validator.guide_walk._input', return_value=""), \
             patch('workshop_validator.guide_walk._is_field_mandatory', return_value=True):

            result = _act_own(state, step, "First Name")

            self.assertIsNotNone(result)
            self.assertEqual(result.status, "NEEDS_REVIEW")
            self.assertTrue(result.stop_walk, "stop_walk must be True for empty mandatory field")
            self.assertTrue(result.freeze)

    def test_act_own_mandatory_field_not_found_returns_stop_walk_true(self):
        """Direct call to _act_own with mandatory field not found returns stop_walk=True."""
        from workshop_validator.guide_walk import _act_own, WalkState
        from unittest.mock import Mock, patch

        page = Mock()
        state = WalkState(page=page)
        step = GuideStep("P001N002", 1, "Exercise 1", "Task 1", "2",
                        "Email: (your email) *mandatory",
                        (0, 0, 0, 0), None, ())

        # Mock labeled to return None (field not found)
        with patch('workshop_validator.guide_walk.labeled', return_value=None), \
             patch('workshop_validator.guide_walk._is_field_mandatory', return_value=True):

            result = _act_own(state, step, "Email")

            self.assertIsNotNone(result)
            self.assertEqual(result.status, "UI_MISMATCH")
            self.assertTrue(result.stop_walk, "stop_walk must be True for mandatory field not found")
            self.assertTrue(result.freeze)


class PopUpNotesTests(unittest.TestCase):
    """Test that stop_walk flag prevents pop-up notes from hiding the mandatory stop."""

    def test_popup_closed_during_mandatory_step_still_stops(self):
        """Pop-up closed during a mandatory step; stop_walk flag ensures walk stops."""
        # Simulate a mandatory step that has a pop-up note added
        def mandatory_with_popup(state, step):
            # Simulate pop-up closed during the step
            state.popup_notes = ["Closed the Sample Tips pop-up (owner rule W-080)."]
            return CheckResult("NEEDS_REVIEW", "Empty mandatory field.", (), freeze=True, stop_walk=True)

        outcome, calls = _walk(Page(HOME), TWO_TASKS, {"a2": mandatory_with_popup})
        # The walk must stop at a2, not continue to b1
        self.assertEqual(calls[-1], "a2")
        for later in ("a3", "b1", "b2"):
            self.assertEqual(outcome.statuses[later], "NOT_RUN")
            # These are not blocked by a2; the walk just stopped
            self.assertFalse(outcome.notes.get(later, "").startswith("blocked by"))

    def test_pretask_popup_cleanup_before_mandatory_step_still_stops(self):
        """Pre-task cleanup closes pop-up before a task whose first step is mandatory."""
        # Simulate a frozen real failure in a1 that blocks a2-a3, then b1 is mandatory
        # The walk should close pop-ups before task 2, then stop at b1
        def frozen_fail(state, step):
            return CheckResult("UI_MISMATCH", "Sample control not found.", (), freeze=True)

        def mandatory_first_step(state, step):
            # b1 is the first step of task 2 and is mandatory
            return CheckResult("NEEDS_REVIEW", "Empty mandatory field.", (), freeze=True, stop_walk=True)

        outcome, calls = _walk(Page(HOME), TWO_TASKS, {
            "a1": frozen_fail,
            "b1": mandatory_first_step,
        })
        # a1 fails, a2-a3 are blocked, b1 is checked and stops the walk
        self.assertEqual(calls, ["a1", "b1"])
        self.assertEqual(outcome.statuses["a1"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["b1"], "NEEDS_REVIEW")
        # b2 is NOT_RUN because the walk stopped at b1
        self.assertEqual(outcome.statuses["b2"], "NOT_RUN")
        self.assertFalse(outcome.notes.get("b2", "").startswith("blocked by"))


class SummaryAndGateTests(unittest.TestCase):
    def test_summary_lists_needs_review_failure_and_counts_blocked_steps(self):
        """summary.json lists frozen NEEDS_REVIEW as a failure and counts blocked steps."""
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "Sample review note.")
        })
        steps = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       note=outcome.notes.get(item.step_id, ""),
                       freeze=outcome.freezes.get(item.step_id, False))
            for item in TWO_TASKS
        )
        result = SimpleNamespace(signed_in=True, report_steps=steps)
        summary = _summary(result, 0, 3, FAILED, "Sample review note.", Path("r"))
        self.assertEqual(summary["failures"], [{
            "step_id": "a2", "status": "NEEDS_REVIEW", "note": "Sample review note."
        }])
        self.assertEqual(summary["first_failure"]["step_id"], "a2")
        self.assertEqual(summary["blocked_steps"], 1)  # a3 is blocked

    def test_needs_review_before_real_failure_gate_judges_real_failure(self):
        """When frozen NEEDS_REVIEW comes before a real failure, the gate judges the real failure."""
        # This is the W-121 rule: the gate judges the first real failure
        outcome, _calls = _walk(Page(HOME), TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "Sample review."),
            "b1": _frozen("UI_MISMATCH", "Sample real failure."),
        })
        steps = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       note=outcome.notes.get(item.step_id, ""),
                       freeze=outcome.freezes.get(item.step_id, False))
            for item in TWO_TASKS
        )
        result = SimpleNamespace(signed_in=True, report_steps=steps)
        summary = _summary(result, 0, 3, FAILED, "Sample review.", Path("r"))
        # Gate outcome is still FAILED based on the real failure
        self.assertEqual(gate_outcome_of(result), FAILED)
        # Keep-org would need both failures verified FALSE_FAILURE
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run_id = "20990101T000000Z-org2"
            run_dir = root / "runs" / "org-0000000000" / run_id
            run_dir.mkdir(parents=True)
            summary_data = {**summary, "run_dir": str(run_dir), "guide_name": "guide.pdf"}
            (run_dir / "summary.json").write_text(json.dumps(summary_data), encoding="utf-8")
            # Verify only the real failure
            rows = [{"step_id": "b1", "verdict": "FALSE_FAILURE", "frames": "f.png"}]
            (run_dir / "verdicts.json").write_text(
                json.dumps({"run_id": run_id, "verdicts": rows}), encoding="utf-8"
            )
            save_gate(root, {"org": 2, "frozen": True, "reason": "x", "last_run": run_id})
            from workshop_validator.automate import main as automate_main
            with contextlib.redirect_stdout(io.StringIO()) as out:
                code = automate_main(
                    ["--reset-gate", "--keep-org", str(run_dir), "--private-root", str(root)]
                )
            # Should refuse because a2 (frozen NEEDS_REVIEW) is not verified
            self.assertEqual(code, 2)
            self.assertIn("failure step a2 is not verified", out.getvalue())


if __name__ == "__main__":
    unittest.main()
