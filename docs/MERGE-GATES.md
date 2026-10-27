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
| Report render | | visual-qa (score 8 or more), accessibility-auditor, instructor-panel for big changes | brief |
| Claims | | evidence-auditor | brief |
| Private data | | privacy-reviewer (Opus) | brief |
| CI, hooks, unattended | | security-reviewer (Opus) | brief |
| Names and brands | | compliance-reviewer (Opus) | brief |
| Private team data | `team_gates register` (append-only snapshot), `team_gates clock12` (dashboard and standups) | | `team_gates all --private` |
| Release to the public | owner's yes | | owner |

Reviewers write `~/.workshop-validator/team/reviews/pr-<N>-<role>.md` whose
last `VERDICT:` line is `APPROVE` or `CLEAR` to pass. code-reviewer and
safety-scanner are always required; rule-auditor is added by path.

## After the merge
`merge-when-green` squash-merges, waits for main CI on the merge commit, and
if it is red opens a revert PR in the same turn. That is an escape: log it.

## Preferences are gates
- No em dashes: `team_gates em-dash`.
- 12-hour clock: times are formatted at the source (`team_kit.clock12`) and
  `team_gates clock12` fails on any 24-hour or raw ISO time the owner would see.
- Plain English: editor reviews every doc the owner reads.

## The gates are tested too
`tests/test_team_kit.py` covers every gate with fixtures: the write guard
denies a folder that does not resolve, the Stop hook lets a second stop in a
row through, a removed decision row fails, a missing verdict blocks a merge.
