"""Synthetic evidence-bound behavioral reporting; no real org data."""
import os
import tempfile
import unittest

from workshop_validator.behavioral_probe import ActionProbe
from workshop_validator.browser_worker import UIAssertion
from workshop_validator.evidence import AssertionSpec, Observation, Verification, PASS, FUNCTIONAL_FAIL
from workshop_validator.necessity import assess_necessity
from workshop_validator.pipeline import PlannedStep, validate_plan
from workshop_validator.private_runner import build_review_notes
from workshop_validator.report import ReportStep, render_report


class BehavioralReportingTests(unittest.TestCase):
    def exercise(self, value):
        spec = AssertionSpec("value", "DOM_VALUE", "correct")
        step = PlannedStep("S1", 1, "Synthetic manual edit", (),
                           (UIAssertion(spec, "textbox", "Field"),))
        probes = {}

        def execute(page, planned, ctx):
            before = Verification(PASS, (), ("synthetic-before",))
            after = Verification(PASS if value == "correct" else FUNCTIONAL_FAIL,
                                 (), ("synthetic-after",))
            probes[planned.step_id] = ActionProbe(
                before, after, assess_necessity(before, after_executing=after))
            return [Observation("synthetic-post", "value", "DOM_VALUE", value,
                                ctx.run_id, ctx.org_id, ctx.step_id, ctx.executor_id)]

        with tempfile.TemporaryDirectory() as d:
            outcome = validate_plan(None, os.path.join(d, "db.sqlite"), "synthetic",
                "synthetic-org", (step,), source_action_ids=("S1",),
                source_reconciled=True, execute_step=execute,
                behavioral_probes=probes)
        return step, outcome, probes

    def test_already_satisfied_is_reported_not_approved_for_removal(self):
        step, result, probes = self.exercise("correct")
        self.assertEqual(result.statuses["S1"], PASS)
        self.assertIn("PRECONDITION_ALREADY_MET_UNPROVEN", result.html)
        self.assertIn("synthetic-before", result.html)
        self.assertIn("Diagnostic only; no guide edit approved", result.html)
        notes = build_review_notes((step,), result.statuses, probes)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].status, "NEEDS_REVIEW")
        self.assertIn("already existed before", notes[0].finding)
        self.assertIn("synthetic-after", notes[0].evidence_ids)

    def test_wrong_value_fails_checkpoint_and_review_note(self):
        step, result, probes = self.exercise("wrong")
        self.assertEqual(result.statuses["S1"], FUNCTIONAL_FAIL)
        self.assertFalse(result.fully_verified)
        self.assertIn("ACTION_REGRESSED_EXPECTED_STATE", result.html)
        notes = build_review_notes((step,), result.statuses, probes)
        self.assertEqual(notes[0].status, FUNCTIONAL_FAIL)
        self.assertIn("ACTION_REGRESSED_EXPECTED_STATE", notes[0].finding)

    def test_no_probe_has_no_necessity_claim(self):
        step, result, probes = self.exercise("correct")
        self.assertEqual(build_review_notes((step,), result.statuses, {}), [])
        html = render_report("synthetic", [ReportStep("S1", 1, "Synthetic",
                             PASS, ("post",))], inventory_verified=True)
        self.assertIn("Not probed", html)

    def test_unattributed_or_unknown_behavioral_claim_rejected(self):
        for classification, ids in (("REDUNDANCY_CANDIDATE", ()),
                                    ("APPROVED_CHANGE", ("ev",))):
            with self.subTest(classification=classification):
                with self.assertRaises(ValueError):
                    render_report("synthetic", [ReportStep("S1", 1, "Synthetic",
                        PASS, ("post",), behavioral_classification=classification,
                        behavioral_evidence_ids=ids)], inventory_verified=True)


if __name__ == "__main__":
    unittest.main()
