# Role checklist (day one, 2026-10-26)

Every role is a file in `.claude/agents/`. A role added later needs a
register row naming which line of this checklist failed.

## Every project
| Line | Role | Status |
| --- | --- | --- |
| Producer | `producer` | kept (Opus) |
| Code reviewer, read-only | `code-reviewer` | kept |
| QA engineer | `test-runner` (unit, compile, browser smoke) and `step-verifier` (walk evidence) | kept |
| Security reviewer | `security-reviewer` | kept (Opus) |
| User's eyes visual QA | `visual-qa` | kept |
| Privacy / redaction | `privacy-reviewer`, `safety-scanner` | kept: guide text and org data are in reach |
| Evidence auditor | `evidence-auditor` | kept: reports claim PASS/FAIL |
| Compliance | `compliance-reviewer` | kept: Salesforce brand, trial-org terms, employer policy |

## Anything people look at (report.html, results.pdf, dashboard)
| Line | Role | Status |
| --- | --- | --- |
| Creative director | merged into `visual-qa` (scores 0 to 10, below 8 blocks) | cut as a separate role: one report surface, no brand system |
| UI designer | `ui-designer` | kept |
| UX designer and copy | `writer` + `ui-designer` | merged: small surface |
| Motion designer | none | cut: static report, no animation |
| UX researcher | `instructor-panel` | merged: the panel is the research we can afford at $0 |
| Accessibility auditor | `accessibility-auditor` | kept |
| Audience panel | `instructor-panel` | kept |

## Anything that is read
| Line | Role | Status |
| --- | --- | --- |
| Writer | `writer` | kept |
| Editor | `editor` | kept |
| Evidence auditor | `evidence-auditor` | kept |
| Talk track | `writer` writes a one-page talk track on request | kept as a deliverable, not a role |

## By type: data from other systems (PDF in, org in)
| Line | Role | Status |
| --- | --- | --- |
| Pipeline engineer | `pipeline-engineer` | kept |
| Privacy gate | `privacy-reviewer` + `public_safety` | kept |
| Live run operator | `run-operator`, `failure-triager`, `check-fixer`, `rule-auditor`, `lessons-keeper`, `git-publisher` | kept (the /validate-guide loop) |

## Cut, not applicable
| Line | Reason |
| --- | --- |
| Web: frontend, performance, SEO, analytics, release | No public site. The report is a local file. |
| Mobile or game: runtime engineers, device QA, store compliance, store marketer, economy, tech artist, audio | Not a mobile app or game. |
