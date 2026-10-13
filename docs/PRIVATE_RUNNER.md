# Private workshop validator CLI (pilot adapter)

`python -m workshop_validator.private_runner --guide /private/guide.pdf --plan /private/approved-plan.json --review /private/page-review.json --output /private/new-run`

**Never run this command inside the public GitHub Actions workflow.** This code is a connector for a separately approved **private** execution environment. You must configure the single disposable trial-org `SF_USERNAME` and `SF_PASSWORD` in that environment's secret store (after rotating any password previously pasted into a chat); they must not appear in source, command arguments, logs or GitHub Actions public artifacts.

The guide is read as a local private file. A required human-reviewed JSON executable plan is pinned to the exact SHA-256 of the guide. Optional typed page reviews are fed to the independent source reconciliation gate; without an attested, complete page review, the HTML result stays INCOMPLETE even when all planned steps pass. User cannot turn unreviewed PDF text into a green "all exercise steps verified" claim merely by providing a short plan.

**Output in the private working directory**: sanitized `status.json`, step-by-step `validation-summary.html`, `checkpoint.sqlite`, a screenshot per executed step that permits it, and `annotated-instructor-review.pdf` when findings remain. Screenshots and report may contain confidential content and require approved private retention and access controls.

The login adapter supports the standard username/password form on `login.salesforce.com` only. It does not bypass SSO/MFA. Actual Salesforce flows and selectors may vary. The pipeline uses pre-authored role/name locators only; autonomous LLM-assisted exact UI path recovery remains unimplemented at $0 AI budget. A classified HTTP 429 blocks the current step and permits truly independent steps to continue, but this CLI currently records the blocker only; a production runner must integrate the existing SQLite retry queue and durable restart semantics.

**Exit codes**: 0 only for independently verified required steps with a complete source review; 2 for incomplete/blocked/invalid. Live Salesforce authentication and full guide execution have NOT been tested or deployed at the time this code was written.

Example private plan format is shown below using synthetic content only:

```json
{
  "schema_version": 1,
  "guide_sha256": "REPLACE_WITH_PRIVATE_SOURCE_PDF_SHA256",
  "steps": [
    {
      "step_id": "S001",
      "page": 1,
      "title": "Open the synthetic builder",
      "dependencies": [],
      "required": true,
      "actions": [{"kind": "click", "role": "button", "accessible_name": "Save"}],
      "assertions": [
        {"assertion_id": "saved", "kind": "DOM_VISIBLE", "expected": true,
         "role": "status", "accessible_name": "Save status"}
      ]
    }
  ]
}
```

A real guide's full source text and per-step plan are private data; do not commit them to this public repository.
