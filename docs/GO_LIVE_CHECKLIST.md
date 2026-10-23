# Go-live checklist — first AGT503 disposable Salesforce trial org

> This is an action plan and current status, not a claim that the pilot has passed. Source materials and artifacts are company-private; code/docs are public.

| Gate | Status | Requirement and acceptance evidence |
| --- | --- | --- |
| 1. Public CI unit + browser smoke | **Passed on synthetic data** | GitHub Actions must show green Python and Chromium jobs on current code revision. |
| 2. PDF candidate inventory | **Private draft extracted** | The supplied 32-page guide produced a private candidate list. It is not an attested inventory. |
| 3. Guide completeness attestation | **Required / not complete** | All source pages, screenshots, tables and numbered/unnumbered actions reviewed; every candidate classified, missing actions manually added, reviewer attested and reconciliation gate passed. |
| 4. Source-bound executable plan | **Required / not complete** | Exactly the reviewed action IDs; each action has safe authored locators, dependencies and independent DOM/API assertions. No high-confidence label substitutes for execution. |
| 5. Private credential/document storage | **Required / not configured** | Company-approved input service, permitted private runner, rotated Salesforce credentials in restricted secrets, no private content in public Actions. |
| 6. Manual real Salesforce smoke | **One setup action checked** | Username-first login succeeded. Adding Agentforce Action Access to the agent user was saved and showed on the user record. The rest of the guide is not executed. |
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

## Behavioral QA acceptance

Before claiming a full-guide PASS:
1. Establish the exact current disposable-org baseline and record successful execution of prerequisite guide steps.
2. For each guide instruction/substep, capture required state BEFORE applying the edit; distinguish user-necessary work from already-generated defaults.
3. Attempt the literal guide instruction, verify its actual effect, and run downstream assertions rather than relying on screenshots.
4. For suspected redundant actions, run a clean-baseline skip-only-that-action replay and verify downstream behavior. Propose removals only after sensitive negative controls also fail as expected.
5. Deliberately break a safe reversible synthetic/disposable-org property and confirm the same independent assertions reject it. Restore the baseline.
6. Keep cosmetic numbering comments in a separate editorial appendix, never as the principal UI-defect finding.
7. Keep both quick HTML summary and annotated instructor PDF as evidence-bound outputs. Any unresolved action prevents a fully green report.

Current status: only synthetic sensitivity checks are implemented. Private real-org workflows and full coverage review remain outstanding.
