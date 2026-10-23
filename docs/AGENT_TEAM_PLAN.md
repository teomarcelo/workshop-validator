# Plan: CLAUDE.md, lessons fix, cleanup, AI agent team, unified HTML report

## Context
The repo was built with Grok and has drifted:
- **The rules only reach Cursor.** They live in `.cursor/rules/` and no AI-neutral rules file exists.
- **The lessons file is broken.**
  - `docs/WORKSHOP_LESSONS.md` is 4 MB and about 28.6k lines, because `lessons.py` appends every run with no de-duplication.
  - Every NOT_RUN step that follows a failure is logged with the failing step's reason, and labeled a "Finding".
  - Nothing reads the file back in.
  - It contains verbatim guide step text, which is confidential under the repo policy, yet it sits untracked in a **public** repo.
- **The current "team" is fake.** `team.py` holds hardcoded strings. Its `hold.json` gate must be edited by hand, which blocks automation.
- **Two report designs disagree.** `report.py` is teal and `step_board.py` is cream/serif.
- **The test suite is red.**
  - Local deps aren't installed (`fitz`, `pdfplumber`, `reportlab`).
  - One real failure: `test_guide_walk.py:1337`, Free-Form → Auto-Layout restore returns UI_MISMATCH.
- **A large amount of work is uncommitted:** about 1.5k lines plus 13 new files.

The user wants:
- the rules written down for Claude
- lessons fixed and the repo cleaned up
- a cost-efficient **Claude Code subagent team** that automates the whole loop: run guide → triage → fix → test → re-run → push a branch and open a PR
- a polished pass/fail HTML report

Decisions made: Claude Code subagents (no API bill, Python stays deterministic); branch + PR, never push to main; trigger with one `/validate-guide` command; include the existing WIP in the commits.

## 1. Environment and baseline
- Create `.venv`, then `pip install -r requirements-dev.txt playwright==1.57.0` and `playwright install chromium`. `.venv/` is already ignored; confirm.
- Re-run the full suite to get the real baseline, now that `test_private_runner` and the others can import.
- Fix `test_free_form_is_put_back_on_auto_layout_before_the_screen`. The path is `guide_walk._restore_auto_layout` (~2577) → `team.approve_auto_layout_restore`, called from `run_add_screen` (~2790).

## 2. CLAUDE.md (new, repo root)
Keep it short. Import the existing rules rather than copying them: `@.cursor/rules/exercise-order.mdc`, `@.cursor/rules/guide-only.mdc`, `@.cursor/rules/step-pass-fail.mdc`, `@.cursor/rules/wait-for-load.mdc`. Cursor and Claude then share one source.

Sections:
- **Purpose and status:** synthetic CI only; one real login; nothing verified end-to-end.
- **PASS policy:** evidence-bound; the verifier differs from the executor; confidence is never proof; the status list; NOT_RUN is never a result.
- **Option A for 429:** keep running independent steps; blocked and dependent steps are never PASS.
- **Behavioral testing:** before-state, literal action, after-state, downstream checks; how a step can be called redundant; cosmetics stay separate.
- **Outputs:** HTML report and annotated PDF are both required; a note with no anchor fails on purpose.
- **Public repo boundary:** never commit PDFs, guide text, credentials, screenshots, video, hostnames, plans or run output. Never read `~/.workshop-validator/verification-orgs.json` or `browser-state.json`.
- **$0 budget.**
- **Commands:** venv, tests, browser smoke, `public_safety`, `/validate-guide`.
- **The agent team and git policy:** branch `automation/*`, open a PR, never push to main.

Also add `.claude/settings.json`:
- deny `Read` on `~/.workshop-validator/verification-orgs.json`, `**/browser-state.json` and `*.pdf` inside the repo
- allow the routine read-only and test commands, to cut down on prompts

## 3. Fix lessons.py and move it out of the public repo
- **New location:** the private root, `~/.workshop-validator/lessons.md` (0600). `lessons_path(private_root)` changes signature, and `automate.py:128` passes `private_root`. The docstring's "safe to commit" claim is removed, because guide text is private.
- **Store, don't append:** parse the existing entries, merge the new rows, and rewrite the file atomically.
  - Key: normalized (step text, status, root-cause note).
  - Each entry keeps `seen N×` and the last-seen date.
  - Hard cap well below 1 MiB, dropping the oldest and least-seen entries first.
