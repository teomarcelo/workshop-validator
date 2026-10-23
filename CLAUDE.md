# Workshop Validator — rules for Claude

Validates Salesforce workshop guides (PDF) by walking every step, in printed
order, in a disposable trial org, then writes a private HTML report and an
annotated PDF. Public repo: github.com/teomarcelo/workshop-validator.

The step rules are shared with Cursor; read them, they are binding:

@.cursor/rules/exercise-order.mdc
@.cursor/rules/guide-only.mdc
@.cursor/rules/step-pass-fail.mdc
@.cursor/rules/wait-for-load.mdc

## PASS policy
- PASS needs evidence read from the page. A PASS with no evidence is shown as FAIL.
- The verifier is never the executor. Confidence is never proof.
- Statuses: PASS, UI_MISMATCH, FUNCTIONAL_FAIL, ENV_BLOCKED, AUTH_BLOCKED,
  NEEDS_REVIEW, NOT_RUN. NOT_RUN is never a result.
- Do only what the step says. Never click a look-alike control. A rename
  continues only through a reviewed alias in `~/.workshop-validator/aliases.json`,
  and that step stays UI_MISMATCH (FAIL) for instructor review.
- Already set by the provisioned org (toggle on, field filled): do not click,
  record PASS with `already-set:` evidence. Unknown state is never clicked.
- A value in parentheses ("First Name: (your first name)") is the learner's own.
  Never type the description, never invent a value.
- Never change the org password or security answer; those steps, and the Save
  that submits that form, stay NOT_RUN.
- HTTP 429 is Option A: keep running independent steps; blocked and dependent
  steps are never PASS. A still-loading page is not a failure (poll ~2 s, up to ~90 s).
- Behavioral checks: before-state, literal action, after-state, downstream.
  A step is called redundant only with that proof; cosmetics stay separate.

## Team only
- Guide runs, triage and validator fixes happen only through `/validate-guide`
  (or the local page, which launches it). The main session never edits
  `workshop_validator/` during a loop and never runs ad-hoc browser probes
  against an org.
- No failure is triaged or reported before step-verifier judged it.
- Every phase is recorded via `python -m workshop_validator.team_state`.
- The watchdog (`python -m workshop_validator.watchdog`) resumes a dead job.

## Orgs (`~/.workshop-validator/verification-orgs.json`, 0600)
- Start on org 1. Move to the next org only after a final success, or a real
  failure (UI_MISMATCH / FUNCTIONAL_FAIL) after a step changed the org.
- Inconclusive runs (sign-in, 429, loading, crash, NEEDS_REVIEW, no check yet)
  keep the same org. A failure before anything changed keeps the org.
- At most one new org per fix iteration. Stop when all orgs are used.

## Public repo boundary
- Never commit PDFs, guide text, credentials, screenshots, video, org
  hostnames, run output, lessons or aliases. Tests use synthetic text only.
- Never read `verification-orgs.json` or any `browser-state.json`; only check
  that the org file exists. Never print usernames or passwords.
- `python -m workshop_validator.public_safety` must pass before a push.
- $0 budget: no paid APIs or services.

## Commands
```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt playwright==1.57.0
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests            # unit tests
RUN_BROWSER_SMOKE=1 .venv/bin/python -m unittest tests.test_browser_worker tests.test_pipeline
.venv/bin/python -m workshop_validator.public_safety      # tracked-file gate
.venv/bin/python -m workshop_validator.automate --guide <pdf> --json   # one org run
.venv/bin/python -m workshop_validator.automate --reset-gate           # after a verified fix
.venv/bin/python -m workshop_validator.lessons --step "<step text>"    # private lessons
.venv/bin/python -m workshop_validator.lessons --rules                 # never: rules for agents
.venv/bin/python -m workshop_validator.step_board --render <run_dir>   # report.html
.venv/bin/python -m workshop_validator.team_state start|advance|log|heartbeat|finish|show
.venv/bin/python -m workshop_validator.watchdog --install              # launchctl setup
```
Exit codes of `automate`: 0 success, 1 real failure, 3 inconclusive, 2 refused.

## Private outputs (`~/.workshop-validator`)
- `runs/<org-key>/<run-id>/`: `report.html`, `results.pdf`, `summary.json`, `status.json`, `walk.webm`.
- `reports/index.html` and `history.json`: every run.
- `lessons.md` / `lessons.json`: de-duplicated lessons, read them before triage.
  Root causes and team mistakes are recorded with `lessons --record root-cause|team`;
  `lessons --rules` lists the `never:` rules every agent follows.
- Each run opens its live `report.html` in the default browser and records the
  org instance (`Organization.InstanceName`); reports link between instances.

## Agent team and git
`/validate-guide <pdf>` runs the loop (see `docs/AGENT_TEAM.md`). Work on a
branch `automation/<date>-<slug>`, open a PR with `gh pr create`, never push
to `main`, never force-push.
