# Inline annotated PDF (current review-copy format)

The preferred instructor PDF now places concise review notes **directly beneath affected source instructions**, not on separate afterthought sheets. A short quick-summary page appears first. Notes sit in newly inserted vertical space; all subsequent guide text, code examples, images and screenshots move down without being covered.

The PDF's original vector artwork is copied as exact-scale, clipped strips. **Affected pages become slightly taller**; original page numbering remains in the guide footer. Unaffected pages render the same as the source, and synthetic tests verify that rendering and ordering. Keeping fixed-height pages while inserting new content would require much more disruptive reflow.

Use only with an approved private PDF and PRIVATE note manifest:

\`\`\`sh
python -m workshop_validator.pdf_inline /private/guide.pdf \
  --notes /private/inline-notes.json --output /private/annotated-review.pdf
\`\`\`

The private JSON entries need reviewed page-specific PDF coordinates: \`source_page\`, \`step_id\`, \`insert_after_y\` (top-origin points), \`summary\`, \`next_check\`, optional \`status\`. Example using synthetic content:

\`\`\`json
[{"source_page": 1, "step_id": "S001", "insert_after_y": 130,
  "summary": "Check whether the generated value is already correct.",
  "next_check": "If so, safely skip only this edit and verify Save."}]
\`\`\`

**Important:** This is a private review copy, not proof of a real Salesforce change. Notes should describe testable findings and clearly distinguish potential redundancy from confirmed behavioral defects. The private runner requires a reviewed \`inline_after_y\` anchor in the confidential executable plan for each flagged instruction. Unknown anchors must fail closed rather than silently place a note on the wrong page. Instructors must visually review placement on every affected page.

The previous separate-sheet engine \`pdf_annotations.py\` remains available for legacy uses, but is not the default for the private runner. Neither original exercise PDFs nor generated review PDFs belong in public GitHub source or public Actions artifacts.
