# Go-live checklist — first AGT503 disposable Salesforce trial org

> This is an action plan and current status, not a claim that the pilot has passed. Source materials and artifacts are company-private; code/docs are public.

| Gate | Status | Requirement and acceptance evidence |
| --- | --- | --- |
| 1. Public CI unit + browser smoke | **Passed on synthetic data** | GitHub Actions must show green Python and Chromium jobs on current code revision. |
| 2. PDF candidate inventory | **Prototype built** | One exact input PDF SHA-256, page count and candidate list; no silent skipping. |
| 3. Guide completeness attestation | **Required / not complete** | All source pages, screenshots, tables and numbered/unnumbered actions reviewed; every candidate classified, missing actions manually added, reviewer attested and reconciliation gate passed. |
| 4. Source-bound executable plan | **Required / not complete** | Exactly the reviewed action IDs; each action has safe authored locators, dependencies and independent DOM/API assertions. No high-confidence label substitutes for execution. |
| 5. Private credential/document storage | **Required / not configured** | Company-approved input service, permitted private runner, rotated Salesforce credentials in restricted secrets, no private content in public Actions. |
| 6. Manual real Salesforce smoke | **Required / not performed** | One trial-org login; one exercise action executes and meets an independently checked assertion, with private evidence. |
| 7. Real instruction coverage | **Required / not performed** | Every required source action PASS or explicitly BLOCKED/NEEDS_REVIEW; no silent omissions. |
| 8. HTTP 429 and browser recovery | **Partly implemented** | Tested bounded same-job retries/Option A in synthetic tests. Test actual response classification, cooldown and dependency revalidation privately. |
| 9. Secure quick HTML report | **Prototype built** | Real private report captures each status, evidence references, and reproducible run ID; no public data leak. |
| 10. Annotated PDF review copy | **Prototype built** | Preserve original page pixels, insert notes adjacent by page, show findings and proposed correction without automatically rewriting source. |
| 11. Cross-run private durability | **Not implemented** | Persist encrypted checkpoint and signed evidence in approved private storage; restore after interruption without duplicate destructive side effects. |
| 12. Scheduled unattended run | **Not enabled** | Manual pilot green, cost allowance checked, private schedule enabled, and one real run completed with laptop closed. |
| 13. Autonomous multi-agent repair | **Not implemented at $0** | A proposal, test from clean baseline, independent verifier and instructor approval are required. Any paid LLM requires a separate budget and approval. |

## First operational blocker

The GitHub connector can commit public-safe code but **cannot create a private repository, set repository/environment secrets, upload approved confidential PDFs, or provision corporate private storage**. An authorized operator must arrange that private boundary before we can execute AGT503 with live credentials. See [private-runner template](../templates/private-runner/README.md).

No new paid infrastructure or external AI API has been authorized. Stop before a subscription or metered charge.
