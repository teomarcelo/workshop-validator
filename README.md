# Workshop Validator

[![Public Python CI](https://github.com/teomarcelo/workshop-validator/actions/workflows/ci.yml/badge.svg)](https://github.com/teomarcelo/workshop-validator/actions/workflows/ci.yml)

**Purpose:** Validate Salesforce workshop exercise-guide instructions against a specifically provisioned, disposable trial org, with an evidence-backed **quick HTML summary** and a faithful **annotated PDF review copy**.

> **Current status:** Tested prototype with synthetic browser exercises. **Not yet verified end-to-end against a real Salesforce org.** Real workshop PDFs, credentials, screenshots, reports and source-specific step manifests must stay outside this public repository.

## Outputs

| Output | Audience | Behavior |
| --- | --- | --- |
| Quick HTML validation report | Instructor / QA reviewer | Status of every planned step, evidence references and findings. An incomplete/unreconciled guide is never labeled fully verified. |
| Annotated PDF review copy | Guide owner / instructor | Original vector content is retained, while concise instructor-check notes appear directly under affected instructions; content below moves down without overlap. Includes a one-page quick summary. No silent source-guide rewrite. |
| Private run artifacts | QA engineering | Screenshot per executed step where successful, local SQLite checkpoint, evidence and sanitized JSON status. **Private environment only.** |

## What works today

- Conservative PDF candidate extraction with original page coordinates, numbering checks and ambiguity flags.
- Explicit full-page source reconciliation gate; draft checklists start *unresolved*, never auto-attested.
- Dependency-aware Option A behavior (independent steps may continue after an HTTP 429; dependent steps do not pass).
- Deterministic Playwright role/name locator execution, observation collection and independently checked expected assertions.
- SQLite checkpoints with version fencing and bounded 429 retry queue; bounded same-job cooldown retries.
- HTML reporting, vector-page-preserving PDF instructor annotations, and conservative unverified UI locator suggestions.
- Standard GitHub-hosted public Actions CI, including **actual headless Chromium synthetic smoke tests**.

## Not yet complete

- **Real behavioral correctness is unverified**: synthetic Playwright checks cannot establish whether current workshop instructions are necessary, obsolete or automatically satisfied. Source numbering is editorial, not a UI defect. Live testing must examine before-state, literal action, downstream result and safe omission/negative-control checks for every actionable step.

- Private cloud runner has **not** been deployed; its [template](templates/private-runner/README.md) needs an approved private repo and protected secrets.
- The first pilot's 32-page AGT503 PDF needs a trustworthy page-by-page coverage review and an exact authored executable plan.
- No real Salesforce login, Salesforce UI step, Agentforce LLM response or full exercise flow has been independently verified.
- Agentic arbitrary UI repair, cryptographic evidence provenance, org baseline reset, cross-run durable cloud resume and the fully automated plan-to-Playwright loop are not implemented. The zero-dollar budget excludes paid model inference.
- Direct user credentials previously pasted in chat **must be rotated** and configured solely in an approved private environment before live testing.

## Get started (public-safe synthetic CI)

The source repository runs Python tests and synthetic Chromium tests on every push and PR. Local commands:

\`\`\`bash
python -m pip install -r requirements-dev.txt playwright==1.57.0
python -m unittest discover -s tests -v
python -m playwright install chromium
RUN_BROWSER_SMOKE=1 python -m unittest discover -s tests -p test_browser_worker.py -v
RUN_BROWSER_SMOKE=1 python -m unittest discover -s tests -p test_pipeline.py -v
RUN_BROWSER_SMOKE=1 python -m unittest discover -s tests -p test_discovery.py -v
\`\`\`

For a **private** guide only, the approved operator can generate an unresolved review checklist:

\`\`\`bash
python -m workshop_validator.review_draft /private/guide.pdf --output /private/review-draft.json
\`\`\`

For the pilot's standard Salesforce username/password login, source-bound authored steps and local private report generation, see [Private Runner](docs/PRIVATE_RUNNER.md). It is not authorized for the public CI.

## Project documentation

- [Living project plan](PROJECT_PLAN.md)
- [Zero-cost cloud architecture](docs/ZERO_COST_CLOUD_ARCHITECTURE.md)
- [Source guide reconciliation](docs/SOURCE_RECONCILIATION.md)
- [Evidence gate](docs/EVIDENCE_GATE.md)
- [Behavioral step verification](docs/BEHAVIORAL_VERIFICATION.md)
- [Bounded 429 recovery](docs/RETRIES.md)
- [Pipeline](docs/PIPELINE.md)
- [HTML report](docs/HTML_REPORT.md)
- [Inline annotated PDF](docs/PDF_INLINE.md)
- [Legacy separate-sheet PDF](docs/PDF_ANNOTATIONS.md)
- [UI discrepancy discovery](docs/UI_DISCOVERY.md)
- [Private-runner template](templates/private-runner/README.md)
- [Automation log](AUTOMATION_LOG.md)

## Security and budget

The code is public; workshop materials are not. Never put org credentials, guide PDFs, step manifests, raw browser traces, customer data, screenshots or full reports in public commits, issues, PRs, Actions artifacts or logs. A secret-bearing live runner needs a **separate approved private execution environment** and protected storage.

The agreed target is **$0 incremental monthly infrastructure**, using public Actions standard runners for synthetic tests and the included private GitHub Free allowance only after checking actual billing limits. No paid LLM API, permanent server or private infrastructure has been provisioned by this project.
