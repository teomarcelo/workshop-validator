"""Synthetic behavioral pre/post probes. No Salesforce credentials or PDFs."""
import json
import os
import tempfile
import unittest

from workshop_validator.browser_worker import UIAction, UIAssertion, BrowserActionError
from workshop_validator.evidence import AssertionSpec, StepContext, verify_step, PASS, FUNCTIONAL_FAIL
from workshop_validator.pipeline import PlannedStep
from workshop_validator.behavioral_probe import execute_with_necessity_probe
from workshop_validator.private_runner import load_plan, PrivateRunnerError


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.ctx = StepContext("synthetic-run", "synthetic-org", "step", "browser", "verifier")
        self.desired = UIAssertion(AssertionSpec("type", "DOM_TEXT", "list[object]", "contains"),
                                   "textbox", "Generated script")
        self.step = PlannedStep("step", 1, "Edit generated type",
            (UIAction("fill", "textbox", "Generated script", "list[object]"),),
            (self.desired,), (), True, (self.desired,))

    def observer(self, state, missing=False):
        def observe(page, ctx, assertions):
            from workshop_validator.evidence import Observation
            if missing:
                return []
            return [Observation(
                evidence_id=f"{ctx.run_id}:{ctx.step_id}:dom:{i}",
                assertion_id=a.spec.assertion_id, kind=a.spec.kind,
                actual=state["value"], run_id=ctx.run_id, org_id=ctx.org_id,
                step_id=ctx.step_id, collector_id=ctx.executor_id)
                for i, a in enumerate(assertions)]
        return observe

    def test_wrong_literal_value_is_functional_failure(self):
        state = {"value": "list[object]"}
        def wrong_action(page, actions):
            state["value"] = "string"
        observed, probe = execute_with_necessity_probe(
            None, self.step, self.ctx, observer=self.observer(state),
            actor=wrong_action)
        self.assertEqual(probe.before.status, PASS)
        self.assertEqual(probe.after.status, FUNCTIONAL_FAIL)
        self.assertEqual(probe.necessity.classification, "ACTION_REGRESSED_EXPECTED_STATE")
        self.assertFalse(probe.necessity.propose_guide_removal)
        self.assertEqual(verify_step(self.ctx, (self.desired.spec,), observed).status,
                         FUNCTIONAL_FAIL)

    def test_missing_before_evidence_prevents_mutation(self):
        mutated = []
        with self.assertRaises(BrowserActionError):
            execute_with_necessity_probe(
                None, self.step, self.ctx, observer=self.observer({}, missing=True),
                actor=lambda page, actions: mutated.append(True))
        self.assertEqual(mutated, [])

    def test_already_satisfied_never_auto_skips_or_recommends_removal(self):
        state = {"value": "list[object]"}
        mutations = []
        observed, probe = execute_with_necessity_probe(
            None, self.step, self.ctx, observer=self.observer(state),
            actor=lambda page, actions: mutations.append("literal"))
        self.assertEqual(mutations, ["literal"])
        self.assertEqual(probe.after.status, PASS)
        self.assertEqual(probe.necessity.classification, "PRECONDITION_ALREADY_MET_UNPROVEN")
        self.assertFalse(probe.necessity.propose_guide_removal)

    def test_no_necessity_probe_does_not_infer_redundancy(self):
        plain = PlannedStep("step", 1, "Click Save", (), (self.desired,))
        observed, probe = execute_with_necessity_probe(
            None, plain, self.ctx, observer=self.observer({"value":"list[object]"}),
            actor=lambda page, actions: None)
        self.assertIsNone(probe)
        self.assertEqual(len(observed), 1)

    def test_unbound_necessity_assertion_blocks_mutation(self):
        extra = UIAssertion(AssertionSpec("other", "DOM_VISIBLE", True), "button", "Save")
        broken = PlannedStep("step", 1, "Bad plan", (), (self.desired,), (), True, (extra,))
        mutated = []
        with self.assertRaises(BrowserActionError):
            execute_with_necessity_probe(None, broken, self.ctx,
                observer=self.observer({"value":"list[object]"}),
                actor=lambda page, actions: mutated.append(True))
        self.assertFalse(mutated)

    def test_private_plan_requires_necessity_assertions_in_post_gate(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "plan.json")
            base = {"schema_version":1, "guide_sha256":"a"*64, "steps":[{
                "step_id":"step", "page":1, "title":"Synthetic",
                "actions":[{"kind":"fill","role":"textbox","accessible_name":"Generated script","value":"list[object]"}],
                "assertions":[{"assertion_id":"saved","kind":"DOM_VISIBLE","expected":True,
                               "role":"status","accessible_name":"Saved"}],
                "necessity_assertions":[{"assertion_id":"type","kind":"DOM_TEXT",
                    "expected":"list[object]","operator":"contains",
                    "role":"textbox","accessible_name":"Generated script"}]}]}
            with open(path,"w") as f: json.dump(base,f)
            planned = load_plan(path,pdf_digest="a"*64,page_count=1)[0]
            self.assertEqual(len(planned.assertions),2)
            self.assertEqual(len(planned.necessity_assertions),1)
            base["steps"][0]["necessity_assertions"][0]["assertion_id"]="saved"
            with open(path,"w") as f: json.dump(base,f)
            with self.assertRaises(PrivateRunnerError):
                load_plan(path,pdf_digest="a"*64,page_count=1)


if __name__ == "__main__":
    unittest.main()
