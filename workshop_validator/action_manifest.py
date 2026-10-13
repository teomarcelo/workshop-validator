"""Fail-closed bridge from private PDF reconciliation to authored browser steps.

Only synthetic fixtures belong in the public repository. An approved manifest
and its source bindings must be stored privately; compilation grants no PASS.
"""

from dataclasses import dataclass
from typing import Mapping, Sequence

from .browser_worker import UIAction, UIAssertion
from .dependencies import Step, plan_steps
from .evidence import OPERATORS, VALUE_TYPES, AssertionSpec
from .guide_inventory import GuideInventory
from .reconciliation import SourceReview, reconcile_inventory


class ManifestError(ValueError):
    """A private action manifest is incomplete or unsafe to execute."""


@dataclass(frozen=True)
class AuthoredStep:
    """A reviewed mapping, NOT an automatically generated PDF instruction."""

    step_id: str
    page: int
    source_ref: str
    dependency_ids: tuple[str, ...]
    actions: tuple[UIAction, ...]
    assertions: tuple[UIAssertion, ...]


@dataclass(frozen=True)
class ActionManifest:
    """Trusted, complete mapping for a private guide revision (not evidence)."""

    pdf_sha256: str
    inventory_sha256: str
    steps: tuple[Step, ...]
    actions: Mapping[str, tuple[UIAction, ...]]
    assertions: Mapping[str, tuple[UIAssertion, ...]]

    def checkpoint_inputs(self) -> tuple[tuple[Step, ...], dict[str, tuple[AssertionSpec, ...]]]:
        """Freeze these expected assertions BEFORE any browser worker runs."""
        return self.steps, {
            step_id: tuple(item.spec for item in items)
            for step_id, items in self.assertions.items()
        }


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_ui(step: AuthoredStep) -> None:
    if not step.actions or not step.assertions:
        raise ManifestError("missing_browser_actions_or_assertions")
    for action in step.actions:
        if not isinstance(action, UIAction) or action.kind not in {"click", "fill"}:
            raise ManifestError("unsupported_browser_action")
        if not _text(action.role) or not _text(action.accessible_name):
            raise ManifestError("invalid_browser_locator")
        if action.kind == "click" and action.value is not None:
            raise ManifestError("unexpected_click_value")
        if action.kind == "fill" and not isinstance(action.value, str):
            raise ManifestError("missing_fill_value")

    seen: set[str] = set()
    for item in step.assertions:
        if not isinstance(item, UIAssertion) or not isinstance(item.spec, AssertionSpec):
            raise ManifestError("invalid_browser_assertion")
        spec = item.spec
        if not _text(item.role) or not _text(item.accessible_name):
            raise ManifestError("invalid_assertion_locator")
        if not _text(spec.assertion_id) or spec.assertion_id in seen:
            raise ManifestError("duplicate_or_invalid_assertion_id")
        seen.add(spec.assertion_id)
        if spec.kind not in {"DOM_VISIBLE", "DOM_TEXT"}:
            raise ManifestError("unsupported_browser_evidence_kind")
        if type(spec.expected) is not VALUE_TYPES.get(spec.kind):
            raise ManifestError("invalid_expected_value_type")
        if spec.operator not in OPERATORS or (
            spec.operator == "contains" and (spec.kind != "DOM_TEXT" or not spec.expected)
        ):
            raise ManifestError("invalid_assertion_operator")


def compile_action_manifest(
    inventory: GuideInventory,
    review: SourceReview,
    actual_pdf_sha256: str,
    authored_steps: Sequence[AuthoredStep],
) -> ActionManifest:
    """Require exact, page-bound coverage of every reconciled ACTION.

    A named reviewer's page-by-page attestation is necessary, not objective
    proof of completeness. Any missing/unmapped action prevents compilation.
    This function does not execute, verify or persist browser observations.
    """
    result = reconcile_inventory(inventory, review, actual_pdf_sha256)
    if not result.complete:
        raise ManifestError("source_reconciliation_incomplete")
    if not result.action_ids:
        raise ManifestError("no_reconciled_actions")

    pages: dict[str, int] = {step.step_id: step.page for step in inventory.steps}
    added_anchors: dict[str, str] = {}
    for page_review in review.pages:
        for added in page_review.added_actions:
            pages[added.action_id] = page_review.page
            added_anchors[added.action_id] = added.source_ref

    authored = tuple(authored_steps)
    authored_ids = [step.step_id for step in authored]
    if (len(authored_ids) != len(set(authored_ids)) or
            set(authored_ids) != set(result.action_ids)):
        raise ManifestError("action_mapping_not_one_to_one")
    by_id = {step.step_id: step for step in authored}

    steps: list[Step] = []
    actions: dict[str, tuple[UIAction, ...]] = {}
    assertions: dict[str, tuple[UIAssertion, ...]] = {}
    for step_id in result.action_ids:
        item = by_id[step_id]
        if (type(item.page) is not int or item.page != pages.get(step_id) or
                not _text(item.source_ref) or
                (step_id in added_anchors and item.source_ref != added_anchors[step_id])):
            raise ManifestError("source_page_or_anchor_mismatch")
        _check_ui(item)
        if not isinstance(item.dependency_ids, tuple):
            raise ManifestError("invalid_dependency_ids")
        steps.append(Step(step_id, item.dependency_ids, required=True))
        actions[step_id] = tuple(item.actions)
        assertions[step_id] = tuple(item.assertions)

    try:
        plan_steps(steps, {})
    except (ValueError, TypeError):
        raise ManifestError("invalid_action_dependencies") from None
    return ActionManifest(actual_pdf_sha256, review.inventory_sha256,
                          tuple(steps), actions, assertions)
