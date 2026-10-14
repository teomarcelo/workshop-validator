"""Fail-closed event-order gate for private controlled replay records.

Only structural provenance is checked here. The private collector must still
independently authenticate events, resets, actual browser actions, and evidence.
No browser, org, PDF, credentials or cloud service is accessed by this module.
"""

from dataclasses import dataclass
import re
from typing import Sequence

from .evidence import PASS, Verification
from .necessity import NecessityFinding
from .replay_provenance import ReplaySample, assess_replay


TRIAL_EVENTS = {
    "executed": (
        "reset_confirmed", "prerequisites_confirmed", "target_before",
        "literal_action", "target_after", "downstream",
    ),
    "skipped": (
        "reset_confirmed", "prerequisites_confirmed", "target_before",
        "omission_declared", "downstream",
    ),
    "fault": (
        "reset_confirmed", "prerequisites_confirmed", "fault_injected",
        "fault_target", "fault_restored", "restore_confirmed",
    ),
}
PHASE_EVENT = {
    "executed_before": ("executed", "target_before"),
    "executed_after": ("executed", "target_after"),
    "executed_downstream": ("executed", "downstream"),
    "skipped_before": ("skipped", "target_before"),
    "skipped_downstream": ("skipped", "downstream"),
    "negative_control": ("fault", "fault_target"),
}
EXECUTOR_EVENTS = frozenset({
    "literal_action", "omission_declared", "fault_injected", "fault_restored",
})


@dataclass(frozen=True)
class ReplayEvent:
    event_id: str
    run_id: str
    reset_id: str
    step_id: str
    sequence: int
    kind: str
    actor_id: str
    evidence_ids: tuple[str, ...]
    action_id: str | None = None


def _blocked(classification: str, reason: str) -> NecessityFinding:
    return NecessityFinding(classification, False, True, (reason,))


def _safe_id(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", value) is not None


def by_target_oracle(samples: Sequence[ReplaySample]) -> str:
    return next(s.oracle_id for s in samples if s.phase == "negative_control")


def assess_recorded_replay(
    samples: Sequence[ReplaySample],
    events: Sequence[ReplayEvent],
    *,
    action_id: str,
    executor_id: str,
    verifier_id: str,
    restored_target: Verification,
    restored_oracle_id: str,
) -> NecessityFinding:
    """Require an ordered action/omission/fault/restore ledger for a candidate.

    Six independently verified replay phases are required by assess_replay.
    Additionally, each of the three runs must have its own ordered event log,
    with verifier-bound observation IDs matching phase evidence exactly. A
    separate target-oracle PASS is required *after* fault restoration.

    This cannot establish that the collector was honest or that an omitted
    action did not happen outside its event log. Authentication, independent
    reset proof and an append-only private event store are deployment gates.
    """
    samples = tuple(samples)
    finding = assess_replay(samples)
    if not finding.propose_guide_removal:
        return finding

    if (not all(_safe_id(x) for x in (action_id, executor_id, verifier_id))
            or executor_id == verifier_id):
        return _blocked("REPLAY_EVENT_LOG_INVALID", "Replay actor or action identity is invalid.")
    if (not _safe_id(restored_oracle_id) or
            restored_oracle_id != by_target_oracle(samples) or
            not isinstance(restored_target, Verification) or
            restored_target.status != PASS or
            not isinstance(restored_target.evidence_ids, (tuple, list)) or
            not restored_target.evidence_ids or
            any(not _safe_id(x) for x in restored_target.evidence_ids)):
        return _blocked("RESTORE_UNVERIFIED", "Fault restoration was not independently verified.")

    by_phase = {s.phase: s for s in samples}
    run_groups = {
        "executed": by_phase["executed_before"],
        "skipped": by_phase["skipped_before"],
        "fault": by_phase["negative_control"],
    }
    events = tuple(events)
    if (len(events) != sum(len(kinds) for kinds in TRIAL_EVENTS.values()) or
            any(not isinstance(e, ReplayEvent) for e in events)):
        return _blocked("REPLAY_EVENT_LOG_INVALID", "Replay event count or type is invalid.")

    event_ids = set()
    evidence_ids = set()
    grouped = {trial: [] for trial in TRIAL_EVENTS}
    run_to_trial = {sample.run_id: trial for trial, sample in run_groups.items()}
    for event in events:
        trial = run_to_trial.get(event.run_id)
        if (trial is None or not _safe_id(event.event_id) or
                event.event_id in event_ids or
                event.reset_id != run_groups[trial].reset_id or
                event.step_id != run_groups[trial].step_id or
                type(event.sequence) is not int or event.sequence < 1 or
                not isinstance(event.kind, str) or
                not isinstance(event.evidence_ids, (tuple, list)) or
                not event.evidence_ids or
                any(not _safe_id(e) or e in evidence_ids for e in event.evidence_ids) or
                len(set(event.evidence_ids)) != len(event.evidence_ids)):
            return _blocked("REPLAY_EVENT_LOG_INVALID", "Replay event provenance or evidence is invalid.")
        event_ids.add(event.event_id)
        evidence_ids.update(event.evidence_ids)
        if event.kind in EXECUTOR_EVENTS:
            if event.actor_id != executor_id or event.action_id != action_id:
                return _blocked("REPLAY_EVENT_LOG_INVALID", "Replay mutation identity differs.")
        elif event.actor_id != verifier_id or event.action_id is not None:
            return _blocked("REPLAY_EVENT_LOG_INVALID", "Replay observation lacks independent attribution.")
        grouped[trial].append(event)

    by_event = {}
    for trial, required in TRIAL_EVENTS.items():
        ordered = sorted(grouped[trial], key=lambda e: e.sequence)
        if ([e.sequence for e in ordered] != list(range(1, len(required) + 1)) or
                tuple(e.kind for e in ordered) != required):
            return _blocked("REPLAY_EVENT_LOG_INVALID", "Replay order or omission boundary is invalid.")
        by_event[trial] = {e.kind: e for e in ordered}

    for phase, (trial, kind) in PHASE_EVENT.items():
        if tuple(by_event[trial][kind].evidence_ids) != tuple(by_phase[phase].verification.evidence_ids):
            return _blocked("REPLAY_EVENT_LOG_INVALID", "Replay observation evidence does not match its phase.")
    if tuple(by_event["fault"]["restore_confirmed"].evidence_ids) != tuple(restored_target.evidence_ids):
        return _blocked("RESTORE_UNVERIFIED", "Restoration evidence does not match the target oracle.")
    return finding
