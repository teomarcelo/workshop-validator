"""Pure dependency gate for Option A continuation after blocked guide steps.

This module does not execute Salesforce actions, grant PASS, or retry requests.
Only independently verified steps may be supplied with status PASS.
"""

from dataclasses import dataclass
from typing import Mapping, Sequence

PASS = "PASS"
NOT_RUN = "NOT_RUN"
BLOCKING = frozenset({
    "ENV_BLOCKED", "AUTH_BLOCKED", "UI_MISMATCH",
    "FUNCTIONAL_FAIL", "NEEDS_REVIEW",
})
VALID_STATUSES = BLOCKING | {PASS, NOT_RUN, "RUNNING", "RETRY_WAIT"}


@dataclass(frozen=True)
class Step:
    step_id: str
    dependency_ids: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class Schedule:
    """Read-only classification; it does not mutate or mark steps passed."""

    runnable: tuple[str, ...]
    waiting: dict[str, tuple[str, ...]]
    blocked_by: dict[str, tuple[str, ...]]
    complete: bool


def plan_steps(steps: Sequence[Step], statuses: Mapping[str, str]) -> Schedule:
    """Return safe-to-start independent steps in deterministic manifest order.

    Steps with NOT_RUN and all prerequisites PASS are runnable. A blocked
    ancestor makes a downstream NOT_RUN step blocked, even through multiple
    levels. Pending prerequisites instead make it waiting. An already
    blocked step is never silently retried by this planner.

    A separate executor/recovery queue owns retries; an independent evidence
    gate owns all transitions to PASS. No Salesforce operations occur here.
    """
    ids = [step.step_id for step in steps]
    if any(not isinstance(step_id, str) or not step_id.strip() for step_id in ids):
        raise ValueError("Every step must have a nonempty step_id")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate step_id in manifest")
    by_id = {step.step_id: step for step in steps}
    for step in steps:
        if len(step.dependency_ids) != len(set(step.dependency_ids)):
            raise ValueError(f"Duplicate dependency for {step.step_id}")
        for dep in step.dependency_ids:
            if dep not in by_id:
                raise ValueError(f"Unknown dependency {dep!r} for {step.step_id}")

    for step_id, status in statuses.items():
        if step_id not in by_id:
            raise ValueError(f"Status for unknown step {step_id!r}")
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r} for {step_id}")
    current = {step_id: statuses.get(step_id, NOT_RUN) for step_id in ids}

    visiting: set[str] = set()
    visited: set[str] = set()

    def check_acyclic(step_id: str) -> None:
        if step_id in visiting:
            raise ValueError(f"Dependency cycle involving {step_id}")
        if step_id in visited:
            return
        visiting.add(step_id)
        for dep in by_id[step_id].dependency_ids:
            check_acyclic(dep)
        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in ids:
        check_acyclic(step_id)

    # A PASS on a dependent step without verified PASS prerequisites is
    # contradictory; reject it instead of publishing an all-green result.
    for step in steps:
        if current[step.step_id] == PASS:
            missing = [dep for dep in step.dependency_ids if current[dep] != PASS]
            if missing:
                raise ValueError(f"Premature PASS for {step.step_id}; unmet: {missing}")

    blockers_cache: dict[str, frozenset[str]] = {}

    def blocking_ancestors(step_id: str) -> frozenset[str]:
        if step_id not in blockers_cache:
            causes: set[str] = set()
            for dep in by_id[step_id].dependency_ids:
                if current[dep] in BLOCKING:
                    causes.add(dep)
                causes.update(blocking_ancestors(dep))
            blockers_cache[step_id] = frozenset(causes)
        return blockers_cache[step_id]

    runnable: list[str] = []
    waiting: dict[str, tuple[str, ...]] = {}
    blocked_by: dict[str, tuple[str, ...]] = {}
    for step in steps:
        if current[step.step_id] != NOT_RUN:
            continue
        blockers = blocking_ancestors(step.step_id)
        if blockers:
            blocked_by[step.step_id] = tuple(x for x in ids if x in blockers)
        else:
            unmet = tuple(dep for dep in step.dependency_ids if current[dep] != PASS)
            if unmet:
                waiting[step.step_id] = unmet
            else:
                runnable.append(step.step_id)

    complete = bool(steps) and all(
        current[step.step_id] == PASS for step in steps if step.required
    )
    return Schedule(tuple(runnable), waiting, blocked_by, complete)
