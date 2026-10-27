# Brief <id>: <one-line task>

Copy to `~/.workshop-validator/team/briefs/<id>.md` (private). This file is
the only instruction the executor gets, so it must be complete.

- **Role:** <role file in .claude/agents/>
- **Branch:** `automation/<YYYY-MM-DD>-<slug>` (from origin/main)
- **Binding rows:** W-<nnn>, W-<nnn> (grep the register by id; quote the ruling here)
- **Owned folders:** <paths this role may change>
- **Layers touched:** code / tests / docs / report render / CI / private data / names
- **Inputs:** <files, lessons (`lessons --step "..."`), research notes, references>

## Steps
1. ...

## Acceptance (one check per layer touched)
- Code: `.venv/bin/python -m unittest discover -s tests` green; a new test fails without the change.
- Docs: `team_gates em-dash` PASS; editor pass.
- Report render: visual-qa score 8 or more, light and dark.
- Private data: `public_safety` PASS; privacy-reviewer APPROVE.
- CI or unattended: security-reviewer APPROVE.

## Verification budget
- Tests: <unit only | unit + browser smoke>. No live org runs (those go through /validate-guide).
- Tokens: about <N>k. Tool calls: about <N>. Stop and report if you pass 2x.

## Report format (final reply)
```
BRANCH: automation/...
COMMITS: <sha> <subject>
DONE: <what changed, 3 lines max>
ACCEPTANCE: <each check above with PASS/FAIL and the summary line>
OPEN: <anything not done, with why>
INBOX: <proposed register rows written to decisions/inbox/, or none>
LEDGER: tokens ~<n>, tool calls ~<n>, minutes ~<n>
```
