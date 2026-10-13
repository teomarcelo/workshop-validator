# End-to-end evidence-gated validation pipeline (prototype)

`workshop_validator.pipeline.validate_plan(page, db_path, run_id, org_id, plans, ...)` now combines the existing deterministic modules for a **single isolated run**: trusted pre-authored UI steps, dependency-aware Option A scheduling, Playwright actions and observations, independent trusted-assertion verification in SQLite checkpoints, and private HTML summary reporting.

The caller is responsible for an authenticated Playwright `Page` from an **approved private runtime**, an exact trusted step/action mapping, and a page-by-page reviewed source inventory. The pipeline deliberately does not log in, read secrets, discover browser steps from private PDF text, generate AI plans, queue retries or publish artifacts.

Fail-closed properties:
- A fully reconciled source must include the **exact** set of action IDs in the executable plan; missing/extra steps raise `PlanMismatch`.
- Every planned step must define required expected assertions from the source manifest, and only the independent verifier can transition it to PASS.
- Missing selectors/observations result in NEEDS_REVIEW rather than PASS.
- A trustworthy classified HTTP 429 raised as `RateLimitDetected` marks the blocked step ENV_BLOCKED, holds its dependents, and permits unrelated steps to proceed (approved Option A).
- Even if all executed steps PASS, a report remains INCOMPLETE when source inventory reconciliation is not established.
- An exception outside known browser/classification errors intentionally aborts the run instead of inventing a status. Crash reconciliation / retries are not yet implemented.

In public CI, `tests/test_pipeline.py` uses synthetic HTML with Chromium only: verifies the full two-step path, rejects incomplete inventories, simulates a 429, and rejects missing selectors. No real Salesforce org information is involved.

**Limitations:** The source reconciliation flag is only as reliable as the approved caller and stored reviewer attestations. This prototype does not cryptographically authenticate browser collectors, does not maintain remote persisted evidence, and does not support fully automated extraction-to-execution for an arbitrary workshop guide. Do not call it a production-grade autonomous validator.
