# Annotated PDF review copies

The PDF annotation engine preserves every original source PDF page as a vector/PDF page, in its original order and dimensions, and inserts a readable instructor-review sheet *immediately after* any page containing review notes. Each sheet includes source page, step ID, status, original instruction, finding, proposed correction and private evidence IDs. Multiple notes are supported per source page; long notes continue across sheets instead of being silently clipped.

**Why appended sheets rather than inserting text directly under an instruction?** PDF pages have fixed coordinates. Adding text under instructions without covering the next screenshot requires moving/reflowing content and breaks the requirement to keep original pages pixel-faithful. This implementation preserves source pages byte-content visually (rendered pixels verified in synthetic tests) and provides contiguous review notes. A later reviewed "reflow" layout or optional linked comment bubbles can improve per-step proximity.

Usage (run only in an approved PRIVATE execution environment):

```bash
python -m workshop_validator.pdf_annotations /private/guide.pdf \
  --notes /private/notes.json --output /private/annotated-review.pdf
```

Example **synthetic** notes JSON (do not commit real PDF-derived text or screenshots):

```json
[
  {
    "source_page": 1,
    "step_id": "P001N001",
    "status": "NEEDS_REVIEW",
    "original_instruction": "Click the example button.",
    "finding": "The extraction has not yet been visually reconciled.",
    "proposed_correction": "",
    "evidence_ids": [],
    "illustrative": true
  }
]
```

All source text, note details and generated PDF reports are confidential. Never upload them to the public repository or its public Actions artifacts. The output is a *review copy*, not a silently corrected or authorized student edition. Even a page with no flagged UI changes remains unverified until guide completeness and evidence gates pass.
