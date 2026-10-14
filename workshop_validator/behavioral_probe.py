"""Private-runner behavioral probes; no automatic omission or guide edits.

Only an explicitly authored necessity_assertions subset describes the desired
state before a manual edit. These same assertions MUST be in the independent
postcondition gate, so an incorrect generated/default value cannot PASS.
The private caller controls provenance, reset and secure artifact storage.
"""

from dataclasses import dataclass, replace

from .browser_worker import BrowserActionError, collect_observations, perform_actions
from .evidence import NEEDS_REVIEW, Verification, StepContext, verify_step
from .necessity import NecessityFinding, assess_necessity


@dataclass(frozen=True)
class ActionProbe:
    before: Verification
    after: Verification
    necessity: NecessityFinding


def execute_with_necessity_probe(page, step, context: StepContext, *,
                                 observer=collect_observations,
                                 actor=perform_actions):
    """Observe desired state BEFORE action, execute literally, check afterward.

    Returns normal postcondition observations for the durable checkpoint gate.
    An optional diagnostic is not a PASS and cannot recommend a guide removal:
    skip replay, clean reset and targeted negative control are NOT performed.
    """
    probes = step.necessity_assertions
    if not probes:
        actor(page, step.actions)
        return observer(page, context, step.assertions), None

    required_ids = {item.spec.assertion_id for item in probes}
    if len(required_ids) != len(probes) or not required_ids.issubset(
            {item.spec.assertion_id for item in step.assertions}):
        raise BrowserActionError("Necessity assertions must be unique required postconditions")

    specs = tuple(item.spec for item in probes)
    before_observations = observer(page, context, probes)
    before = verify_step(context, specs, tuple(
        replace(obs, evidence_id=obs.evidence_id + ":before")
        for obs in before_observations
    ))
    if before.status == NEEDS_REVIEW:
        raise BrowserActionError("Before-state evidence invalid; no action attempted")

    actor(page, step.actions)
    after_observations = observer(page, context, step.assertions)
    after = verify_step(context, specs, tuple(
        replace(obs, evidence_id=obs.evidence_id + ":after")
        for obs in after_observations if obs.assertion_id in required_ids
    ))
    finding = assess_necessity(before, after_executing=after)
    return after_observations, ActionProbe(before, after, finding)
