# Deterministic UI discrepancy discovery — proposals only

`workshop_validator.discovery.propose_locators` inspects only visible accessible elements of a specified semantic role, excludes hidden, duplicate and ambiguous matches, and returns a bounded set of name-similarity suggestions. It never clicks an alternative, generates arbitrary JavaScript, collects customer data or marks a step PASS. The similarity value is a **textual ranking score**, not evidence of UI correctness or a probability.

When a Playwright target is missing, the private runner may save these candidates as restricted JSON for an instructor or repair agent to consider. The replacement path must then be applied in a disposable-org replay and independently verified against source-defined assertions before a specific guide correction is proposed. User approval is still required before publishing a revised guide.

This is a $0 heuristic useful for simple Salesforce label changes. It cannot autonomously navigate large UI reorganizations or reason across multi-page workflows the way an LLM-backed investigative agent could. Optional LLM agent inference requires explicit cost/security approval; never imply full autonomous arbitrary guide remediation without a tested implementation.
