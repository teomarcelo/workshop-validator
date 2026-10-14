# Automation Log

## 2026-10-13 09:27 PDT — Run 1

Milestone: Option A dependency-aware step scheduler.

Implementation commit: d7c04c864446bc0b2ba354bd80d15b42d0510084.

Changes: Added a pure Python dependency planner, twelve unit tests, usage documentation, and a gitignore. Independent steps can proceed while downstream steps remain held. Cycles, unknown dependencies, and premature PASS are rejected.

Commands executed in a local container:
- python3 --version: Python 3.13.5.
- python3 -m unittest discover -s tests -v: 12 passed, OK.
- python3 -m compileall -q workshop_validator tests: succeeded.
- git hash-object on five new files: matched uploaded Git blobs.
- git ls-remote: failed because the local container could not resolve github.com; the GitHub connector was used for the commit.

Scope: No PDF parsing, browser tests, live org access, cloud deployment, or durable orchestration occurred.

Blockers: No private pilot PDF, configured secret store, browser worker, or cloud orchestration was available for this milestone.

Next: Implement and test an independent per-step evidence verifier before accepting PASS from execution.

## 2026-10-13 10:27 PDT — Run 2

Milestone: Independent per-step evidence gate.
Implementation commit: a7cf86567f01766b5f8065dd8f44de371f05892c.

Changes: Added workshop_validator/evidence.py, tests/test_evidence.py, and docs/EVIDENCE_GATE.md. The gate verifies typed assertions against run/org/step-bound observations. Missing or malformed evidence is NEEDS_REVIEW, a mismatched observation is FUNCTIONAL_FAIL, and only fully matching required assertions receive PASS.

Commands actually executed in local container:
- git clone --depth 1: failed (DNS unavailable).
- python3 --version: Python 3.13.5.
- python3 -m unittest discover -s tests -v: 18 new tests passed.
- python3 -m compileall -q workshop_validator tests: succeeded.
- python3 -m unittest discover -s tests -q: 18 new tests passed after fixture adjustment.
- git hash-object on three new files: hashes matched uploaded GitHub blobs.

Scope: Existing scheduler tests were not rerun; no Salesforce login, PDF extraction, browser test, or cloud deployment occurred.
Blockers: Pilot PDF, approved cloud credential store, browser worker, and durable runtime are not configured. Collector identity is not cryptographically verified.
Next: Add a durable status transition gate connecting verified outcomes to dependency planning.


## 2026-10-13 11:26 PDT — Run 3

Milestone: Transactional, evidence-gated SQLite checkpoint prototype.

Implementation commit: be459a9b956d4e7c2d084c95561f625732110eba.

Changes: Added workshop_validator/checkpoints.py, tests/test_checkpoints.py, and docs/CHECKPOINTS.md. Run manifests and expected assertions are frozen at creation; atomic claims use attempt-version checks. A step can become PASS only through the independent verifier using stored assertions. Blocked branches preserve Option A continuation of independent steps. Added SQLite database patterns to .gitignore.

Commands actually executed in a local container:
- git clone --depth 1: failed because github.com DNS resolution was unavailable; fetched the existing source/tests through the GitHub connector and recreated them locally for validation.
- python3 -m unittest discover -s /tmp/wv_run3/repo/tests -v: initially failed because the command ran outside the package root; corrected working directory.
- python3 -m unittest discover -s tests -v: 15 new checkpoint tests passed.
- python3 -W error::ResourceWarning -m unittest discover -s tests -v: 15 checkpoint tests passed after fixing a test connection warning.
- python3 -W error::ResourceWarning -m unittest discover -s tests -q: 45 tests passed (15 new + 12 scheduler + 18 evidence tests).
- python3 -m compileall -q workshop_validator tests: succeeded.
- git hash-object on new files: hashes matched the blobs committed through GitHub; post-commit fetch verified all four implementation file SHA values.

Scope: Local SQLite persistence and state-transition tests only; no Salesforce org login, private PDF processing, Playwright execution, actual HTTP 429 request, deployed cloud runner, or durable cloud workflow. A local checkpoint database is not a cloud orchestrator.

Blockers: No configured approved secret manager, private pilot PDF in the runner, browser worker, or cloud durable workflow. Authenticated evidence provenance, worker leases/crash reconciliation, and 429 cooldown/retry queues remain unimplemented.

Next: Implement a bounded, rate-limit-aware retry state machine with persisted Retry-After cooldowns and stale-worker recovery, without ever turning blocked steps into PASS.

## 2026-10-13 12:28 PDT — Run 4

G1 milestone: Verified existing public CI workflow. Run 38455037627 completed successfully on commit 8b5595b8bf8b93e968ce2489866386952a4b06e3. The GitHub Actions job completed both commands: `python -m unittest discover -s tests -v` and `python -m compileall -q workshop_validator tests`.

