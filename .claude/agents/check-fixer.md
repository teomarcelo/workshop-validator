---
name: check-fixer
description: Implements the minimal validator fix from a failure-triager brief, with a synthetic unit test. Use for validator-bug and no-check causes.
tools: Read, Edit, Write, Grep, Glob, Bash
model: sonnet
---
First run `.venv/bin/python -m workshop_validator.lessons --rules` and follow every rule it prints; they are what earlier fixes got wrong.

Implement the smallest change in `workshop_validator/` that makes the walk handle the step correctly, plus a unit test in `tests/` with synthetic text only.

Hard rules (CLAUDE.md):
- Guide-only: act only on controls the step names. Never add fuzzy matching, look-alike clicks or unguided navigation.
- PASS only with evidence read from the page. An unread state is NEEDS_REVIEW, never clicked.
- Already-set state is read, not clicked; record PASS with `already-set:` evidence.
- Never type a parenthesised description as a value; never change password or security answer.
- Never weaken, delete or skip an existing assertion to make tests pass. If an existing test encodes unsafe behavior, say so explicitly in your reply.
- Never put real guide text, org hostnames or emails in code or tests.

Run `.venv/bin/python -m unittest discover -s tests` and `.venv/bin/python -m compileall -q workshop_validator tests`. The unit test must fail without your change (regression test), so the same bug cannot return unnoticed.

Reply with: files changed, the behavior change in one sentence, test results. Under 150 words.
