# Deterministic UI discrepancy discovery — proposals only

`workshop_validator.discovery.propose_locators` inspects only visible accessible elements of a specified semantic role, excludes hidden, duplicate and ambiguous matches, and returns a bounded set of name-similarity suggestions. It never clicks an alternative, generates arbitrary JavaScript, collects customer data or marks a step PASS. The similarity value is a **textual ranking score**, not evidence of UI correctness or a probability.

When a Playwright target is missing, the private runner may save these candidates as restricted JSON for an instructor or repair agent to consider. The replacement path must then be applied in a disposable-org replay and independently verified against source-defined assertions before a specific guide correction is proposed. User approval is still required before publishing a revised guide.

This is a $0 heuristic useful for simple Salesforce label changes. It cannot autonomously navigate large UI reorganizations or reason across multi-page workflows the way an LLM-backed investigative agent could. Optional LLM agent inference requires explicit cost/security approval; never imply full autonomous arbitrary guide remediation without a tested implementation.

## Locator order

For click and fill actions, the validator uses this priority order to locate controls. All matches must be exact. Never fall back to fuzzy or partial matches.

1. **Role with exact accessible name**: `get_by_role(role, name=name, exact=True)`
2. **Exact text**: `get_by_text(text, exact=True)`
3. **Test ID**: `get_by_test_id(id)` when present

Regex patterns and partial text matches are not used for action targets. If the exact name is not found, the step ends with UI_MISMATCH or NEEDS_REVIEW, never by clicking a look-alike control.
