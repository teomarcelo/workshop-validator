"""Conservative zero-LLM UI discovery proposals from accessible elements.

Suggestions are not corrected instructions, not user approval, and never
unlock a blocked step. A proposed route must be tested from an approved
disposable-org baseline and independently verified before recommending edits.
"""

from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
import re


_ALLOWED_ROLES = frozenset({"button", "menuitem", "link", "tab", "textbox",
                             "option", "checkbox", "combobox"})


@dataclass(frozen=True)
class LocatorProposal:
    role: str
    expected_name: str
    candidate_name: str
    similarity: float
    status: str = "UNVERIFIED_CANDIDATE"


def _normalize(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", s.casefold()))


def name_similarity(expected: str, candidate: str) -> float:
    """Textual similarity is NOT a calibrated correctness/confidence score."""
    a, b = _normalize(expected), _normalize(candidate)
    if not a or not b:
        return 0.0
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    token_score = len(ta & tb) / len(ta | tb)
    return round(0.65 * ratio + 0.35 * token_score, 4)


def propose_locators(page, role: str, expected_name: str, *,
                     min_similarity: float = 0.38, max_results: int = 5,
                     max_scanned: int = 200) -> tuple[LocatorProposal, ...]:
    """Suggest unique, visible accessible elements of the EXPECTED role only.

    Do not scrape whole-page content, try arbitrary javascript or click an
    unverified candidate. Return only bounded text for private review.
    """
    if role not in _ALLOWED_ROLES or not isinstance(expected_name, str) or not expected_name.strip():
        raise ValueError("Unsupported or empty locator")
    if not 0 <= min_similarity <= 1 or not 1 <= max_results <= 20:
        raise ValueError("Invalid discovery budget")
    from playwright.sync_api import Error as PlaywrightError
    try:
        locators = page.get_by_role(role)
        count = min(locators.count(), max_scanned)
        scored: list[LocatorProposal] = []
        seen: set[str] = set()
        for index in range(count):
            el = locators.nth(index)
            if not el.is_visible():
                continue
            label = (el.get_attribute("aria-label") or
                     el.get_attribute("title") or
                     el.get_attribute("placeholder") or
                     el.inner_text(timeout=500) or "").strip()
            label = " ".join(label.split())[:200]
            if not label or label in seen or _normalize(label) == _normalize(expected_name):
                continue
            # Exact accessible name must uniquely resolve to be a credible candidate.
            if page.get_by_role(role, name=label, exact=True).count() != 1:
                continue
            seen.add(label)
            similarity = name_similarity(expected_name, label)
            if similarity >= min_similarity:
                scored.append(LocatorProposal(role, expected_name, label, similarity))
        scored.sort(key=lambda s: (-s.similarity, s.candidate_name))
        return tuple(scored[:max_results])
    except PlaywrightError:
        # Browser discovery failure itself is not a proposed correction.
        return ()


def proposals_to_private_dict(items: tuple[LocatorProposal, ...]) -> list[dict]:
    return [asdict(item) for item in items]
