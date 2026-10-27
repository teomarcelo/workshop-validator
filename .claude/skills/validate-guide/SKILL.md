---
name: validate-guide
description: Validate a Salesforce workshop guide PDF end to end - run it on the current verification org, triage the first failure, fix the validator, re-run, render the HTML report, and open a PR. Use when the user runs /validate-guide <pdf>.
model: sonnet
---

# /validate-guide <path-to-guide.pdf> [--mode quick|full] [--resume]

You are the orchestrator. You delegate; you do not click, and you never edit
`workshop_validator/` yourself. You never read `~/.workshop-validator/verification-orgs.json`
or any `browser-state.json`. Follow CLAUDE.md. Keep each agent prompt short
and file-scoped; ask for structured replies, not file dumps.

**Modes:** `quick` (default) verifies all non-PASS steps + PASS rows with any
`suspect` flag. `full` verifies every step. **Resume:** `--resume` reads
`team_state show`, continues at the recorded phase; if the run finished but
was not verified, goes to Verify.

**State tracking:** call `.venv/bin/python -m workshop_validator.team_state <cmd>` at
every phase: `start --pdf <pdf> --mode quick|full` (acquire lock),
`advance --phase <phase> [--run-dir <dir>]`, `log --agent <name> --action <a> --detail <d>`,
`heartbeat` (while waiting), `finish --outcome VERIFIED|FAILED|INCOMPLETE`.
The lock prevents concurrent runs.

**Self-healing:** every failure is written down with its cause, fix and a
`never:` rule, and every agent reads those rules before it works.
- At preflight, run `.venv/bin/python -m workshop_validator.lessons --rules` and paste
  the rules into the prompts of failure-triager, check-fixer and rule-auditor.
- **Team failures are lessons too.** Whenever test-runner is RED, rule-auditor
  vetoes, the same first failure repeats, an agent errors or returns nothing
  usable, or a command in this skill fails, record it at once:
  `.venv/bin/python -m workshop_validator.lessons --record team --title "<agent/phase>" --cause "<what went wrong>" --fix "<what you did>" --never "<rule>"`.
  If the failure is in this skill or an agent file itself, say so in the final reply
  so the user can approve a fix to the instructions.
- A lesson printed as **RECURRING** means an earlier fix did not hold: tell
  failure-triager, and do not repeat that fix.

**Live report:** `automate` opens `<run_dir>/report.html` in the user's default
browser when a run starts. It refreshes itself during the run and becomes the
final report when the run ends. Each report names the org instance and links
to this guide's latest report on every other instance.

## Budgets
- At most 3 fix iterations per invocation.
- Stop on the same first failure twice in a row, on AUTH_BLOCKED, or when
  `automate` exits 2 (refused / all orgs used).
- Never retry around HTTP 429 (Option A). Never use more than one new org per iteration.

## Loop
1. **Preflight.** `.venv` exists; `test -f ~/.workshop-validator/verification-orgs.json`;
   test-runner is GREEN. `team_state start --pdf <pdf> --mode <mode>`. Load the team rules
   (`lessons --rules`).
2. **Run.** `team_state advance --phase run`. run-operator (background) with the PDF.
   **Never leave a run unattended:** while waiting, `team_state heartbeat` every
   2–3 minutes. Read its exit code and summary.
   - 0 success → step 3 (Verify).
   - 1 real failure or 3 inconclusive → step 4 (Verify).
   - 2 refused → read `gate.json` reason (not the org file), `team_state finish --outcome <outcome>`, stop.
3. **Verify.** `team_state advance --phase verify`. Collect step IDs per mode: quick =
   all non-PASS + PASS rows with any `suspect` flag; full = every step. Launch
   step-verifier in batches of ~20 steps (≤4 batches in parallel allowed).
   **Routing:**
   - `FALSE_FAILURE` or `FALSE_PASS` → Triage with cause `validator-bug`.
   - `REAL` → Triage as normal.
   - `UNCLEAR` → mark as NEEDS_REVIEW, never PASS.
   - `CONFIRMED_PASS` → no action.
4. **Triage.** `team_state advance --phase triage`. failure-triager on the run dir (or
   the step-verifier verdict). If it suspects a rename, ask salesforce-doc-validator
   (Sonnet unless ambiguous) to confirm with current docs.
5. **Decide by cause.**
   - `environment`: no code change; `team_state finish --outcome <outcome>`; record and finish as INCOMPLETE.
   - `guide-mismatch` confirmed by docs: propose an alias entry (guide, screen,
     step_id, doc URL) and have rule-auditor approve it before writing
     `~/.workshop-validator/aliases.json` with `"approved": true`. The step stays FAIL.
   - `already-set`, `validator-bug`, `no-check`: check-fixer with the brief.
6. **Verify the fix.** `team_state advance --phase test`. test-runner must be GREEN,
   then `team_state advance --phase audit` and rule-auditor must APPROVE. On RED or VETO
   (record a team lesson first), send the findings back to
   check-fixer (max 2 rounds), else stop.
7. **Re-run.** `.venv/bin/python -m workshop_validator.automate --reset-gate`, then go
   to step 2 (Run). The gate keeps the same org after an inconclusive run or a
   failure before the org changed, and moves to the next org otherwise.
8. **Lessons.** `team_state advance --phase lessons`. lessons-keeper with the step text,
   the triager's cause and `never:` rule, what fixed it, the PR link (after publish,
   re-send it), and every team failure of this iteration. Not optional: an iteration
   with a diagnosed cause and no recorded root cause is not finished.
9. **Report.** `team_state advance --phase report`. Run
   `.venv/bin/python -m workshop_validator.step_board --render <run_dir>` and then
   `.venv/bin/python -m workshop_validator.step_board --history ~/.workshop-validator`.
   The browser already shows the report; it reloads on its own only while running, so
   `open <run_dir>/report.html` once more after re-rendering.
10. **Publish** (only if code changed): `team_state advance --phase publish`. safety-scanner
   must be CLEAR, then git-publisher with the changed paths, the test result, the
   auditor verdict and the outcome counts. `team_state log --agent git-publisher --action pr-created --link <url>`, re-render.
11. `team_state finish --outcome <outcome>`.

## Final reply to the user
Outcome (VERIFIED / FAILED / INCOMPLETE), mode (quick/full), verified step count,
org used (N of 5), pass/fail/review counts, the first failure in one line, what
was fixed, PR link, report path.
