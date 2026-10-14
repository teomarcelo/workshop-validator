"""Sanitized synthetic omission/fault replay evidence; no org or browser used."""

from dataclasses import replace
import unittest

from workshop_validator.evidence import FUNCTIONAL_FAIL, NEEDS_REVIEW, PASS, Verification
from workshop_validator.replay_provenance import ReplaySample, assess_replay


def samples():
    phases = (
        "executed_before", "executed_after", "executed_downstream",
        "skipped_before", "skipped_downstream", "negative_control",
    )
    return [ReplaySample(
        phase=p,
        run_id=("exec" if p.startswith("executed") else
                "skip" if p.startswith("skipped") else "fault"),
        org_id="synthetic-org", guide_sha256="a" * 64,
        baseline_id="baseline-v1",
        reset_id=("reset-exec" if p.startswith("executed") else
                  "reset-skip" if p.startswith("skipped") else "reset-fault"),
        step_id="S1", oracle_id="downstream-v1" if "downstream" in p else "target-v1",
        verification=Verification(FUNCTIONAL_FAIL if p == "negative_control" else PASS,
                                  (), ("ev-" + p,)),
        reset_verified=True, prerequisites_verified=True,
    ) for p in phases]


class ReplayProvenanceTests(unittest.TestCase):
    def test_all_three_resettable_paths_and_detected_fault_yield_review_only(self):
        finding = assess_replay(samples())
        self.assertEqual(finding.classification, "REDUNDANCY_CANDIDATE")
        self.assertTrue(finding.propose_guide_removal)
        self.assertTrue(finding.requires_instructor_review)

    def test_wrong_value_that_passes_blocks_correction(self):
        s = samples()
        s[-1] = replace(s[-1], verification=Verification(PASS, (), ("fault-pass",)))
        finding = assess_replay(s)
        self.assertEqual(finding.classification, "TEST_ORACLE_INSENSITIVE")
        self.assertFalse(finding.propose_guide_removal)

    def test_wrong_value_detected_but_skip_downstream_fails(self):
        s = samples()
        s[4] = replace(s[4], verification=Verification(FUNCTIONAL_FAIL, (), ("failed-skip",)))
        self.assertEqual(assess_replay(s).classification, "SKIP_PATH_FAILED")

    def test_action_regresses_state_even_when_omission_works(self):
        s = samples()
        s[1] = replace(s[1], verification=Verification(FUNCTIONAL_FAIL, (), ("failed-edit",)))
        self.assertEqual(assess_replay(s).classification, "ACTION_REGRESSED_EXPECTED_STATE")

    def test_executed_downstream_must_pass(self):
        s = samples()
        s[2] = replace(s[2], verification=Verification(NEEDS_REVIEW, (), ("uncertain",)))
        self.assertEqual(assess_replay(s).classification, "EXECUTED_DOWNSTREAM_UNVERIFIED")

    def test_skip_baseline_must_match(self):
        s = samples()
        s[3] = replace(s[3], verification=Verification(FUNCTIONAL_FAIL, (), ("diverged",)))
        self.assertEqual(assess_replay(s).classification, "SKIP_BASELINE_DIVERGED")

    def test_executed_before_state_must_match_skip_baseline(self):
        s = samples()
        s[0] = replace(s[0], verification=Verification(FUNCTIONAL_FAIL, (), ("missing-default",)))
        self.assertEqual(assess_replay(s).classification, "EXECUTED_BASELINE_DIVERGED")

    def test_nonstring_digest_and_phase_fail_closed(self):
        s = samples()
        s[0] = replace(s[0], guide_sha256=None)
        self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")
        s = samples()
        s[0] = replace(s[0], phase=None)
        self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")

    def test_missing_or_duplicate_phase_fails_closed(self):
        self.assertEqual(assess_replay(samples()[:-1]).classification, "REPLAY_PROVENANCE_INVALID")
        s = samples()
        s[-1] = replace(s[-1], phase="skipped_before")
        self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")

    def test_mixed_baseline_org_guide_or_step_fails_closed(self):
        for field, value in (("baseline_id", "other"), ("org_id", "other"),
                             ("guide_sha256", "b" * 64), ("step_id", "S2")):
            with self.subTest(field=field):
                s = samples()
                s[-1] = replace(s[-1], **{field: value})
                self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")

    def test_reused_evidence_or_run_or_reset_fails_closed(self):
        for field, value in (("run_id", "exec"), ("reset_id", "reset-exec")):
            with self.subTest(field=field):
                s = samples()
                s[-1] = replace(s[-1], **{field: value})
                self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")
        s = samples()
        s[-1] = replace(s[-1], verification=Verification(FUNCTIONAL_FAIL, (), ("ev-executed_before",)))
        self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")

    def test_unverified_reset_or_prerequisites_fails_closed(self):
        for field in ("reset_verified", "prerequisites_verified"):
            with self.subTest(field=field):
                s = samples()
                s[0] = replace(s[0], **{field: False})
                self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")

    def test_oracle_mismatch_or_same_downstream_oracle_fails_closed(self):
        for index, oracle in ((-1, "different-target"), (2, "target-v1")):
            with self.subTest(index=index):
                s = samples()
                s[index] = replace(s[index], oracle_id=oracle)
                self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")

    def test_missing_evidence_and_invalid_digest_fails_closed(self):
        s = samples()
        s[-1] = replace(s[-1], verification=Verification(FUNCTIONAL_FAIL, (), ()))
        self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")
        s = samples()
        s[0] = replace(s[0], guide_sha256="not-a-digest")
        self.assertEqual(assess_replay(s).classification, "REPLAY_PROVENANCE_INVALID")


if __name__ == "__main__":
    unittest.main()