Local commands: `python3 --version` returned Python 3.13.5; `git --version` returned git 2.47.3; `git ls-remote` failed due to DNS resolution. No local tests were run. The CI workflow was already committed before this execution, so no duplicate workflow was created.

Limitations: No PDF or browser integration or live-org validation occurred. Next milestone: G2 synthetic PDF and Playwright end-to-end integration. No private execution environment is deployed.

## 2026-10-13 12:42 PDT — Interactive build sprint

Milestones completed:
- Created and verified the first public GitHub Actions Python unit/compile workflow: commit 8b5595b8bf8b93e968ce2489866386952a4b06e3; green cloud run https://github.com/teomarcelo/workshop-validator/actions/runs/38455037627.
- Added a conservative, license-conscious, page-linked PDF instruction inventory with synthetic PDF fixture tests: commit a517be421b4c113494d6b61e05b9a57242bfb506; green cloud run https://github.com/teomarcelo/workshop-validator/actions/runs/38456016853.
- Added typed Playwright Chromium fill/click actions, DOM observation collection and independent evidence verification, including synthetic PDF-to-UI and negative tests: commit cfc3353f0c2a25abe03c953007e9e2f963f87f22; green GitHub Actions browser + unit run https://github.com/teomarcelo/workshop-validator/actions/runs/38456494568.
- Added escaped private HTML report generator with no-false-green gates, unit tests, and updated PROJECT_PLAN.md: commit ea006c86708fcbb74d41997c5c12f034d660ef0e; green GitHub Actions browser + unit run https://github.com/teomarcelo/workshop-validator/actions/runs/38456836225.

Actual local tests: synthetic PDF parser 7 tests passed; synthetic Playwright smoke 6 tests passed with system Chromium. The local browser smoke used a temporary shape-compatible evidence shim, because the full connected GitHub repository was not locally cloned (network DNS unavailable); authoritative GitHub Actions success above used the actual committed independent evidence module. The report module's 6 local tests passed. Git blob SHA comparison verified that tested new module/test bytes matched the uploaded code. A privately supplied PDF was scanned in the local container but its contents, artifacts, credentials and source-derived manifests were not committed to this public repository.

Current scope: **No live Salesforce login or instruction was attempted.** The source inventory is candidate-only and explicitly unverified. Public CI tests are safe synthetic jobs; they are not the private cloud validation runner. No paid cloud resources or external LLM APIs were provisioned.

Next: Add deterministic source reconciliation and trusted action-manifest interface; integrate browser observations with durable checkpoint transitions and private HTML reporting. Actual org validation is blocked on a separately approved PRIVATE execution workflow, company-approved source PDF access, and new operator-entered rotated org secrets. Preserve the $0 budget and prohibit confidential artifacts in public GitHub Actions.

## 2026-10-13 13:29 PDT — Scheduled run 5

Implemented a fail-closed, page-by-page PDF reconciliation review gate, with 17 synthetic tests and documentation. Implementation commit: 35c69ac822a38e8c059d70e230aaf0b861160587.

Local commands: git clone --depth 1 (failed: DNS unavailable); python3 -W error::ResourceWarning -m unittest discover -s tests -v (17 new tests passed using a minimal GuideInventory stand-in); python3 -m compileall -q workshop_validator tests (passed); git hash-object (new source/test bytes matched GitHub blobs). Full repository tests were not run locally. Prior green public CI: 38457059197, before this change.

Blockers: No approved private runner, guide audit, rotated secrets, or real Salesforce run. Next: trusted manifest-to-action planning interface, followed by browser-to-checkpoint integration. Reviewer attestations do not independently prove completeness.

## 2026-10-13 14:07-14:27 PDT — Interactive implementation sprint (user-requested)

