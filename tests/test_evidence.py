import unittest

from workshop_validator.evidence import (
    AssertionSpec, Observation, StepContext, verify_step,
    PASS, FUNCTIONAL_FAIL, NEEDS_REVIEW,
)


class EvidenceGateTests(unittest.TestCase):
    def setUp(self):
        self.context = StepContext("run-1", "org-1", "S-1", "executor", "verifier")
        self.specs = [
            AssertionSpec("title", "DOM_TEXT", "Saved", "contains"),
            AssertionSpec("visible", "DOM_VISIBLE", True),
        ]
        self.observations = [
            Observation("e-1", "title", "DOM_TEXT", "Record Saved", "run-1", "org-1", "S-1", "executor"),
            Observation("e-2", "visible", "DOM_VISIBLE", True, "run-1", "org-1", "S-1", "executor"),
        ]

    def check(self, status, specs=None, observations=None, context=None):
        result = verify_step(context or self.context,
                             self.specs if specs is None else specs,
                             self.observations if observations is None else observations)
        self.assertEqual(result.status, status)
        return result

    def test_matching_assertions_pass(self):
        result = self.check(PASS)
        self.assertEqual(result.evidence_ids, ("e-1", "e-2"))

    def test_missing_assertion_is_not_pass(self):
        self.check(NEEDS_REVIEW, observations=self.observations[:1])

    def test_empty_assertions_are_not_pass(self):
        self.check(NEEDS_REVIEW, specs=[], observations=[])

    def test_mismatch_is_functional_failure(self):
        bad = [self.observations[0], self.replace(self.observations[1], actual=False)]
        self.check(FUNCTIONAL_FAIL, observations=bad)

    def test_screenshot_is_not_assertion_grade(self):
        self.check(NEEDS_REVIEW, observations=[self.replace(self.observations[0], kind="SCREENSHOT"), self.observations[1]])

    def test_wrong_run_is_rejected(self):
        self.check(NEEDS_REVIEW, observations=[self.replace(self.observations[0], run_id="other"), self.observations[1]])

    def test_wrong_org_is_rejected(self):
        self.check(NEEDS_REVIEW, observations=[self.replace(self.observations[0], org_id="other"), self.observations[1]])

    def test_wrong_step_is_rejected(self):
        self.check(NEEDS_REVIEW, observations=[self.replace(self.observations[0], step_id="other"), self.observations[1]])

    def test_wrong_collector_is_rejected(self):
        self.check(NEEDS_REVIEW, observations=[self.replace(self.observations[0], collector_id="other"), self.observations[1]])

    def test_same_executor_and_verifier_is_rejected(self):
        self.check(NEEDS_REVIEW, context=self.replace(self.context, verifier_id="executor"))

    def test_duplicate_evidence_id_is_rejected(self):
        self.check(NEEDS_REVIEW, observations=[self.observations[0], self.replace(self.observations[1], evidence_id="e-1")])

    def test_duplicate_assertion_observations_are_rejected(self):
        self.check(NEEDS_REVIEW, observations=self.observations + [self.replace(self.observations[0], evidence_id="e-3")])

    def test_unexpected_evidence_is_rejected(self):
        self.check(NEEDS_REVIEW, observations=self.observations + [self.replace(self.observations[0], evidence_id="e-3", assertion_id="other")])

    def test_bool_does_not_count_as_api_status_int(self):
        spec = [AssertionSpec("http", "API_STATUS", 200)]
        obs = [Observation("e-3", "http", "API_STATUS", True, "run-1", "org-1", "S-1", "executor")]
        self.check(NEEDS_REVIEW, specs=spec, observations=obs)

    def test_api_status_match(self):
        spec = [AssertionSpec("http", "API_STATUS", 200)]
        obs = [Observation("e-3", "http", "API_STATUS", 200, "run-1", "org-1", "S-1", "executor")]
        self.check(PASS, specs=spec, observations=obs)

    def test_contains_only_for_nonempty_text(self):
        self.check(NEEDS_REVIEW, specs=[AssertionSpec("title", "DOM_VISIBLE", True, "contains"), self.specs[1]])
        self.check(NEEDS_REVIEW, specs=[AssertionSpec("title", "DOM_TEXT", "", "contains"), self.specs[1]])

    def test_not_contains_verifies_absent_text_and_rejects_presence(self):
        specs = [AssertionSpec("deprecated", "DOM_TEXT", "unused_setting:", "not_contains")]
        good = [Observation("e-safe", "deprecated", "DOM_TEXT",
                            "agent: configured\noutputs: enabled",
                            "run-1", "org-1", "S-1", "executor")]
        self.check(PASS, specs=specs, observations=good)
        self.check(FUNCTIONAL_FAIL, specs=specs,
                   observations=[self.replace(good[0], actual="unused_setting: old")])

    def test_negative_text_assertion_refuses_empty_editor_and_wrong_kind(self):
        spec = [AssertionSpec("deprecated", "DOM_TEXT", "old_field:", "not_contains")]
        empty = [Observation("e-empty", "deprecated", "DOM_TEXT", "",
                             "run-1", "org-1", "S-1", "executor")]
        self.check(NEEDS_REVIEW, specs=spec, observations=empty)
        self.check(NEEDS_REVIEW,
                   specs=[AssertionSpec("visible", "DOM_VISIBLE", True, "not_contains")])
        self.check(NEEDS_REVIEW,
                   specs=[AssertionSpec("empty", "DOM_TEXT", "", "not_contains")])

    def test_duplicate_spec_is_rejected(self):
        self.check(NEEDS_REVIEW, specs=self.specs + [self.specs[0]])

    def test_failure_reasons_do_not_echo_actual_values(self):
        actual = "SENSITIVE_EXAMPLE_VALUE"
        bad = [self.replace(self.observations[0], actual=actual), self.observations[1]]
        result = self.check(FUNCTIONAL_FAIL, observations=bad)
        self.assertNotIn(actual, repr(result))

    @staticmethod
    def replace(instance, **changes):
        from dataclasses import replace
        return replace(instance, **changes)


if __name__ == "__main__":
    unittest.main()
