"""Fail-closed, read-only reconciliation before a PRIVATE checkpoint resumes.

This gate cannot authenticate the private collector, establish live Salesforce
state by itself, or resume any action. Call it only with independently collected
observations and trusted, frozen assertion specifications. It does not modify
checkpoints, retries, or reports.
"""

from dataclasses import dataclass
import re
from typing import Mapping, Sequence

from .dependencies import Step, plan_steps
from .evidence import AssertionSpec, Observation, StepContext, verify_step, PASS


@dataclass(frozen=True)
class ResumeIdentity:
    run_id: str
    org_id: str
    guide_sha256: str
    baseline_id: str


@dataclass(frozen=True)
class StateCheck:
    context: StepContext
    assertions: tuple[AssertionSpec, ...]
    observations: tuple[Observation, ...]


@dataclass(frozen=True)
class ResumeDecision:
    eligible_steps: tuple[str, ...]
    verified_previous_steps: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def can_resume(self) -> bool:
        return not self.blockers


def _valid_identity(identity: object) -> bool:
    return (isinstance(identity, ResumeIdentity)
            and all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", v)
                    for v in (identity.run_id, identity.org_id, identity.baseline_id))
            and isinstance(identity.guide_sha256, str)
            and re.fullmatch(r"[0-9a-f]{64}", identity.guide_sha256) is not None)


def reconcile_resume(
    *,
    expected: ResumeIdentity,
    observed: ResumeIdentity,
    steps: Sequence[Step],
    checkpoint_statuses: Mapping[str, str],
    checkpoint_pass_evidence: Mapping[str, Sequence[str]],
    baseline_check: StateCheck,
    passed_step_checks: Mapping[str, StateCheck],
    runnable_prechecks: Mapping[str, StateCheck],
) -> ResumeDecision:
    """Reverify live state before *considering* any resumed guide mutation.

    A PASS in SQLite is historical evidence, not proof that the org still has
    that state. Every historical PASS and every newly runnable step must be
    checked with fresh evidence. Blocked 429 steps stay blocked; Option A may
    still allow independent NOT_RUN steps. Caller must check the precondition
    again immediately before each action, under an approved private collector.
    """
    def blocked(code: str) -> ResumeDecision:
        return ResumeDecision((), (), (code,))

    if not _valid_identity(expected) or not _valid_identity(observed) or expected != observed:
        return blocked("BASELINE_IDENTITY_MISMATCH")
    steps = tuple(steps)
    if not steps or any(not isinstance(s, Step) for s in steps):
        return blocked("MANIFEST_INVALID")
    ids = {s.step_id for s in steps}
    if set(checkpoint_statuses) != ids:
        return blocked("CHECKPOINT_COVERAGE_MISMATCH")
    if any(status in {"RUNNING", "RETRY_WAIT"} for status in checkpoint_statuses.values()):
        return blocked("UNFINISHED_ATTEMPT")
    try:
        schedule = plan_steps(steps, checkpoint_statuses)
    except (ValueError, TypeError, RecursionError):
        return blocked("CHECKPOINT_DEPENDENCIES_INVALID")
    passed = tuple(s.step_id for s in steps if checkpoint_statuses[s.step_id] == PASS)
    runnable = schedule.runnable
    if (set(checkpoint_pass_evidence) != set(passed)
            or set(passed_step_checks) != set(passed)
            or set(runnable_prechecks) != set(runnable)):
        return blocked("RECHECK_COVERAGE_MISMATCH")

    used: set[str] = set()
    historical: set[str] = set()
    for step_id in passed:
        evidence = checkpoint_pass_evidence[step_id]
        if (not isinstance(evidence, (tuple, list)) or not evidence
                or any(not isinstance(e, str) or not e.strip() or e in historical for e in evidence)
                or len(set(evidence)) != len(evidence)):
            return blocked("HISTORICAL_EVIDENCE_INVALID")
        historical.update(evidence)

    def check(label: str, item: object) -> str | None:
        if not isinstance(item, StateCheck) or not isinstance(item.context, StepContext):
            return "RECHECK_INVALID"
        ctx = item.context
        if (ctx.run_id != expected.run_id or ctx.org_id != expected.org_id
                or ctx.step_id != label):
            return "RECHECK_CONTEXT_MISMATCH"
        try:
            result = verify_step(ctx, item.assertions, item.observations)
        except (TypeError, AttributeError, ValueError):
            return "RECHECK_INVALID"
        if result.status != PASS:
            return "LIVE_STATE_NOT_VERIFIED"
        if (not result.evidence_ids or any(not isinstance(e, str) or not e.strip()
                or e in historical or e in used for e in result.evidence_ids)
                or len(set(result.evidence_ids)) != len(result.evidence_ids)):
            return "RECHECK_EVIDENCE_REUSED"
        used.update(result.evidence_ids)
        return None

    failure = check("__baseline__", baseline_check)
    if failure:
        return blocked(failure)
    for step_id in passed:
        failure = check(step_id, passed_step_checks[step_id])
        if failure:
            return blocked(failure)
    for step_id in runnable:
        failure = check(step_id, runnable_prechecks[step_id])
        if failure:
            return blocked(failure)
    return ResumeDecision(runnable, passed, ())