Implemented/committed public-safe code with companion synthetic tests and documentation:
- 64636e25000f6cb9aa83a278ee319494549a16e8: Layout-preserving PDF instructor review sheets. GitHub Actions confirmed success: https://github.com/teomarcelo/workshop-validator/actions/runs/38468660126.
- 30b660585bb74960e12f84796198f7de3014f89b: End-to-end authored browser > independent evidence > dependency checkpoint > private HTML report pipeline. Confirmed green: https://github.com/teomarcelo/workshop-validator/actions/runs/38469003618.
- 9e2f1f2004bdca278dd48dd7e53e7d3f81272810: Persistent bounded HTTP 429 retry tickets. Confirmed green: https://github.com/teomarcelo/workshop-validator/actions/runs/38469166597.
- 3d9e3716293587619b9dd50fc0c34423c796c0bd: Private-only Salesforce login/guide-bound authored plan adapter. Its CI run was superseded/cancelled by a subsequent push; newer green runs include this code.
- e8ddea15a116565829f337fbe292f6c3fd0ae2bb: Private HTTPS input handoff and separately approved private GitHub Actions workflow TEMPLATE (not deployed). Confirmed green: https://github.com/teomarcelo/workshop-validator/actions/runs/38469630541.
- 67d6c32edbce1eb923fc62238404384eaf1dbaeb: Bounded same-job Option A 429 retries integrated in the pipeline and private runner. Confirmed green: https://github.com/teomarcelo/workshop-validator/actions/runs/38469857498.
- 0ab0cde11bf38f9958e6fbba0542be62a5f93c23: Fail-closed private page-review checklist generator. Confirmed green: https://github.com/teomarcelo/workshop-validator/actions/runs/38470033121.
- 7471db1ca4f383bf080cefaddf53dedc224bff23: Conservative UI locator proposal discovery; no automatic approval. Confirmed green: https://github.com/teomarcelo/workshop-validator/actions/runs/38470205338.
- 495085d126c6f45642954387d1518b843f1c7a1f: Public README, live pilot go-live checklist and living plan updates. Confirmed green: https://github.com/teomarcelo/workshop-validator/actions/runs/38470343620.
- c29f3a1fc5291094f76643926bdca8946ed10bcd: Rich quick HTML metrics and private screenshot evidence links. CI result was still running when this log was drafted; do not call it passing until GitHub reports success.

Private artifacts generated for the operator in the conversation (NOT in the public repo): A 36-page instructor sample comprising all 32 original AGT503 pages plus four marked instructor-note sheets about possible numbering inconsistencies, and a companion quick HTML summary. Original pages were checked rendered pixel-identical across the entire 32-page source. These source-page findings are NOT live Salesforce UI discrepancies, the edits are NOT approved, and no Salesforce test was performed. The actual source PDF and reports were not committed.

Limits/blockers: No real Salesforce login or Guide step has been executed. No private Actions runner, enterprise-approved PDF/plan storage, rotated trial-org secrets or full page-by-page reviewed executable AGT503 manifest is configured. The public GitHub integration cannot create a private repo or set its secrets. No paid cloud resources or model API calls were used. Autonomous arbitrary Salesforce UI repair and durable cross-job private execution remain outstanding.

Next safe milestone: confirm latest CI is green; add synthetic cross-run checkpoint/crash recovery and secure source-to-manifest authoring scaffolding. Real-org validation must wait for operator-authorized private infrastructure/secrets; do not falsely claim 100% step coverage.

## 2026-10-13 15:26 PDT — Scheduled public-safety milestone

Milestone: Added a conservative tracked-file boundary gate to the public CI job. Implementation commit: cb022d2310d699ff07214a8098605dbbb10de79d.

Changes: New `workshop_validator/public_safety.py`, `tests/test_public_safety.py`, `docs/PUBLIC_SAFETY.md`, and a public CI step. The gate rejects tracked PDF/image/browser-trace/database and secret-container file formats, designated private-output folders, symlinks, unexpected file types, unreadable/binary/non-UTF8 files and files over 1 MiB. It prints generic reasons/counts only. This is a **file type/path** check, not content secret scanning, and cannot prevent a secret from reaching Git history before CI; human review and repository secret scanning remain essential.

Actual local commands/results:
- `git clone --depth 1 https://github.com/teomarcelo/workshop-validator.git /tmp/workshop-validator-run6`: failed (local DNS resolution unavailable).
- `python3 -m unittest discover -s tests -v` in a minimal local workspace containing the two new files: 4 tests passed.
- `python3 -m compileall -q workshop_validator tests` in that workspace: passed.
- `git init -q && git add workshop_validator/public_safety.py tests/test_public_safety.py`: passed locally.
- `python3 -m workshop_validator.public_safety` on those two locally tracked files: passed.
- `git hash-object workshop_validator/public_safety.py tests/test_public_safety.py`: hashes matched both GitHub blobs; post-commit GitHub reads verified the two blobs and CI/doc blobs.

CI status: Existing public GitHub Actions run 38470713483 on predecessor 645f7ed completed successfully. A green Actions run for implementation cb022d was **not yet observed** at log time. The full repository suite and Chromium were **not run locally**.

Blockers: No approved private runner or source storage, no rotated org secrets, no complete page-reviewed executable manifest, no real Salesforce login. No live org steps or private PDF were executed. No paid infrastructure or API was used.

Next: Verify public Actions for cb022d, then add a fail-closed reviewed-source preflight before any real-org mutation and synthetic tests for missing/incomplete review.

## 2026-10-14 UTC - Corrective behavioral QA milestone