- **Skip cascades:** NOT_RUN rows whose note matches the freeze or blocked note are dropped. One root-cause lesson is recorded per run instead.
- **Fix `classify()`:**
  - NOT_RUN → never a finding; it is recorded only if it is a loading or no-check note, as an observation.
  - UI_MISMATCH / FUNCTIONAL_FAIL → finding.
  - Executor errors → mistake.
- **Add a reader:** `read_lessons(private_root, step_text=None)` returns the relevant entries. The triage and fixer agents use it.
- **Migration:** `python -m workshop_validator.lessons --compact --from docs/WORKSHOP_LESSONS.md` builds the private store from the 4 MB file. Only after checking the new file do I delete `docs/WORKSHOP_LESSONS.md`. It is untracked, so it's not in git.
- **Tests** in `tests/test_lessons.py`: de-duplication, cascades dropped, size cap, classification, secrets scrubbed, path outside the repo.

## 4. Clean up the fake team and the automation gates
- Replace `team.py` with `workshop_validator/guards.py`, keeping only the real logic: `checker(step_text, action)` and `approve_auto_layout_restore`. The constant-string roles and the circular imports go. Update `guide_walk.py` and `automate.py` imports, and rewrite `tests/test_team.py` → `tests/test_guards.py`.
- `automate.py`:
  - drop the `hold.json` "continue" gate
  - add `--reset-gate`, so the orchestrator can unfreeze the gate after a verified fix
  - add `--json` to print a sanitized summary (statuses, first failure, run dir, never credentials) for the agents to read
- **Docs cleanup:**
  - `PROJECT_PLAN.md`: update the status header and the 10-15 login facts.
  - `README.md`: add sections on the agent team, `/validate-guide` and the lessons location.
  - `AUTOMATION_LOG.md`: add a new entry.
  - `docs/PRIVATE_RUNNER.md`: shorten the order and reporting paragraphs to point at CLAUDE.md.
  - New `docs/AGENT_TEAM.md`: roster, models, loop, guardrails.
- **Audit the WIP for confidential content before committing.**
  - Check whether `guide_walk.py` builtin checks and `tests/test_guide_walk.py` embed verbatim AGT503 text or org hostnames.
  - Where they do, replace the text with short synthetic patterns. Where that isn't possible, stop and report to the user.
  - Check that the AGT503 PDF test skips when the PDF is absent.

## 5. The AI agent team (`.claude/agents/*.md` + `.claude/skills/validate-guide/SKILL.md`)
Models are chosen by role. Mechanical work goes to Haiku and judgment to Sonnet. Opus is reserved for the rare deep check.

| Agent | Model | Job |
|---|---|---|
| **Orchestrator:** the `/validate-guide` skill, `model: sonnet` | Sonnet | Drives the loop, enforces budgets, writes `team-log.json`, renders the report |
| `run-operator` | Haiku | Runs `automate --json` in the background, waits, and returns a sanitized summary. Never reads credentials |
| `failure-triager` | Sonnet | Takes the **first** failing step (cascades ignored), its note, screenshot and lessons. Classifies it as code/locator bug, guide mismatch, environment (429/loading), or no check implemented. Writes a fix brief |
| `salesforce-doc-validator` (existing, global) | Opus, invoked as Sonnet unless the case is ambiguous | Confirms a suspected UI rename against current Salesforce docs |
| `check-fixer` | Sonnet | Implements the minimal code fix plus a synthetic test, following guide-only. Not allowed to weaken assertions, add unguided actions, or embed guide text |
| `test-runner` | Haiku | Runs unit, compile and browser smoke tests and returns only the failures |
| `rule-auditor` | Sonnet | Independent reviewer of the fix diff against CLAUDE.md: false-PASS paths, assertion weakening, unguided actions, leaks. Can veto. Kept separate from the fixer (verifier ≠ executor) |
| `lessons-keeper` | Haiku | Records the root-cause lesson and runs compaction |
| `safety-scanner` | Haiku | Runs `public_safety` on the staged tree, then greps the diff for emails, hostnames, passwords and guide-text fragments |
| `git-publisher` | Haiku | Creates branch `automation/<date>-<slug>`, makes the commit, pushes, runs `gh pr create` with the report summary, and checks CI. Only after test-runner, rule-auditor and safety-scanner have all passed |

**Loop (inside the skill):**
1. preflight: venv, tests green, credentials file present (only its existence is checked)
2. run-operator runs the guide on org N
3. if all steps PASS → go to step 8
4. otherwise failure-triager, plus doc-validator if needed
5. environment or loading causes: no code change; record them and finish as INCOMPLETE
6. code causes: check-fixer → test-runner → rule-auditor, with up to 2 rounds of rework
7. `automate --reset-gate`, then re-run on the next fresh org
8. lessons-keeper
9. render the report
10. if code changed: safety-scanner → git-publisher (PR)
11. `open` the report

