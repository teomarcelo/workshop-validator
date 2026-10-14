"""Require a complete reviewed source-to-action mapping before private execution.

This is a structural attestation check, not proof of Salesforce success.
"""

from .dependencies import Step, plan_steps
from .reconciliation import reconcile_inventory


class SourcePreflightError(ValueError):
    """Sanitized preflight failure."""


def require_executable_source_review(inventory, review, pdf_digest, plans):
    """Return source action IDs only for a complete page-bound executable plan."""
    if review is None:
        raise SourcePreflightError("A complete page review is required")
    try:
        result = reconcile_inventory(inventory, review, pdf_digest)
        if not result.complete or not result.action_ids:
            raise SourcePreflightError("Source review incomplete")
        candidate_pages = {step.step_id: step.page for step in inventory.steps}
        expected_pages = {}
        for page_review in review.pages:
            for candidate in page_review.candidates:
                if candidate.disposition == "ACTION":
                    expected_pages[candidate.step_id] = candidate_pages[candidate.step_id]
            for added in page_review.added_actions:
                expected_pages[added.action_id] = page_review.page
        ids = tuple(result.action_ids)
        if len(expected_pages) != len(ids) or set(expected_pages) != set(ids):
            raise SourcePreflightError("Source action mapping inconsistent")
        planned_ids = [step.step_id for step in plans]
        if (len(planned_ids) != len(ids) or len(planned_ids) != len(set(planned_ids))
                or set(planned_ids) != set(ids)):
            raise SourcePreflightError("Executable plan does not cover source actions")
        for step in plans:
            if (type(step.page) is not int or step.page != expected_pages[step.step_id]
                    or not step.actions or not step.assertions):
                raise SourcePreflightError("Action lacks page-bound executable verification")
        # Reuse the production Option A graph validator before login.
        # Every dependency must point to a reviewed source action, and cycles
        # cannot be left to fail after an authenticated browser session starts.
        graph = []
        for step in plans:
            dependencies = getattr(step, "dependencies", ())
            if (not isinstance(dependencies, (tuple, list)) or
                    not all(isinstance(dep, str) and dep for dep in dependencies)):
                raise SourcePreflightError("Invalid source dependency graph")
            graph.append(Step(step.step_id, tuple(dependencies),
                              getattr(step, "required", True)))
        try:
            plan_steps(tuple(graph), {})
        except (ValueError, TypeError, RecursionError):
            raise SourcePreflightError("Invalid source dependency graph") from None
        return ids
    except SourcePreflightError:
        raise
    except (TypeError, ValueError, KeyError, AttributeError):
        raise SourcePreflightError("Invalid source review or plan") from None
