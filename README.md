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
- A private local username/password login to one disposable trial org has succeeded. Individual Salesforce UI steps, Agentforce LLM responses, and the full exercise flow have not been independently verified.
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

## Local results site

Start the site on this computer:

```bash
python -m pip install -r requirements-dev.txt playwright==1.57.0
python -m playwright install chromium
python -m workshop_validator.local_site
```

Open http://127.0.0.1:8765 . Upload the exercise-guide PDF and enter the disposable trial org username and password in the form. Do not put them on the command line. The site listens only on 127.0.0.1.

The password is used for that sign-in and is not written to the repo, the results page, or the log. The PDF is kept in a private file outside the repo only while it is read, then removed. Do not commit the PDF, the recording, credentials, screenshots, reports, or the org hostname. If a run is interrupted, delete anything left under `~/.workshop-validator/runs`.

The run reads the PDF, opens a Chrome window, and signs in at login.salesforce.com with the username and password from the form. A blocked login promo is ignored. When a saved sign-in is still logged in, the next upload opens Lightning home and does not load the login page again. It then walks the guide checks that are implemented, in printed order. That browser session is recorded. Sign-in is limited to about two minutes. A step with no check stays NOT RUN. PASS is shown only with evidence. A sign-in failure does not mark the guide failed.

When the run finishes, or if it stops early, stay on the results page and press play on Browser recording. Open the recording plays that same file. The video stays in that run's directory under `~/.workshop-validator/runs`. Runs that finished before recording was added have no video.

## Unattended runs and the agent team

`python -m workshop_validator.automate --guide <pdf> --json` walks the guide on
the current verification org from `~/.workshop-validator/verification-orgs.json`
and writes a private run folder with `report.html`, `results.pdf` and
`summary.json`. Orgs are used one at a time: the next org is used only after a
success or a real failure that came after the org was changed.

In Claude Code, `/validate-guide <pdf>` runs the whole loop with a team of
subagents (run, triage, fix, test, audit, publish as a pull request). See
[Agent team](docs/AGENT_TEAM.md) and [CLAUDE.md](CLAUDE.md) for the rules.

Lessons from runs are private (`~/.workshop-validator/lessons.md`), counted once
per root cause, and never committed.

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
