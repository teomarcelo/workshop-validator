# Merge gates

Every layer a change touches has a deterministic check that prints one
summary line, plus a fresh reviewer verdict where judgment is needed.
`team_gates merge-when-green <pr>` refuses to merge until all of them pass.

| Layer | Deterministic check (one line) | Reviewer verdict | Where |
| --- | --- | --- | --- |
| Every PR | gitleaks secrets scan; `public_safety` (paths, home paths, guide names, private team files) | code-reviewer, safety-scanner | CI + local |
| Every PR | `team_gates em-dash` (added doc lines), `team_gates decision-rows` (no removed W- rows or decision files) | | CI on PRs |
| Code | unit tests, `compileall` | rule-auditor when `workshop_validator/`, `.claude/`, `.cursor/` or CLAUDE.md change | CI |
| Browser | synthetic Chromium smoke | | CI |
| Merged tree | tests, `public_safety`, `compileall` on origin/main merged with the PR head | | `merge-when-green` |
| New tests | `team_gates prove-tests` (base is the merge-base with the PR base branch; new or changed tests pass on head; each changed `workshop_validator/*.py` file, swapped to base alone, makes at least one of them fail or error; skips and no tests are UNPROVEN; SKIP only with no code change) | | `merge-when-green` |
| Report render | | visual-qa (score 8 or more), accessibility-auditor, instructor-panel for big changes | brief |
| Claims | | evidence-auditor | brief |
| Private data | | privacy-reviewer (Opus) | brief |
| CI, hooks, unattended | | security-reviewer (Opus) | brief |
| Names and brands | | compliance-reviewer (Opus) | brief |
| Private team data | `team_gates register` (append-only snapshot), `team_gates clock12` (dashboard and standups) | | `team_gates all --private` |
| Release to the public | owner's yes | | owner |

prove-tests guards against weak or mistaken tests, not hostile ones; review still reads test code that writes files, calls `os._exit` or patches `sys.modules`.

## Review verdicts
Reviewers reply in chat. The producer saves each reply with
`team_kit review --pr <N> --role <role> --head <headRefOid>` (the reply on
stdin). That writes `~/.workshop-validator/team/reviews/pr-<N>-<role>.md`
(0600) with `HEAD: <sha>` as its first line. A verdict passes when its last
`VERDICT:` line is `APPROVE` or `CLEAR` and the first non-empty line names the
PR's current head. A `HEAD:` line anywhere else does not count, so a push after
review makes the verdict stale.

A review counts only if the producer saved it this way. The write guard denies
the Write, Edit and NotebookEdit tools in `reviews/` for every session, so no
agent hand-writes a verdict by mistake. This is a guard against mistakes, not a
security boundary: an agent running as the same user with Bash can still write
any file in that folder. The producer, not the file, is the source of truth.

code-reviewer and safety-scanner are always required. rule-auditor is added for
`workshop_validator/`, `.claude/`, `.cursor/` and CLAUDE.md. security-reviewer
is added for `.github/`, `.claude/settings.json`, `.claude/settings.local.json`,
`.claude/agents/`, `.claude/workflows/`, and `team_hooks.py`, `team_gates.py`,
`team_kit.py`, `team_state.py`, `watchdog.py` and `public_safety.py` in
`workshop_validator/`.

## Merge when green
`merge-when-green` refuses (exit 2, one line) a PR that is not open into main,
comes from a fork, has no `headRefOid`, or whose head is not `automation/*`.
It fetches both `main` and the head, then runs the merged-tree gates on
origin/main merged with the exact `headRefOid`. The squash merge passes
`--match-head-commit <headRefOid>`, so a push after the gates stops the merge.

## After the merge
`merge-when-green` waits for main CI on the merge commit. If it is red, it
opens a revert PR in the same turn, from a temporary worktree (never the owner
checkout). The local revert branch is deleted afterwards, so a retry does not
collide. If the revert fails, it prints one `REVERT FAILED` line and exits
non-zero: revert by hand. Either way that is an escape: log it.

## Write guard
Agents write only in their own checkout or worktree, the private team folder
(except `reviews/`) and the scratch folder. In the live main checkout the guard
also denies `.claude/settings.json`, `.claude/settings.local.json`,
`team_hooks.py`, `team_kit.py`, `.claude/agents/` and `.claude/workflows/`.
Those change only from an agent worktree, which the guard checks by path
containment in `<main checkout>/.claude/worktrees/`. The allowlist glob
`git worktree add .claude/worktrees/*` still matches `..` paths; the write guard
limits harm; tightening it is an owner decision.

## Preferences are gates
- No em dashes: `team_gates em-dash`.
- 12-hour clock: times are formatted at the source (`team_kit.clock12`) and
  `team_gates clock12` fails on any 24-hour or raw ISO time the owner would see.
- Plain English: editor reviews every doc the owner reads.

## The gates are tested too
`tests/test_team_kit.py` covers every gate with fixtures: the write guard
denies a folder that does not resolve, the Stop hook lets a second stop in a
row through, a removed decision row fails, a missing verdict blocks a merge.
`merge-when-green`, the revert and the worktree cleanup are tested with an
injected command runner, so no test calls gh, the network or an org.