**Budgets:**
- at most 3 fix iterations per invocation
- stop on a repeated identical failure
- stop if auth fails
- never retry around a 429; Option A applies
- each agent's prompt is short and file-scoped, and returns structured summaries rather than file dumps, to keep tokens down

## 5a. Failure handling: renamed controls and already-provisioned state

**Renamed control (decided: yes, continue with a flag)**
- New private file: `~/.workshop-validator/aliases.json`. Each entry holds:
  - the guide's name and the name seen on screen
  - the step ID
  - the doc source URL
  - the auditor's approval and the date
- It stays private because it contains guide text.
- An alias is written only when all three are true:
  - the triager classifies the failure as a wording change
  - `salesforce-doc-validator` confirms the rename
  - `rule-auditor` approves
- `guide_walk` tries an alias **only after the exact guide name fails**. It goes through the same role/name locator path; it never searches by fuzzy match.
- The step result is `UI_MISMATCH` with `freeze=False`. The note reads: "Guide says 'Save'; screen says 'Save & Close'. Continued with the reviewed alias." The evidence ID is the read of the control.
  - The step and its task stay **FAIL**. The report and the PDF show the suggested correction for instructor approval.
  - Later steps still run.
- Tests:
  - an alias is never used when the exact name exists
  - a step that used an alias is never PASS
  - its task is FAIL
  - the steps after it are executed

**Already toggled or set because the trial org was provisioned**
- Today, `run_toggle` (`guide_walk.py:824`) and `_act_toggle` (`:1890`) already read the state first and skip the click when the toggle is on. But the note is the same whether we clicked or not ("X is on."), so the report can't tell the two cases apart.
- **Read first, never flip:**
  - Every toggle, checkbox, assignment or "create" step reads its current state before acting.
  - If it's already in the target state: **do not click** (clicking would turn it off), do not re-create, do not re-assign.
  - Generalize the existing patterns: "HelloWorld Agent is already listed" (`:1083`) and "Lightning is already open" (`:436`).
- **Unknown state is never clicked:**
  - If `_read_toggle` returns unread, the step becomes NEEDS_REVIEW.
  - It does not fall through to a generic click; I'll check where the `None` return leads and close that path.
- **Recording:**
  - Status PASS, because the target state was read on the page.
  - A distinct note: "Already on in the provisioned org; not clicked."
  - Evidence ID `already-set:<name>`.
  - `behavioral_classification=ALREADY_SATISFIED`, using the existing `report.BEHAVIORAL` vocabulary.
- **Report:**
  - an "Already set by org" chip on the step and a count tile
  - the PDF note says the learner may find this already on and suggests "If not already on, …" wording
  - it is labeled *pending verification on a non-provisioned org* and is not a removal recommendation (BEHAVIORAL_VERIFICATION policy)
- **Triage rule:** a missing "Enable"/"Turn on" when "Disable", or an on switch, is present means already satisfied, not a UI mismatch. check-fixer adds a before-state read for that step.
- **Lessons:** record "already set in provisioned org" once per step, so later runs and agents expect it.
- **Tests:**
  - an already-on toggle is not clicked and is PASS with the already-set evidence
  - an off toggle is clicked, then re-read, then PASS
  - an unread toggle is NEEDS_REVIEW with no click
  - an already-created record is not re-created

## 6. The HTML report
- New `workshop_validator/theme.py` holds the shared design tokens and CSS: neutral palette, light and dark via `prefers-color-scheme`, system fonts, print styles. Both `report.py` and `step_board.render_document` (~504) use it, so the two looks become one.
- New `step_board.render_run_report(run_dir) -> Path` plus a `python -m workshop_validator.step_board --render <run_dir>` CLI. It reads `status.json`, the board data, and the optional `team-log.json`, and writes `report.html` (0600) in the run directory.
  - **Header:** guide name, run ID, date, and an overall verdict (VERIFIED / INCOMPLETE / FAILED) that follows the existing `render_report` rules.
  - **Stat tiles:** Pass / Fail / Not run / Needs review, and a stacked pass/fail bar for each exercise.
  - **Sections:** Setup → Exercise → Task, each collapsible with `<details>`; failed tasks open by default. Each step shows a status pill, and failed steps show the reason inline, using the step-pass-fail format. The steps after a failure stay visible, and screenshot thumbnails link to `screenshots/*.png`.
  - **Team activity timeline:** what was triaged, fixed and re-run, with the PR link.
