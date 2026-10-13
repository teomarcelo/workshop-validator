"""Typed Playwright executor and observation collector for an authored step.

Does not log in, discover Salesforce selectors, store secrets, or mark PASS.
The independent evidence gate decides status from trusted expected assertions.
"""

from dataclasses import dataclass
from typing import Sequence

from .evidence import AssertionSpec, Observation, StepContext, Verification, verify_step


class BrowserActionError(RuntimeError):
    """Sanitized UI targeting/action failure; no page text or secret is included."""


@dataclass(frozen=True)
class UIAction:
    kind: str  # click | fill
    role: str
    accessible_name: str
    value: str | None = None


@dataclass(frozen=True)
class UIAssertion:
    spec: AssertionSpec
    role: str
    accessible_name: str


def _locator(page, role: str, accessible_name: str):
    if not role or not accessible_name:
        raise BrowserActionError("Missing accessible role/name")
    return page.get_by_role(role, name=accessible_name, exact=True)


def perform_actions(page, actions: Sequence[UIAction], timeout_ms: int = 2000) -> None:
    """Perform only pre-authored allowed UI actions; never derive action from LLM output."""
    from playwright.sync_api import Error as PlaywrightError

    for index, action in enumerate(actions):
        if action.kind not in {"click", "fill"}:
            raise BrowserActionError(f"Unsupported action at index {index}")
        if action.kind == "fill" and not isinstance(action.value, str):
            raise BrowserActionError(f"Missing fill value at index {index}")
        target = _locator(page, action.role, action.accessible_name)
        try:
            if action.kind == "click":
                target.click(timeout=timeout_ms)
            else:
                target.fill(action.value, timeout=timeout_ms)
        except PlaywrightError:
            # Do not expose the playwright error, which may include private DOM.
            raise BrowserActionError(f"UI action failed at index {index}") from None


def collect_observations(page, context: StepContext,
                         assertions: Sequence[UIAssertion]) -> list[Observation]:
    """Collect typed DOM evidence without generating verdicts or raw browser logs."""
    from playwright.sync_api import Error as PlaywrightError

    observed: list[Observation] = []
    for index, item in enumerate(assertions):
        spec = item.spec
        target = _locator(page, item.role, item.accessible_name)
        try:
            # Ambiguous accessible selectors must not produce a passing assertion.
            if target.count() != 1:
                actual = None
            elif spec.kind == "DOM_VISIBLE":
                actual = target.is_visible()
            elif spec.kind == "DOM_TEXT":
                actual = target.inner_text()
            else:
                raise BrowserActionError(f"Unsupported browser assertion at index {index}")
        except PlaywrightError:
            raise BrowserActionError(f"UI observation failed at index {index}") from None
        observed.append(Observation(
            evidence_id=f"{context.run_id}:{context.step_id}:dom:{index}",
            assertion_id=spec.assertion_id, kind=spec.kind, actual=actual,
            run_id=context.run_id, org_id=context.org_id,
            step_id=context.step_id, collector_id=context.executor_id,
        ))
    return observed


def check_authored_step(page, context: StepContext,
                        actions: Sequence[UIAction],
                        expected: Sequence[UIAssertion]) -> Verification:
    """Execute, collect, then call the distinct verifier; synthetic demo only.

    An executor/collector can still forge evidence at this stage; production
    needs authentic browser provenance, immutable manifest and private storage.
    """
    if not expected:
        raise BrowserActionError("Expected assertions cannot be empty")
    perform_actions(page, actions)
    observations = collect_observations(page, context, expected)
    return verify_step(context, [i.spec for i in expected], observations)
