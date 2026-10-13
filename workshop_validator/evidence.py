"""Independent, deterministic assertion gate for sanitized step observations.

No browser execution, external network, private artifacts, or credential access.
The expected assertions must come from a trusted guide manifest, never from the
executor's own post-hoc claims. Evidence provenance here is structural only;
a deployed runner must authenticate its collector and bind stored evidence.
"""

from dataclasses import dataclass
from typing import Any, Sequence

PASS = "PASS"
FUNCTIONAL_FAIL = "FUNCTIONAL_FAIL"
NEEDS_REVIEW = "NEEDS_REVIEW"

# Screenshots and model opinions are deliberately not assertion-grade evidence.
VALUE_TYPES = {"DOM_TEXT": str, "DOM_VISIBLE": bool, "API_STATUS": int}
OPERATORS = frozenset({"eq", "contains"})


@dataclass(frozen=True)
class AssertionSpec:
    assertion_id: str
    kind: str
    expected: Any
    operator: str = "eq"


@dataclass(frozen=True)
class Observation:
    evidence_id: str
    assertion_id: str
    kind: str
    actual: Any
    run_id: str
    org_id: str
    step_id: str
    collector_id: str


@dataclass(frozen=True)
class StepContext:
    run_id: str
    org_id: str
    step_id: str
    executor_id: str
    verifier_id: str


@dataclass(frozen=True)
class Verification:
    status: str
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...]


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def verify_step(
    context: StepContext,
    assertions: Sequence[AssertionSpec],
    observations: Sequence[Observation],
) -> Verification:
    """Return PASS only for complete, independent, matching assertions.

    Any missing/invalid provenance or malformed observation requires review.
    A well-formed observed mismatch is a functional failure, never PASS.
    Reasons contain only assertion identifiers, never observed private values.
    """
    problems: list[str] = []
    failures: list[str] = []
    used: list[str] = []
    fields = (context.run_id, context.org_id, context.step_id,
              context.executor_id, context.verifier_id)
    if not all(_nonempty(field) for field in fields):
        problems.append("Invalid step context")
    if context.executor_id == context.verifier_id:
        problems.append("Executor and verifier must be distinct")
    if not assertions:
        problems.append("No required assertions")

    by_assertion: dict[str, AssertionSpec] = {}
    for spec in assertions:
        if not _nonempty(spec.assertion_id) or spec.assertion_id in by_assertion:
            problems.append("Invalid or duplicate assertion identifier")
            continue
        by_assertion[spec.assertion_id] = spec
        value_type = VALUE_TYPES.get(spec.kind)
        if value_type is None or type(spec.expected) is not value_type:
            problems.append(f"Invalid expected value type for {spec.assertion_id}")
        if spec.operator not in OPERATORS or (
            spec.operator == "contains" and
            (spec.kind != "DOM_TEXT" or not spec.expected)
        ):
            problems.append(f"Invalid operator for {spec.assertion_id}")

    by_observation: dict[str, Observation] = {}
    evidence_ids: set[str] = set()
    for observation in observations:
        if not _nonempty(observation.evidence_id) or observation.evidence_id in evidence_ids:
            problems.append("Invalid or duplicate evidence identifier")
        else:
            evidence_ids.add(observation.evidence_id)
        if not _nonempty(observation.assertion_id) or observation.assertion_id in by_observation:
            problems.append("Invalid or duplicate observed assertion")
        else:
            by_observation[observation.assertion_id] = observation
        if observation.assertion_id not in by_assertion:
            problems.append("Observation for unknown assertion")
        if (observation.run_id != context.run_id or
            observation.org_id != context.org_id or
            observation.step_id != context.step_id or
            observation.collector_id != context.executor_id):
            problems.append("Observation provenance mismatch")

    for assertion_id, spec in by_assertion.items():
        observation = by_observation.get(assertion_id)
        if observation is None:
            problems.append(f"Missing evidence for {assertion_id}")
            continue
        if observation.kind != spec.kind or type(observation.actual) is not VALUE_TYPES.get(spec.kind):
            problems.append(f"Invalid evidence kind or type for {assertion_id}")
            continue
        used.append(observation.evidence_id)
        # Skip comparison if the specification is malformed.
        if (type(spec.expected) is not VALUE_TYPES.get(spec.kind) or
            spec.operator not in OPERATORS or
            (spec.operator == "contains" and (spec.kind != "DOM_TEXT" or not spec.expected))):
            continue
        matched = (observation.actual == spec.expected if spec.operator == "eq"
                   else spec.expected in observation.actual)
        if not matched:
            failures.append(f"Assertion mismatch: {assertion_id}")

    if problems:
        return Verification(NEEDS_REVIEW, tuple(problems + failures), tuple(used))
    if failures:
        return Verification(FUNCTIONAL_FAIL, tuple(failures), tuple(used))
    return Verification(PASS, (), tuple(used))
