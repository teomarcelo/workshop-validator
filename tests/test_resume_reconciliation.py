"""Synthetic post-checkpoint org drift tests; no live org or private data."""

from dataclasses import replace
import unittest

from workshop_validator.dependencies import Step
from workshop_validator.evidence import AssertionSpec, Observation, StepContext
from workshop_validator.resume_reconciliation import (
    ResumeIdentity, StateCheck, reconcile_resume,
)


def check(label, *, actual="correct", evidence=None, run="synthetic-run", org="synthetic-org"):
    context = StepContext(run, org, label, "collector", "independent-verifier")
    return StateCheck(context, (AssertionSpec("value", "DOM_VALUE", "correct"),),
                      (Observation(evidence or "fresh:" + label, "value", "DOM_VALUE", actual,
                                   run, org, label, "collector"),))


class ResumeReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.identity = ResumeIdentity("synthetic-run", "synthetic-org", "a" * 64, "baseline-v1")
        self.kw = dict(
            expected=self.identity, observed=self.identity,
            steps=(Step("S1"), Step("S2", ("S1",)), Step("S3")),
            checkpoint_statuses={"S1": "PASS", "S2": "NOT_RUN", "S3": "ENV_BLOCKED"},
            checkpoint_pass_evidence={"S1": ("old:S1",)},
            baseline_check=check("__baseline__"),
            passed_step_checks={"S1": check("S1")},
            runnable_prechecks={"S2": check("S2")},
        )

    def decide(self, **changes):
        return reconcile_resume(**(self.kw | changes))

    def test_option_a_only_independent_eligible_step(self):
        d = self.decide()
        self.assertTrue(d.can_resume)
        self.assertEqual(d.eligible_steps, ("S2",))
        self.assertEqual(d.verified_previous_steps, ("S1",))

    def test_wrong_generated_value_in_historical_pass_blocks(self):
        d = self.decide(passed_step_checks={"S1": check("S1", actual="broken")})
        self.assertFalse(d.can_resume)
        self.assertEqual(d.blockers, ("LIVE_STATE_NOT_VERIFIED",))

    def test_wrong_value_before_next_action_blocks(self):
        self.assertEqual(self.decide(runnable_prechecks={"S2": check("S2", actual="broken")}).blockers,
                         ("LIVE_STATE_NOT_VERIFIED",))

    def test_baseline_value_drift_blocks(self):
        self.assertEqual(self.decide(baseline_check=check("__baseline__", actual="broken")).blockers,
                         ("LIVE_STATE_NOT_VERIFIED",))

    def test_wrong_guide_org_run_and_reset_block(self):
        for change in ({"org_id": "different"}, {"run_id": "different"},
                       {"guide_sha256": "b" * 64}, {"baseline_id": "different"}):
            with self.subTest(change=change):
                self.assertEqual(self.decide(observed=replace(self.identity, **change)).blockers,
                                 ("BASELINE_IDENTITY_MISMATCH",))

    def test_missing_or_fake_pass_evidence_blocks(self):
        for evidence in ({}, {"S1": ()}, {"S1": ("",)}, {"S1": ("old:S1", "old:S1")}):
            with self.subTest(evidence=evidence):
                self.assertFalse(self.decide(checkpoint_pass_evidence=evidence).can_resume)

    def test_reused_historical_or_fresh_evidence_blocks(self):
        self.assertEqual(self.decide(passed_step_checks={"S1": check("S1", evidence="old:S1")}).blockers,
                         ("RECHECK_EVIDENCE_REUSED",))
        self.assertEqual(self.decide(runnable_prechecks={"S2": check("S2", evidence="fresh:S1")}).blockers,
                         ("RECHECK_EVIDENCE_REUSED",))

    def test_unfinished_attempt_blocks_even_with_other_runnable_steps(self):
        for status in ("RUNNING", "RETRY_WAIT"):
            self.assertEqual(self.decide(checkpoint_statuses={"S1": "PASS", "S2": status,
                                                              "S3": "ENV_BLOCKED"}).blockers,
                             ("UNFINISHED_ATTEMPT",))

    def test_premature_pass_blocks(self):
        self.assertEqual(self.decide(checkpoint_statuses={"S1": "NOT_RUN", "S2": "PASS",
                                                          "S3": "ENV_BLOCKED"},
                                     checkpoint_pass_evidence={"S2": ("old:S2",)},
                                     passed_step_checks={"S2": check("S2")},
                                     runnable_prechecks={"S1": check("S1")}).blockers,
                         ("CHECKPOINT_DEPENDENCIES_INVALID",))

    def test_missing_live_recheck_blocks(self):
        self.assertEqual(self.decide(passed_step_checks={}).blockers, ("RECHECK_COVERAGE_MISMATCH",))
        self.assertEqual(self.decide(runnable_prechecks={}).blockers, ("RECHECK_COVERAGE_MISMATCH",))

    def test_foreign_checkpoint_step_blocks(self):
        self.assertEqual(self.decide(checkpoint_statuses=self.kw["checkpoint_statuses"] |
                                                 {"private-step": "PASS"}).blockers,
                         ("CHECKPOINT_COVERAGE_MISMATCH",))

    def test_collector_cannot_be_verifier(self):
        wrong = check("S2")
        wrong = replace(wrong, context=replace(wrong.context, verifier_id="collector"))
        self.assertEqual(self.decide(runnable_prechecks={"S2": wrong}).blockers,
                         ("LIVE_STATE_NOT_VERIFIED",))

    def test_wrong_context_blocks(self):
        self.assertEqual(self.decide(runnable_prechecks={"S2": check("S2", run="other")}).blockers,
                         ("RECHECK_CONTEXT_MISMATCH",))

    def test_no_implicit_replay_of_blocked_step(self):
        d = self.decide()
        self.assertNotIn("S3", d.eligible_steps)
        self.assertEqual(self.kw["checkpoint_statuses"]["S3"], "ENV_BLOCKED")


if __name__ == "__main__":
    unittest.main()
