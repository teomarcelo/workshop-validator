# Workshop Validator - rules for Claude

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
  Never type the description, never invent a value. An empty own-value field the
  guide does not mark mandatory is left unchanged as NOT_RUN and the walk
  continues; a mandatory one stops as NEEDS_REVIEW.
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

## Producer team (register prefix W-; see `docs/AGENT_TEAM.md`, "Producer loop")
- Autonomy boundary. The team does alone: research, design, code, tests,
  reviews, merges behind gates. Only the owner does: sign-ins, org signup and
  credentials, payments, legal forms, accepting agreements, GitHub
  visibility and settings, creating scheduled tasks, and sending any outbound
  message (drafts wait in the private `outbox/`). Nothing new goes public
  without the owner's yes.
- Never ask the owner a routine question ("may I merge?" is an escape).
  Decide, log a RECOMMENDED row, move on. Every message to the owner ends
  with "What I need from you:" (one line per open ask, W- id and date) or
  "Nothing right now".
- The producer never does hands-on work. Brief, fresh executor in a worktree,
  fresh reviewer, `team_gates merge-when-green <pr>` (squash merge when every
  gate passes, same-turn revert PR when main goes red), then register,
  ledger, standup, dashboard and worktree cleanup.
- Private memory lives in `~/.workshop-validator/team/` (register, plan,
  standups, briefs, reviews, ledger, dashboard). It is never committed.
- Escapes: when the owner finds a mistake first, log it, name the missing
  gate, add the test, hook or role that would have caught it, then fix it.
- A fresh session reads CLAUDE.md, the newest standup's restart block and
  `progress/now.json`. At about 40 percent context: restart block, commit,
  `team_kit release`, clear. Never clear while an agent you started runs.
- Models: producer Opus; executors Sonnet, effort high; reviewers Sonnet, or
  Opus for security, privacy, compliance and shared code. Fresh agent per
  task; none resumed past about 300k tokens.

### Ownership map (one owner per folder; shared ones change only by reviewed PR)
| Path | Owner |
| --- | --- |
| `workshop_validator/` walk, checks, automate, evidence | check-fixer (via /validate-guide) |
| `workshop_validator/guide_inventory.py`, `pdf_*.py` | pipeline-engineer |
| `workshop_validator/theme.py`, `step_board.py` render, `team_dashboard.py` | ui-designer |
| `workshop_validator/team_*.py`, `watchdog.py`, `team_state.py`, `.claude/settings.json` hooks | producer (security-reviewer reviews) |
| `tests/` | the owner of the code under test |
| `docs/` (not `docs/decisions/`), `README.md` | writer |
| `docs/decisions/`, `CLAUDE.md`, `.claude/agents/`, `.claude/workflows/`, `briefs/` | producer (shared) |
| `.github/workflows/` | producer (security-reviewer reviews) |
| `.cursor/rules/` | shared: owner's rules, reviewed PR only |

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
.venv/bin/python -m workshop_validator.team_kit status|watchdog|step|ledger|claim|release
.venv/bin/python -m workshop_validator.team_gates all [--private] | merge-when-green <pr> | worktree-cleanup
.venv/bin/python -m workshop_validator.team_dashboard [--open]          # private status page
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
to `main`, never force-push. Green PRs are squash-merged by
`team_gates merge-when-green` (W-006), never by a direct push.
