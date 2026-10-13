# Page-by-page source reconciliation

The `workshop_validator.reconciliation` module checks a private review of every page and candidate extracted from a PDF. It does not independently prove that a guide is complete or that a Salesforce step passed.

## Private review procedure

1. Extract the inventory from an approved private PDF. Record `file_sha256(pdf_path)` and `inventory_sha256(inventory)` in a `SourceReview` along with a reviewer identifier and timezone-aware timestamp. If either digest changes, review is invalid.
2. Visually inspect every rendered page, including pages without extracted steps. Review text, screenshots, tables, callouts, non-numbered actions, and cross-page instructions. Only then set `text_checked`, `visuals_checked`, and `no_other_actions`.
3. Review every extracted candidate exactly once: `ACTION`, `NOT_ACTION` (with a rationale), or `UNRESOLVED` (blocks completion). Explicitly acknowledge every candidate and inventory warning. Add missed actions with a unique identifier and a private source-region reference.
4. Call `reconcile_inventory(inventory, review, file_sha256(pdf_path))`. On any issue the result is incomplete and has no approved action IDs. A complete result is a consistent reviewer attestation, not proof of objective completeness. It does not grant execution or PASS status.

The PDF, inventory, source review, source references and resulting action IDs remain private. Public CI runs only synthetic tests. The checker does not provide a review UI, second-person audit, manifest-to-browser planner or real Salesforce validation.

## Synthetic verification

```sh
python -W error::ResourceWarning -m unittest discover -s tests -p test_reconciliation.py -v
```

Tests cover missing pages and candidates, duplicated entries, unresolved instructions, visual checks, warning acknowledgment, additional actions, exclusions and source-digest mismatch.
