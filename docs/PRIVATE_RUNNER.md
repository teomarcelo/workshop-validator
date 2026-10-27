# Private workshop validator CLI (pilot adapter)

`python -m workshop_validator.private_runner --guide /private/guide.pdf --plan /private/approved-plan.json --review /private/page-review.json --output /private/new-run`

**Never run this command inside the public GitHub Actions workflow.** This code is a connector for a separately approved **private** execution environment. You must configure the single disposable trial-org `SF_USERNAME` and `SF_PASSWORD` in that environment's secret store (after rotating any password previously pasted into a chat); they must not appear in source, command arguments, logs or GitHub Actions public artifacts.

The guide is read as a local private file. A required human-reviewed JSON executable plan is pinned to the exact SHA-256 of the guide. Optional typed page reviews are fed to the independent source reconciliation gate; without an attested, complete page review, the HTML result stays INCOMPLETE even when all planned steps pass. User cannot turn unreviewed PDF text into a green "all exercise steps verified" claim merely by providing a short plan.

**Output in the private working directory**: sanitized `status.json`, step-by-step `validation-summary.html`, `checkpoint.sqlite`, a screenshot per executed step that permits it, and an `annotated-instructor-review.pdf` with short notes directly under flagged steps. Screenshots and report may contain confidential content and require approved private retention and access controls.

The login adapter supports the standard username/password form on `login.salesforce.com` only. It follows the current username-first screen and still accepts an older form that shows both fields at once. It does not bypass SSO/MFA. Actual Salesforce flows and selectors may vary. The pipeline uses pre-authored role/name locators only; autonomous LLM-assisted exact UI path recovery remains unimplemented at $0 AI budget. A classified HTTP 429 blocks the current step and permits truly independent steps to continue, but this CLI currently records the blocker only; a production runner must integrate the existing SQLite retry queue and durable restart semantics.

Run the guide in printed order. Finish Setup, then each exercise and each task inside it, before starting the next. A step is finished only when it passes, fails for a real mismatch, or is explicitly blocked. If a later exercise was already touched, return to the earliest unfinished step.

Report every step as PASS or FAIL under its exercise and task. A task is PASS only when every step in it passed. Keep a failed step visible when later steps still ran, and mark steps that were not run as NOT RUN. Do not record a UI failure while the page is still loading.

**Exit codes**: 0 only for independently verified required steps with a complete source review; 2 for incomplete/blocked/invalid. A private local login to one disposable trial org has succeeded with the username-first form. Full guide execution, page attestation, and an authored executable plan are still not done.

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


### Inline note layout (required for every flagged step)

Each **private** plan step that might produce a finding must supply an instructor-reviewed
\`inline_after_y\` PDF coordinate (points measured from the top of the original source
page), located *after the relevant instruction and any associated code/screenshot*.
For example, add \`"inline_after_y": 135\` alongside the step's \`page\` in the
private plan. Source coordinates are confidential and never committed publicly.

When a finding exists but its source anchor was not reviewed, PDF generation
**fails closed** instead of placing a disconnected note or obscuring screenshots.
The source-review PDF generates a one-page findings summary followed by concise
inline check cards. Subsequent guide content is shifted downward, and affected
pages grow taller; original page numbers remain on the guide's footers.

The browser result still must be proven by a real Salesforce trial-org run. A
potentially redundant generated default is not an automatically approved edit.

## Claiming orgs

The team claims new verification orgs itself (W-042). The owner gives only
the event code. Put it in `~/.workshop-validator/org-claim.json`, mode 0600:

```json
{"portal_url": "<portal_url>", "event_code": "<event_code>"}
```

- `python -m workshop_validator.org_claim` (or `--dry-run`) opens the portal,
  follows Add Event, checks that the event-code field and its submit button
  exist, then stops. Nothing is submitted.
- `python -m workshop_validator.org_claim --claim` submits the code, opens the
  claimed org's details, reads the username and password by label, and
  appends one org to `verification-orgs.json` (atomic write under a file
  lock, mode 0600). It prints only `org-claim: CLAIMED org <n>`. When the
  values are unclear it writes nothing and prints `NEEDS_REVIEW`.
- `--claim` is refused unless the last run said move on (a success or a real
  failure) and no unused org is left (W-043). `--force` skips that check. It
  works only when the owner runs it at a terminal.
- One claim per run: before the browser opens, the tool creates
  `~/.workshop-validator/team/claim-attempt` (exclusive create, mode 0600)
  with the gate's last run id. Two claims at once cannot both start. A second `--claim` for the same run is refused unless the first one
  ended `CLAIMED`.
- `--guides` downloads the guide PDFs linked on the portal into
  `~/.workshop-validator/team/guide-inbox/` (folder 0700, files 0600) and
  prints the count. Only https links on the portal's host are fetched; others
  are skipped and counted.

The browser stays on https and on the portal's host. After every page load,
redirects included, and again before it types the code or saves an org, it
checks the address. Anywhere else it stops with `left the https portal`.
The tool never prints page text, credentials, the portal address or the code.
Exit codes: 0 done, 1 needs review, 2 refused, 3 stopped (for example sign-in
required).
