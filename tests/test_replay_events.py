"""Sanitized synthetic replay ledger; no org, guide or private artifacts."""

from dataclasses import replace
import unittest

from workshop_validator.evidence import FUNCTIONAL_FAIL, PASS, Verification
from workshop_validator.replay_provenance import ReplaySample
from workshop_validator.replay_events import (
    ReplayEvent, TRIAL_EVENTS, assess_recorded_replay,
)


def fixture():
    phases = (
        "executed_before", "executed_after", "executed_downstream",
        "skipped_before", "skipped_downstream", "negative_control",
    )
    samples = [ReplaySample(
        phase=p,
        run_id="exec" if p.startswith("executed") else "skip" if p.startswith("skipped") else "fault",
        org_id="synthetic-org", guide_sha256="a" * 64,
        baseline_id="synthetic-reset", reset_id="reset-exec" if p.startswith("executed") else
        "reset-skip" if p.startswith("skipped") else "reset-fault",
        step_id="S1", oracle_id="downstream" if "downstream" in p else "target",
        verification=Verification(FUNCTIONAL_FAIL if p == "negative_control" else PASS,
                                  (), ("ev-" + p,)),
        reset_verified=True, prerequisites_verified=True,
    ) for p in phases]
    refs = {
        "executed": {"target_before": "executed_before", "target_after": "executed_after",
                     "downstream": "executed_downstream"},
        "skipped": {"target_before": "skipped_before", "downstream": "skipped_downstream"},
        "fault": {"fault_target": "negative_control"},
    }
    events = []
    for trial, kinds in TRIAL_EVENTS.items():
        run = {"executed": "exec", "skipped": "skip", "fault": "fault"}[trial]
        for i, kind in enumerate(kinds, 1):
            action = kind in {"literal_action", "omission_declared", "fault_injected", "fault_restored"}
            events.append(ReplayEvent(
                event_id=f"evt:{trial}:{i}", run_id=run, reset_id="reset-" + run,
                step_id="S1", sequence=i, kind=kind,
                actor_id="browser" if action else "independent-verifier",
                evidence_ids=("ev-" + refs[trial][kind],) if kind in refs[trial] else
                             ("ev-restored",) if kind == "restore_confirmed" else
                             (f"ev-event:{trial}:{i}",),
                action_id="manual-edit" if action else None,
            ))
    return samples, events


def check(samples=None, events=None, **overrides):
    s, e = fixture()
    return assess_recorded_replay(
        s if samples is None else samples, e if events is None else events,
        action_id="manual-edit", executor_id="browser", verifier_id="independent-verifier",
        restored_target=Verification(PASS, (), ("ev-restored",)),
        restored_oracle_id="target", **overrides,
    )


class ReplayEventTests(unittest.TestCase):
    def test_complete_separate_trials_are_review_only(self):
        finding = check()
        self.assertEqual(finding.classification, "REDUNDANCY_CANDIDATE")
        self.assertTrue(finding.requires_instructor_review)

    def test_missing_event_blocks_candidate(self):
        _, events = fixture()
        self.assertEqual(check(events=events[:-1]).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_skipped_trial_that_executed_action_blocks_candidate(self):
        _, events = fixture()
        i = next(i for i, e in enumerate(events) if e.kind == "omission_declared")
        events[i] = replace(events[i], kind="literal_action")
        self.assertEqual(check(events=events).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_wrong_order_or_sequence_blocks_candidate(self):
        _, events = fixture()
        for changes in ({"sequence": 99}, {"sequence": 1}):
            with self.subTest(changes=changes):
                modified = list(events)
                modified[3] = replace(modified[3], **changes)
                self.assertEqual(check(events=modified).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_forged_observer_identity_blocks_candidate(self):
        _, events = fixture()
        events[2] = replace(events[2], actor_id="browser")
        self.assertEqual(check(events=events).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_mutation_with_wrong_action_id_blocks_candidate(self):
        _, events = fixture()
        events[3] = replace(events[3], action_id="other-action")
        self.assertEqual(check(events=events).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_wrong_run_reset_or_step_blocks_candidate(self):
        _, events = fixture()
        for field, value in (("run_id", "other"), ("reset_id", "other"), ("step_id", "other")):
            with self.subTest(field=field):
                modified = list(events)
                modified[3] = replace(modified[3], **{field: value})
                self.assertEqual(check(events=modified).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_reused_event_id_or_evidence_blocks_candidate(self):
        _, events = fixture()
        for field, value in (("event_id", events[0].event_id),
                             ("evidence_ids", events[0].evidence_ids),
                             ("evidence_ids", ("ev-repeat", "ev-repeat"))):
            with self.subTest(field=field, value=value):
                modified = list(events)
                modified[3] = replace(modified[3], **{field: value})
                self.assertEqual(check(events=modified).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_malformed_evidence_is_fail_closed_not_an_exception(self):
        _, events = fixture()
        for invalid in ((["nested"],), (None,), (42,)):
            with self.subTest(invalid=invalid):
                modified = list(events)
                modified[3] = replace(modified[3], evidence_ids=invalid)
                self.assertEqual(check(events=modified).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_observation_evidence_must_match_verification(self):
        _, events = fixture()
        events[2] = replace(events[2], evidence_ids=("ev-other",))
        self.assertEqual(check(events=events).classification, "REPLAY_EVENT_LOG_INVALID")

    def test_restore_missing_or_unverified_blocks_candidate(self):
        _, events = fixture()
        events[-1] = replace(events[-1], evidence_ids=("ev-unrelated",))
        self.assertEqual(check(events=events).classification, "RESTORE_UNVERIFIED")
        self.assertEqual(assess_recorded_replay(
            *fixture(), action_id="manual-edit", executor_id="browser",
            verifier_id="independent-verifier",
            restored_target=Verification(FUNCTIONAL_FAIL, (), ("ev-restored",)),
            restored_oracle_id="target",
        ).classification, "RESTORE_UNVERIFIED")

    def test_restore_must_use_same_target_oracle(self):
        self.assertEqual(assess_recorded_replay(
            *fixture(), action_id="manual-edit", executor_id="browser",
            verifier_id="independent-verifier",
            restored_target=Verification(PASS, (), ("ev-restored",)),
            restored_oracle_id="different",
        ).classification, "RESTORE_UNVERIFIED")

    def test_negative_control_that_passes_blocks_even_with_full_log(self):
        samples, events = fixture()
        samples[-1] = replace(samples[-1], verification=Verification(PASS, (), ("ev-negative_control",)))
        finding = check(samples=samples, events=events)
        self.assertEqual(finding.classification, "TEST_ORACLE_INSENSITIVE")
        self.assertFalse(finding.propose_guide_removal)

    def test_wrong_literal_edit_cannot_propose_removal(self):
        samples, events = fixture()
        samples[1] = replace(samples[1], verification=Verification(FUNCTIONAL_FAIL, (), ("ev-executed_after",)))
        self.assertFalse(check(samples=samples, events=events).propose_guide_removal)


if __name__ == "__main__":
    unittest.main()
