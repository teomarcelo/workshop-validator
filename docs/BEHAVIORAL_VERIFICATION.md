# Behavioral validation and negative-testing acceptance criteria

> This policy supersedes the previous numbering-focused instructor sample.
> Status: synthetic tests exist; no live Salesforce exercise has passed.

## Validate what the learner actually does

Every guide action (including lettered substeps) needs source-page anchoring, a confirmed prior state, a literal attempted action, observed resulting state, independent assertions and, where relevant, downstream behavioral verification. A screenshot, successful click, plausible selector, generated unit test or model confidence is NOT enough to declare the guide step correct.

Each manual change may be:
- REQUIRED ON THE TESTED BASELINE: the expected state was absent before the change and appeared only after it, with downstream behavior functioning.
- ALREADY SATISFIED: the target state existed before the guide-directed change. This is not an automatic pass or proof the instruction is obsolete.
- REDUNDANCY CANDIDATE: a clean baseline and preceding steps were reproduced; the manual instruction was safely omitted; downstream checks passed; and an intentionally broken version of the same property was caught by the test oracle.
- FALSE PASS RISK: a deliberately broken version still passes. The assertion is too weak and guide corrections must be blocked.
- INCORRECT OR OBSOLETE UI: an instruction cannot be followed literally, or its demonstrated result has changed. Investigate and replay any alternative, then verify it independently before proposing a replacement.
- ENVIRONMENT BLOCKED: a real rate limit or trial-org outage prevents verification. Respect dependencies and the approved Option A policy; do not blame the guide.

## Before-state and omission testing

Capture the relevant generated or auto-populated field/script value BEFORE the learner edits it. If it is already correct, do not silently edit it, and do not claim this proves the instruction redundant. Run a separate replay from a known reset state, omit only that action, perform all independent downstream assertions, and report whether the flow works. When a multi-part instruction contains several fields, test each subpart separately: one auto-generated value does not make every other field unnecessary.

## Purposefully broken tests

First test with synthetic Playwright pages (public CI). In an explicitly authorized disposable Salesforce trial org with reset guarantees, deliberately corrupt one safely reversible field or mock a harmless missing control. The verifier must catch that defect as FUNCTIONAL_FAIL or NEEDS_REVIEW. If it still says PASS, treat the entire validation path as unreliable, restore the baseline and fix the expected assertions. Never inject destructive faults into shared/production orgs, bypass rate limits, or leak test data to public GitHub artifacts.

For any claim that a manual instruction can be removed, a tested negative control must demonstrate that the checking logic is capable of detecting an incorrect state. Even after that, the outcome is an instructor-review CANDIDATE until approved.

## Cosmetic issues are separate

Repeated numbering, step counters continuing between tasks or pages, line wrapping and spelling are low-priority editorial issues. They must not appear in a behavioral validation report as broken Salesforce UI. Do not recommend renumbering when the user is asking for actionable workflow errors. If the source guide is otherwise executable and behaves correctly, typography does not invalidate the action.

## Independent verification and outputs

The quick HTML report and the annotated PDF are BOTH required outputs. Each substantive finding needs original page and step ID, current observed state, tested expected result, evidence IDs, actual outcome, and a precise suggested amendment only after verified execution. Unverified instructor observations must be labeled as such. Preserve original PDF page appearance and use review sheets only for findings that matter to instructions. Track coverage of every page and action; an incomplete run is not green.

The public Python module workshop_validator.necessity analyzes independent verifier outcomes for before-state, skip-path, executed-path and negative-control checks. The browser negative tests deliberately inject incorrect synthetic script data and verify the checks reject it. This is test-harness behavior, not current Salesforce product validation.

## Run-bound report behavior

Private outputs now carry per-step before/after classifications and evidence
identifiers. A pre-existing correct value is a review question, not proof that
the manual edit can be omitted. An inconclusive executed path blocks removal
recommendations. The annotated review copy preserves all original pages.

## Live-test blocker

A real Salesforce run requires a separately approved private runtime; the actual PDF and approved executable manifest outside the public repository; rotated trial-org credentials in protected secrets; safe reset/baseline mechanisms; and a step-by-step browser replay. Neither the public GitHub tests nor the corrected sample review PDF establishes a live Salesforce PASS.

## Private runner pre/post necessity probe (public-safe scaffold)

An operator-reviewed private plan may include `necessity_assertions` on a
specific manual-edit step, using the same role/name, assertion_id, kind,
expected and operator fields as normal `assertions`. These describe the
**desired generated/default value**, not merely a visible button. The runner
collects these observations before the literal action, then executes the
authored action and collects all postconditions. Necessity assertions are
automatically added to the independent postcondition gate: a wrong value
cannot PASS merely because an unrelated Save button is visible.

If the before-state evidence is missing/ambiguous, the runner refuses to
mutate that step. Its private `behavioral-probes.json` contains only
classifications, reasons and evidence identifiers (no raw DOM values);
`status.json` points to it. An already-correct pre-state is **not** a
removal recommendation: the runner does not auto-skip, reset, inject faults,
or claim a redundancy candidate. Those controlled replay stages remain
separate, private, authorized work. Synthetic unit tests prove that a
deliberately wrong value is independently rejected.


## Live form-value assertions (synthetic support)

Use `DOM_VALUE` with the `eq` operator for generated input and textarea
values. `DOM_TEXT` observes rendered text and may not reflect a form control's
current value after editing. `DOM_VALUE` reads Playwright's live `input_value()`
property, rejects ambiguous/missing controls, and independently compares the
observed string with the exact authored expected value. Weak substring
operators are deliberately unsupported for `DOM_VALUE`. Synthetic Chromium
fault injection changes an initially correct textarea to a wrong value and
requires `FUNCTIONAL_FAIL`, including through the pre/post necessity probe.
This is not evidence of any current Salesforce behavior.
