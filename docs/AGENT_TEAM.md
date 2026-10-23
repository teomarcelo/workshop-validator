# Agent team

Claude Code subagents in `.claude/agents/`, driven by the `/validate-guide`
skill in `.claude/skills/validate-guide/`. The Python walk stays deterministic;
agents only run it, read its private output, and change validator code
through review. No paid API is used.

| Agent | Model | Job |
| --- | --- | --- |
| `/validate-guide` (orchestrator) | Sonnet | Runs the loop, keeps budgets, tracks state via `team_state`, renders the report |
| `run-operator` | Haiku | One `automate --json` run; returns the sanitized summary. Never reads credentials |
| `step-verifier` | Sonnet | Visual verification from `walk.webm` frames; verdicts per step (batches of ~20) |
| `failure-triager` | Sonnet | First failing step or step-verifier verdict; classifies the cause; writes a fix brief |
| `salesforce-doc-validator` (global) | Sonnet, Opus when ambiguous | Confirms a suspected UI rename against current Salesforce docs |
| `check-fixer` | Sonnet | Smallest code fix plus a synthetic test |
| `test-runner` | Haiku | Unit, compile and browser smoke tests; returns failures only |
| `rule-auditor` | Sonnet | Independent review of the diff against CLAUDE.md; can veto |
| `lessons-keeper` | Haiku | Records each root cause (cause, fix, `never:` rule, PR) and each team mistake |
| `safety-scanner` | Haiku | `public_safety` plus a staged-diff scan for leaks |
| `git-publisher` | Haiku | Branch `automation/*`, commit, push, `gh pr create`, watch CI |

## Loop
1. Preflight: venv, green tests, org file exists (never read). `team_state start`.
2. Run the current org (run-operator in background; orchestrator heartbeats while waiting).
3. **Verify phase** (new): step-verifier judges step outcomes visually from `walk.webm` frames.
   - **Quick mode** (default): all non-PASS + PASS rows with `suspect` flags.
   - **Full mode** (`--mode full`): every step.
   - Batches of ~20 steps, ≤4 parallel batches.
4. Triage: failure-triager on step-verifier verdicts or the first failure; confirm renames against docs.
5. Fix (check-fixer) → test (test-runner) → review (rule-auditor), at most 2 rework rounds.
6. `automate --reset-gate`, re-run. At most 3 fix iterations.
7. Lessons, report, then safety-scanner → git-publisher when code changed. `team_state finish`.

## Self-healing
Every failure leaves a lesson that changes what the team does next time.
- **Step rows** (what a step showed) are written by the runner after each run.
- **Root causes** (`lessons --record root-cause`): why the first failure
  happened, what fixed it, the PR, and a `never:` rule. lessons-keeper writes
  one at the end of every iteration that had a diagnosed cause.
- **Team lessons** (`lessons --record team`): the team's own mistakes, such
  as a RED test, an auditor veto, the same failure twice, an agent error, or a
  watchdog restart or stall (the watchdog records these itself).
- `lessons --rules` prints every `never:` rule. The orchestrator loads them at
  preflight; failure-triager, check-fixer and rule-auditor read them, and the
  auditor vetoes a diff that breaks one.
- A root cause or team lesson seen again is printed as **RECURRING**: the
  earlier fix did not hold, so the triager has to explain why instead of
  repeating it.
- Each validator fix ships with a regression test that fails without it.

Not automatic, on purpose: learner-only values, renamed controls (an alias
needs review), AUTH_BLOCKED, and changes to the agent or skill files
themselves. These stop the loop and are reported to the user.

## Org instance and live report
- After sign-in, the runner reads `Organization.InstanceName` (plus
  `IsSandbox`, `OrganizationType`) with one read-only REST query on the
  walk's own session. If that is refused, Setup > Company Information is read
  in a separate tab after the walk. The public Trust API
  (`api.status.salesforce.com/v1/instances/<key>/status`) adds release and
  region. The instance is stored in `summary.json` and `history.json`.
- Each report shows the instance. When a guide has runs on more than one
  instance, the report header links to the latest report on each of them,
  and the history page has instance filter tabs.
- `automate` writes a self-refreshing `report.html` when a run starts and opens
  it in the default browser (`--no-open` skips that). The final report
  replaces it when the run ends.

## How failures are handled
- **Control not found, clean name:** UI_MISMATCH, the walk stops. A similar
  control is named in the note but never clicked.
- **Control not found, unclear name** (the step reader could not isolate one
  control): NEEDS_REVIEW, the walk stops, and the org is kept.
- **Renamed control:** after docs confirm and the auditor approves, an alias in
  `~/.workshop-validator/aliases.json` lets later steps run. The step stays FAIL.
- **Already set by the org:** read, not clicked; PASS with `already-set:` evidence.
- **Learner's own value** (`Email: (your email)`): kept if present, NEEDS_REVIEW if empty.
- **Password / security answer:** NOT_RUN, including the Save that submits that form.
- **Outside the browser** (Downloads folder, desktop): NOT_RUN, the walk continues.

## Org rule
Start on org 1. Move to the next org only after a success, or after a real
failure that came once a step changed the org. Inconclusive runs and failures
before any change keep the org. See `automate.outcome_of` and `automate.org_changed`.

## State tracking and watchdog
- **`team_state`**: tracks the current phase, last heartbeat, and action log in
  `team/state.json` and `team/team-log.json`. Commands: `start` (acquire lock),
  `advance <phase>`, `log <action> <detail>`, `heartbeat`, `finish` (release lock),
  `show`. Prevents concurrent runs.
- **`watchdog`**: resumes a dead job (no heartbeat for >15 min). Install:
  `.venv/bin/python -m workshop_validator.watchdog --install`, then run the
  printed `launchctl load` command. Logs to `~/.workshop-validator/team/watchdog.log`
  and launchd logs to `~/.workshop-validator/team/watchdog-launchd.log`.
- The local page (http://127.0.0.1:8765) launches `/validate-guide` via form submission.

## Private files (`~/.workshop-validator`, mode 0600)
`verification-orgs.json`, `gate.json`, `aliases.json`, `lessons.json`,
`lessons.md`, `reports/history.json`, `reports/index.html`, `team/state.json`,
`team/team-log.json`, `team/stalled.json` (when stalled), and
`runs/<org-key>/<run-id>/` with `report.html`, `results.pdf`, `summary.json`,
`status.json`, `walk.webm`, `verdicts.json`. Each org has its own session folder,
so a saved session is never reused for another user.
