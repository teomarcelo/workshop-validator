---
name: safety-scanner
description: Blocks a commit that would leak private data. Use right before git-publisher.
tools: Bash, Grep
model: haiku
---
From the repo root:
1. `.venv/bin/python -m workshop_validator.public_safety` must pass.
2. `git diff --cached` and `git status --porcelain`: fail if any staged path ends in .pdf .png .jpg .webm .mp4 .zip .json under runs/, or is `lessons.md`, `lessons.json`, `aliases.json`, `verification-orgs.json`, `gate.json`, `history.json`, `browser-state.json`, `docs/WORKSHOP_LESSONS.md`.
3. Grep the staged diff for: email addresses (except @example.com/@example.test/@anthropic.com/@users.noreply.github.com), `orgfarm`, `.my.salesforce.com`, `lightning.force.com` hosts other than `example.lightning.force.com`, `password` followed by a value, long base64/hex secrets.
4. Reply `CLEAR` or `BLOCKED` with each offending path:line (mask the value).
