# Private page-by-page source review draft

`python -m workshop_validator.review_draft /private/AGT503.pdf --output /private/AGT503-review-draft.json`

Creates a per-page checklist bound by SHA-256 to the original PDF and candidate inventory. Every page defaults to `text_checked: false`, `visuals_checked: false`, `no_other_actions: false`, and an unresolved note; every extracted candidate defaults to `UNRESOLVED`. This means the checklist **cannot** prove source completeness until an authorized reviewer inspects each PDF page, verifies screenshots/tables and all numbered and unnumbered instructions, marks action candidates, adds missed actions, resolves warnings and signs the attestation.

The source review can be consumed by `private_runner.load_source_review` and the fail-closed `reconcile_inventory` gate. Never fabricate a reviewer's identity, visual inspection, or approval. This draft is PRIVATE and contains source-specific IDs/digests; do not commit it or publish it as a public Actions artifact.

This does **not** generate a complete executable Playwright script from arbitrary guide content. That remains a separate inference/planning and grounded verification milestone.