- No JS dependencies, no external fonts or CDN; all text escaped; works offline.
- Use the `dataviz` skill when building the tiles and bars.
- Reuse the existing `_sections`, `_rollup`, `_display` and `outcomes_from_report_steps`; the status logic does not change.
- `local_site` result pages link the new report.
- Tests: escaping, PASS without evidence is never shown as PASS, every step is listed, the failure stays on its step, and dark-mode CSS is present.

## 7. Commit and publish
On branch `automation/agent-team` (CI runs on `automation/**`), in logical commits:
1. existing WIP plus test fixes
2. lessons rewrite and migration
3. guards and `automate` cleanup
4. theme and report
5. CLAUDE.md, `.claude/` and docs

Each commit gets the Co-Authored-By line. Then `public_safety`, push, and `gh pr create`. I won't push to main.

## Verification
- `.venv/bin/python -m unittest discover -s tests -v`: all pass, including the previously failing guide_walk test and the new lessons, guards and report tests.
- `RUN_BROWSER_SMOKE=1` runs of `test_browser_worker`, `test_pipeline`, `test_discovery` and `test_necessity_browser`.
- `python -m workshop_validator.public_safety` passes, and `git ls-files` shows no PDF, video, lessons or run output.
- Lessons migration: the private file is under 1 MiB, has no repeated entries and no cascade NOT_RUN lines.
- Render `report.html` from a synthetic run directory and look at it in a browser (light and dark).
- Dry-run `/validate-guide` against the synthetic fixtures: check that the agents trigger in order and that the safety-scanner blocks a planted fake email in a test commit.
- PR CI goes green on GitHub.
- **A live end-to-end run on the real AGT503 PDF needs the user** to create `~/.workshop-validator/verification-orgs.json` with rotated credentials. I will not handle the credentials.

## 8. Report contents and persistence (added 2026-10-19)

**Where results persist** (private, outside the public repo, survives reboots, never auto-deleted):
- `~/.workshop-validator/runs/<run-id>/report.html` — the full report for one run, plus `status.json`, `team-log.json`, `screenshots/`, `results.pdf`, `walk.webm`.
- `~/.workshop-validator/reports/index.html` — a history page regenerated after every run: one row per run (date, guide, org, verdict, pass/fail/not-run counts, PR link), newest first, linking each run's report.
- `~/.workshop-validator/reports/history.json` — the machine-readable history the index is built from; append-only, de-duplicated by run ID.
- Reports are never committed: they contain guide text and screenshots.

**Every report shows**
- Verdict banner: VERIFIED (green) only when every required step is PASS with evidence and the source review is complete; FAILED (red) when any step failed; INCOMPLETE (amber) otherwise (blocked, not run, needs review, unreviewed source).
- Stat tiles: Pass, Fail, Not run, Needs review, Already set by org.
- One pass/fail bar per exercise.
- Setup → Exercise → Task, each with PASS/FAIL. Failed tasks open by default; every step has a status pill; a failed step shows its reason and screenshot; steps after it stay listed as NOT RUN with "not run because Step X failed".
- Chips: "Already set by org" (not clicked) and "Renamed in UI" (alias used, still FAIL, with the proposed guide correction).
- Proposed guide corrections for the instructor, all marked pending approval.
- Team activity timeline: each run, the triage, each fix and its tests, the audit verdict, the re-run, and the PR link.
- Footer: run ID, org number (never the hostname), guide SHA-256 prefix, and links to the annotated PDF and the recording.

## 9. Test orgs and when to move to the next one (added 2026-10-19)
- Five disposable trial-org usernames share one password. They live only in `~/.workshop-validator/verification-orgs.json` (mode 0600), written by the user, never pasted into chat, never read by agents. `automate.load_orgs` will also accept `{"password": "...", "usernames": ["..."]}`.
- Start every cycle on org 1. Move to the next org **only** when the current org's run reached a final result:
  - **Success:** every step PASS → the next org confirms it is repeatable.
  - **Complete fail:** the run finished with a real, recorded failure (UI_MISMATCH / FUNCTIONAL_FAIL found by the guide walk, not an interruption) → after a fix, re-test on the next fresh org.
- **Do not** move on (and do not count the org as used) when the run was inconclusive: sign-in failed, HTTP 429 / environment blocked, still loading at the limit, browser crashed, or the run was stopped. Retry on the same org later.
- Never use more than one new org per fix iteration, and stop when all five are used.
