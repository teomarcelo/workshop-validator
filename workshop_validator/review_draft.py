"""Generate a private, explicitly UNRESOLVED source-review checklist.

This makes it possible for instructors to audit a guide page by page without
silently accepting imperfect PDF text extraction. It NEVER auto-attests that
visuals were examined or all actions were inventoried.
"""

from pathlib import Path
import argparse
import json
from datetime import datetime, timezone

from .guide_inventory import extract_inventory
from .reconciliation import file_sha256, inventory_sha256


def make_draft(guide_path: str | Path) -> dict:
    inv = extract_inventory(guide_path)
    by_page = {page: [] for page in range(1, inv.page_count + 1)}
    for item in inv.steps:
        by_page[item.page].append({
            "step_id": item.step_id,
            "disposition": "UNRESOLVED",
            "reviewed_warnings": [],
            "rationale": "",
        })
    return {
        "pdf_sha256": file_sha256(guide_path),
        "inventory_sha256": inventory_sha256(inv),
        "reviewer_id": "",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_inventory_warnings": [],
        "pages": [{
            "page": number, "text_checked": False, "visuals_checked": False,
            "no_other_actions": False,
            "candidates": by_page[number], "added_actions": [],
            "unresolved": ["Page review has not been performed."],
        } for number in by_page],
        "_draft_metadata": {
            "page_count": inv.page_count,
            "candidate_step_count": len(inv.steps),
            "flagged_step_count": sum(bool(step.warnings) for step in inv.steps),
            "warnings": list(inv.warnings),
            "verified_complete": False,
            "notice": "This is a checklist, not an attestation or executed UI plan."
        }
    }


def main():
    p = argparse.ArgumentParser(description="Make a PRIVATE unresolved page-review checklist")
    p.add_argument("guide")
    p.add_argument("--output", required=True)
    args = p.parse_args()
    path = Path(args.output)
    if path.exists():
        p.error("Refusing to overwrite existing private review")
    review = make_draft(args.guide)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    with path.open("x", encoding="utf-8") as out:
        json.dump(review, out, indent=2)
    path.chmod(0o600)
    print(json.dumps({"page_count": review["_draft_metadata"]["page_count"],
        "candidate_steps": review["_draft_metadata"]["candidate_step_count"],
        "verified_complete": False}))
if __name__ == "__main__":
    main()
