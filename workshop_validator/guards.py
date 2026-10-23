"""Checks that refuse actions the guide does not name.

These run inside the walk. They never open a browser and never click.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    verdict: str
    detail: str


def approve_auto_layout_restore(step_text: str, toolbar: str) -> Verdict:
    """Free-Form was not in the guide. Allow putting the canvas back on Auto-Layout."""
    toolbar_low = toolbar.lower()
    step_low = step_text.lower()
    if "free-form" not in toolbar_low and "freeform" not in toolbar_low:
        return Verdict("REJECT", "The toolbar is not Free-Form.")
    if "screen element" not in step_low:
        return Verdict("REJECT", "This step is not adding a screen element.")
    return Verdict("PASS", "The toolbar is Free-Form. Put it back on Auto-Layout, then add the screen.")


def checker(step_text: str, action: str) -> Verdict:
    """Reject an action the step text does not name."""
    from .guide_walk import _guide_allows_control

    page = type("Page", (), {"_guide_text": step_text})()
    if not _guide_allows_control(page, action) or action.lower() not in step_text.lower():
        return Verdict("REJECT", f"The step does not say {action}. That click is refused.")
    return Verdict("PASS", f"The step names {action}.")
