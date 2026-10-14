"""Fail-closed provenance gate for separately executed omission experiments.

This module NEVER resets an org, skips a guide action, injects a fault, or
collects browser evidence. It checks the structural binding of PRIVATE replay
results before allowing the existing necessity classifier to propose an
instructor-review candidate. The private collector must authenticate evidence
and independently prove resets; boolean attestations are not proof by themselves.
"""

from dataclasses import dataclass
import re
from typing import Sequence

from .evidence import FUNCTIONAL_FAIL, NEEDS_REVIEW, PASS, Verification
from .necessity import NecessityFinding, assess_necessity


PHASES = frozenset({
    "executed_before", "executed_after", "executed_downstream",
    "skipped_before", "skipped_downstream", "negative_control",
})
TARGET_PHASES = frozenset({
    "executed_before", "executed_after", "skipped_before", "negative_control",
})
RUN_GROUPS = (
    ("executed_before", "executed_after", "executed_downstream"),
    ("skipped_before", "skipped_downstream"),
    ("negative_control",),
)


@dataclass(frozen=True)
class ReplaySample:
    phase: str
    run_id: str
    org_id: str
    guide_sha256: str
    baseline_id: str
    reset_id: str
    step_id: str
    oracle_id: str
    verification: Verification
    reset_verified: bool
    prerequisites_verified: bool


def _blocked(classification: str, reason: str) -> NecessityFinding:
    return NecessityFinding(classification, False, True, (reason,))


def assess_replay(samples: Sequence[ReplaySample]) -> NecessityFinding:
    """Assess independently verified execute/omit/break replays.

    A candidate requires three distinct clean-reset trial runs on the same
    source, org and baseline; a stable target oracle across the negative
    control; a distinct downstream oracle; unique evidence IDs; and verified
    prerequisite/reset attestations. No evidence is interpreted as a literal
    action log: callers must independently establish that the stated action,
    omission and reversible fault actually occurred.
    """
    samples = tuple(samples)
    if (len(samples) != len(PHASES) or any(
        not isinstance(s, ReplaySample) for s in samples
    ) or any(not isinstance(s.phase, str) for s in samples) or
        {s.phase for s in samples} != PHASES):
        return _blocked("REPLAY_PROVENANCE_INVALID", "Replay phases are missing or duplicated.")
    by_phase = {s.phase: s for s in samples}
    first = samples[0]
    if (not isinstance(first.guide_sha256, str) or
            not re.fullmatch(r"[a-fA-F0-9]{64}", first.guide_sha256)
            or not all(isinstance(getattr(first, key), str) and getattr(first, key).strip()
                       for key in ("run_id", "org_id", "baseline_id", "reset_id", "step_id", "oracle_id"))):
        return _blocked("REPLAY_PROVENANCE_INVALID", "Replay identity is invalid.")
    if any(
        s.guide_sha256 != first.guide_sha256 or
        s.org_id != first.org_id or s.baseline_id != first.baseline_id or
        s.step_id != first.step_id or
        not isinstance(s.oracle_id, str) or not s.oracle_id.strip() or
        type(s.reset_verified) is not bool or not s.reset_verified or
        type(s.prerequisites_verified) is not bool or not s.prerequisites_verified or
        not isinstance(s.verification, Verification) or
        s.verification.status not in (PASS, FUNCTIONAL_FAIL, NEEDS_REVIEW) or
        not isinstance(s.verification.evidence_ids, (tuple, list)) or
        not s.verification.evidence_ids or
        any(not isinstance(e, str) or not e.strip() for e in s.verification.evidence_ids)
        for s in samples
    ):
        return _blocked("REPLAY_PROVENANCE_INVALID", "Replay evidence or reset attestation is invalid.")
    evidence_ids = [e for s in samples for e in s.verification.evidence_ids]
    if len(evidence_ids) != len(set(evidence_ids)):
        return _blocked("REPLAY_PROVENANCE_INVALID", "Replay evidence IDs were reused.")
    group_ids = []
    reset_ids = []
    for group in RUN_GROUPS:
        runs = {by_phase[p].run_id for p in group}
        resets = {by_phase[p].reset_id for p in group}
        if len(runs) != 1 or len(resets) != 1 or not all(runs) or not all(resets):
            return _blocked("REPLAY_PROVENANCE_INVALID", "Replay run or reset grouping is invalid.")
        group_ids.extend(runs)
        reset_ids.extend(resets)
    if len(set(group_ids)) != 3 or len(set(reset_ids)) != 3:
        return _blocked("REPLAY_PROVENANCE_INVALID", "Replays require distinct runs and resets.")
    target_oracles = {by_phase[p].oracle_id for p in TARGET_PHASES}
    downstream_oracles = {by_phase[p].oracle_id for p in PHASES - TARGET_PHASES}
    if (len(target_oracles) != 1 or len(downstream_oracles) != 1 or
            not all(target_oracles | downstream_oracles) or target_oracles == downstream_oracles):
        return _blocked("REPLAY_PROVENANCE_INVALID", "Target or downstream oracle identity differs.")

    # A broken control that still passes takes precedence over a removal claim.
    if by_phase["negative_control"].verification.status == PASS:
        return _blocked("TEST_ORACLE_INSENSITIVE", "A deliberately broken state passed the target oracle.")
    if by_phase["executed_downstream"].verification.status != PASS:
        return _blocked("EXECUTED_DOWNSTREAM_UNVERIFIED", "Executed-path downstream behavior was not verified.")
    if by_phase["skipped_before"].verification.status != PASS:
        return _blocked("SKIP_BASELINE_DIVERGED", "Skipped-path before-state differs or is unverified.")
    if by_phase["executed_before"].verification.status != PASS:
        return _blocked("EXECUTED_BASELINE_DIVERGED", "Executed-path before-state differs or is unverified.")
    return assess_necessity(
        by_phase["executed_before"].verification,
        after_executing=by_phase["executed_after"].verification,
        after_skipping=by_phase["skipped_downstream"].verification,
        negative_control=by_phase["negative_control"].verification,
        baseline_reproduced=True,
        prerequisites_verified=True,
    )