Why this corrective milestone was necessary:
- A prior instructor-review PDF emphasized step numbering without actually running the workshop in Salesforce. It was not a behavioral validation report and did not surface unnecessary manual configuration edits.
- Source-document visual inspection and synthetic unit tests were previously conflated with step-by-step real-org testing. No Salesforce step had been independently executed.

Public-safe implementation changes:
- Evidence assertions now support strict nonempty DOM_TEXT absence checks (not_contains), refusing to interpret an empty script editor as proof that a block is missing.
- Added a fail-closed necessity decision engine. If the desired state was already present, it requires baseline reproduction, prerequisite verification, a skip-path downstream test and a failed targeted negative control before producing only a REDUNDANCY_CANDIDATE.
- Added synthetic Chromium negative tests which deliberately corrupt a generated script type/insert an obsolete block and confirm that independent assertions reject the wrong state.
- Updated the project plan and behavioral QA policy so actual broken instructions/unnecessary edits outrank numbering or typography. Both quick HTML summary and annotated instructor PDF remain required.

Verification performed:
- Commit ca177b33cfa24145504830fc609f9fe0407b310d: public GitHub Actions run 38494124027 SUCCESS.
- Commit 44684fbdef7519c556464a93ca22902eb091cdef: public GitHub Actions run 38494220746 SUCCESS (unit tests and actual headless Chromium synthetic smoke).
- Commit 0e44925190e07d4737e445fe10e2eed82b4ab618: public GitHub Actions run 38494363760 SUCCESS.
- Separately, a corrected PRIVATE instructor review example was rendered, the original PDF pages were compared pixel-for-pixel, and behavioral findings were explicitly labeled unverified. Neither the source guide nor any source-derived review artifact was committed here.

Limitations: No authenticated Salesforce browser run, no live no-op/omission test, no manual test of all guide instructions, no production environment, and no proof of a current UI change. Source page-numbering alone is no longer considered an actionable validation defect.

Next high-priority work: integrate pre/post state and omission/negative-control probes into an approved, resettable PRIVATE Salesforce run; obtain authorized private guide/plan storage and rotated secrets; verify one real end-to-end step before expanding to complete guide coverage.

## 2026-10-13 18:27 PDT — Behavioral necessity pre/post probe integration

Milestone: public-safe optional authored `necessity_assertions` in the private plan. They are checked BEFORE the literal action, then enforced as independent postconditions along with normal step assertions. An ambiguous before-state blocks mutation; a deliberately wrong post-state is classified FUNCTIONAL_FAIL. The diagnostic cannot auto-skip an instruction or recommend removing it without reset, skip replay and negative control. Private runner records only sanitized classifications/evidence identifiers in private `behavioral-probes.json`; the quick HTML and annotated instructor PDF remain separate required outputs.

Implementation commit: `8fbfcf6a20a33448806bb965a91ad986f9d18ff5`. Source/tests/docs are on branch `automation/behavioral-probes-20261013` (not merged to main). New tests: `tests/test_behavioral_probe.py`, plus regression test in `tests/test_necessity.py`. No guide PDF, credential, org URL, private manifest or evidence artifact was committed.

Commands/actions ACTUALLY performed:
- GitHub connector read PROJECT_PLAN.md, docs/BEHAVIORAL_VERIFICATION.md, README.md, docs/GO_LIVE_CHECKLIST.md, AUTOMATION_LOG.md, source/tests and Actions history; created 7 source/test/doc blobs, tree and commit; verified branch file blobs by reading them back.
- `git clone --depth 1 --branch automation/behavioral-probes-20261013 https://github.com/teomarcelo/workshop-validator.git /tmp/workshop-validator-probe && python -m unittest discover -s tests -v`: FAILED before tests; local DNS could not resolve github.com.
- `python -m compileall -q /tmp/wv-probe/workshop_validator/behavioral_probe.py`: PASSED for an isolated locally transcribed copy of the new module, NOT a repository-wide compilation/test.
- GitHub main ref update and pull-request creation: both BLOCKED by connector safety checks. New branch creation succeeded.
- Last observed public Actions green: run 38494563658 on main commit 0fd5f4a (2026-10-14 01:13 UTC), which PRECEDES this change. No branch CI run was observed. Therefore this milestone is NOT CI-verified and not merged.

Blockers: no public CI for branch without PR/main merge; no approved private runner, rotated secret-manager credentials, authorized private guide/plan storage, complete page-reviewed executable manifest or disposable-org reset proof. No Salesforce login, live step, private PDF, paid API or cloud deployment was performed.

Next: obtain authorized merge/PR route, run full Python and Chromium synthetic CI on this commit and fix any failures; then test a resettable private baseline with explicit necessity assertions and a targeted reversible negative control before proposing any instructor correction.
