import unittest

from workshop_validator.evidence import PASS, FUNCTIONAL_FAIL, NEEDS_REVIEW, Verification
from workshop_validator.necessity import assess_necessity


def verified(status, name="synthetic-evidence"):
    return Verification(status, (), (name,))


class NecessityDecisionTests(unittest.TestCase):
    def test_verified_skip_and_detected_break_yields_only_candidate(self):
        result = assess_necessity(verified(PASS), after_skipping=verified(PASS),
                    negative_control=verified(FUNCTIONAL_FAIL),
                    baseline_reproduced=True, prerequisites_verified=True)
        self.assertEqual(result.classification, "REDUNDANCY_CANDIDATE")
        self.assertTrue(result.propose_guide_removal)
        self.assertTrue(result.requires_instructor_review)

    def test_already_correct_is_not_enough(self):
        r = assess_necessity(verified(PASS))
        self.assertFalse(r.propose_guide_removal)
        self.assertEqual(r.classification, "PRECONDITION_ALREADY_MET_UNPROVEN")
        r = assess_necessity(verified(PASS), baseline_reproduced=True,
                             prerequisites_verified=True)
        self.assertEqual(r.classification, "SKIP_PATH_NOT_TESTED")

    def test_negative_control_that_passes_fails_closed(self):
        r = assess_necessity(verified(PASS), after_skipping=verified(PASS),
                    negative_control=verified(PASS),
                    baseline_reproduced=True, prerequisites_verified=True)
        self.assertEqual(r.classification, "TEST_ORACLE_INSENSITIVE")
        self.assertFalse(r.propose_guide_removal)

    def test_skip_path_failure_disproves_early_removal(self):
        r = assess_necessity(verified(PASS), after_skipping=verified(FUNCTIONAL_FAIL),
                    negative_control=verified(FUNCTIONAL_FAIL),
                    baseline_reproduced=True, prerequisites_verified=True)
        self.assertEqual(r.classification, "SKIP_PATH_FAILED")
        self.assertFalse(r.propose_guide_removal)

    def test_manual_edit_is_required_for_untested_synthetic_initial_state(self):
        r = assess_necessity(verified(FUNCTIONAL_FAIL),
                             after_executing=verified(PASS))
        self.assertEqual(r.classification, "ACTION_HELPED_ON_TESTED_BASELINE")
        self.assertFalse(r.propose_guide_removal)

    def test_broken_precondition_and_no_effect_are_reported(self):
        r = assess_necessity(verified(FUNCTIONAL_FAIL),
                             after_executing=verified(FUNCTIONAL_FAIL))
        self.assertEqual(r.classification, "ACTION_DID_NOT_FIX_RESULT")

    def test_missing_evidence_is_inconclusive(self):
        r = assess_necessity(verified(NEEDS_REVIEW))
        self.assertEqual(r.classification, "INSUFFICIENT_PRECONDITION_EVIDENCE")
        self.assertFalse(r.propose_guide_removal)

    def test_action_that_breaks_correct_state_blocks_removal(self):
        r = assess_necessity(verified(PASS), after_executing=verified(FUNCTIONAL_FAIL),
                             after_skipping=verified(PASS),
                             negative_control=verified(FUNCTIONAL_FAIL),
                             baseline_reproduced=True, prerequisites_verified=True)
        self.assertEqual(r.classification, "ACTION_REGRESSED_EXPECTED_STATE")
        self.assertFalse(r.propose_guide_removal)

    def test_inconclusive_executed_path_blocks_removal_even_with_skip_proof(self):
        r = assess_necessity(verified(PASS), after_executing=verified(NEEDS_REVIEW),
                             after_skipping=verified(PASS),
                             negative_control=verified(FUNCTIONAL_FAIL),
                             baseline_reproduced=True, prerequisites_verified=True)
        self.assertEqual(r.classification, "EXECUTED_PATH_INCONCLUSIVE")
        self.assertFalse(r.propose_guide_removal)

    def test_guide_step_numbering_has_no_role_in_behavioral_decision(self):
        r = assess_necessity(verified(PASS), after_skipping=verified(PASS),
                             baseline_reproduced=True, prerequisites_verified=True,
                             negative_control=verified(FUNCTIONAL_FAIL))
        self.assertEqual(r.classification, "REDUNDANCY_CANDIDATE")


if __name__ == "__main__":
    unittest.main()
