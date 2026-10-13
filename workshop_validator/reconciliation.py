"""Fail-closed, human-attested page reconciliation for a private PDF inventory.

This is an audit of reviewer attestations, NOT automatic proof that a PDF was
fully understood. The PDF, source-specific reviews and results stay private.
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re

from .guide_inventory import GuideInventory

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DISPOSITIONS = frozenset({"ACTION", "NOT_ACTION", "UNRESOLVED"})


def file_sha256(path: str | Path) -> str:
    """Bind a private review to the exact PDF bytes, without logging them."""
    digest = sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_sha256(inventory: GuideInventory) -> str:
    """Bind a review to the exact extracted candidates, page count and warnings."""
    payload = json.dumps(asdict(inventory), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateReview:
    step_id: str
    disposition: str  # ACTION, NOT_ACTION, UNRESOLVED
    reviewed_warnings: tuple[str, ...] = ()
    rationale: str = ""  # Required for NOT_ACTION; stored privately.


@dataclass(frozen=True)
class AddedAction:
    action_id: str
    source_ref: str  # Private visual/page-region anchor, not source text in public logs.


@dataclass(frozen=True)
class PageReview:
    page: int
    text_checked: bool
    visuals_checked: bool
    no_other_actions: bool
    candidates: tuple[CandidateReview, ...] = ()
    added_actions: tuple[AddedAction, ...] = ()
    unresolved: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceReview:
    pdf_sha256: str
    inventory_sha256: str
    reviewer_id: str
    reviewed_at: str  # ISO 8601 with timezone
    pages: tuple[PageReview, ...]
    reviewed_inventory_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReconciliationResult:
    complete: bool
    action_ids: tuple[str, ...]
    issues: tuple[str, ...]  # Machine-readable, sanitized codes; no PDF content.
    reviewed_pages: int


def _exact_reviewed(actual: tuple[str, ...], expected: tuple[str, ...]) -> bool:
    return len(actual) == len(set(actual)) and set(actual) == set(expected)


def reconcile_inventory(inventory: GuideInventory, review: SourceReview,
                        actual_pdf_sha256: str) -> ReconciliationResult:
    """Require explicit coverage of EVERY page and extracted candidate.

    A complete result means a named reviewer attested to inspecting the PDF's
    text and visuals page by page, accounted for all extraction warnings and
    candidates, and identified any additional actions. It is not an automated
    or independently established claim that no instruction was missed.
    """
    issues: list[str] = []
    if (not _SHA256.fullmatch(actual_pdf_sha256) or
            review.pdf_sha256 != actual_pdf_sha256):
        issues.append("pdf_digest_mismatch")
    if review.inventory_sha256 != inventory_sha256(inventory):
        issues.append("inventory_digest_mismatch")
    if not isinstance(review.reviewer_id, str) or not review.reviewer_id.strip():
        issues.append("missing_reviewer")
    try:
        timestamp = datetime.fromisoformat(review.reviewed_at)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("naive timestamp")
    except (TypeError, ValueError):
        issues.append("invalid_review_timestamp")
    if not _exact_reviewed(review.reviewed_inventory_warnings, inventory.warnings):
        issues.append("unreviewed_inventory_warnings")
    if inventory.page_count < 1:
        issues.append("invalid_page_count")

    by_page = {page: [] for page in range(1, inventory.page_count + 1)}
    seen_inventory_ids: set[str] = set()
    for step in inventory.steps:
        if step.page not in by_page or not step.step_id or step.step_id in seen_inventory_ids:
            issues.append("invalid_inventory_step")
        else:
            by_page[step.page].append(step)
            seen_inventory_ids.add(step.step_id)

    reviewed_pages: set[int] = set()
    action_ids: list[str] = []
    used_action_ids: set[str] = set(seen_inventory_ids)
    for page_review in review.pages:
        page = page_review.page
        if page not in by_page:
            issues.append("unknown_page")
            continue
        if page in reviewed_pages:
            issues.append(f"page_{page}:duplicate_review")
            continue
        reviewed_pages.add(page)
        if not (page_review.text_checked is True and page_review.visuals_checked is True):
            issues.append(f"page_{page}:inspection_incomplete")
        if page_review.no_other_actions is not True:
            issues.append(f"page_{page}:unlisted_actions_possible")
        if page_review.unresolved:
            issues.append(f"page_{page}:unresolved")

        expected = {step.step_id: step for step in by_page[page]}
        reviewed: set[str] = set()
        for candidate in page_review.candidates:
            step_id = candidate.step_id
            if step_id not in expected:
                issues.append(f"page_{page}:unknown_candidate")
                continue
            if step_id in reviewed:
                issues.append(f"page_{page}:duplicate_candidate")
                continue
            reviewed.add(step_id)
            if candidate.disposition not in _DISPOSITIONS:
                issues.append(f"page_{page}:invalid_disposition")
            elif candidate.disposition == "UNRESOLVED":
                issues.append(f"page_{page}:unresolved_candidate")
            elif candidate.disposition == "NOT_ACTION" and not candidate.rationale.strip():
                issues.append(f"page_{page}:missing_exclusion_rationale")
            elif candidate.disposition == "ACTION":
                action_ids.append(step_id)
            if not _exact_reviewed(candidate.reviewed_warnings, expected[step_id].warnings):
                issues.append(f"page_{page}:unreviewed_candidate_warnings")
        if reviewed != set(expected):
            issues.append(f"page_{page}:missing_candidates")

        for added in page_review.added_actions:
            if (not isinstance(added.action_id, str) or not added.action_id.strip() or
                    not isinstance(added.source_ref, str) or not added.source_ref.strip()):
                issues.append(f"page_{page}:invalid_added_action")
                continue
            if added.action_id in used_action_ids:
                issues.append(f"page_{page}:duplicate_action_id")
                continue
            used_action_ids.add(added.action_id)
            action_ids.append(added.action_id)
    if reviewed_pages != set(by_page):
        issues.append("missing_page_reviews")
    issues = list(dict.fromkeys(issues))
    return ReconciliationResult(not issues, tuple(action_ids) if not issues else (),
                                tuple(issues), len(reviewed_pages))
