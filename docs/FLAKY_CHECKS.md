# Flaky check quarantine

A flaky check is a step verification that gives different statuses across runs of the same guide on the same Salesforce instance when no validator code changed between runs. This document defines detection, quarantine, cause categories, and the path out.

## Definition and detection

### What makes a check flaky

A check is flaky when all these conditions hold:

1. **Same guide**: identical PDF, same step text, same step sequence number.
2. **Same instance**: runs happened on the same Salesforce org instance (compared via `Organization.InstanceName`).
3. **No code change**: the validator commit SHA did not change between the runs showing different statuses.
4. **Different statuses**: the step showed at least two different final statuses (PASS, UI_MISMATCH, FUNCTIONAL_FAIL, NEEDS_REVIEW, ENV_BLOCKED, or AUTH_BLOCKED) across the runs. A status is final after step-verifier judged it.
5. **Not a guide fix**: the step text itself did not change (the guide is identical), so the difference comes from the validator, not from updated instructions.

Example: step "Click Save" in guide `intro.pdf` gave PASS on run `run-a` on instance `XX00`, then gave UI_MISMATCH on run `run-b` on the same instance, with validator commit `a0026af` for both runs. This is flaky.

Counter-example: the same step gave PASS, then gave UI_MISMATCH after a fix to `workshop_validator/clicks.py` changed the selector logic. Not flaky; the code change explains it.

### Data we already store

**history.json** (per run, in `~/.workshop-validator/reports/history.json`):
- `run_id`: timestamp-based identifier.
- `guide`: guide filename (not full text).
- `instance`: dict with `instance` key holding the Salesforce instance name like `XX00`.
- `outcome`: success, failed, or inconclusive.
- `counts`: dict of status counts (how many PASS, UI_MISMATCH, etc.).
- `first_failure`: dict with `step_id`, `page`, and `status` of the first non-PASS step.
- `run_dir`: path to the private run directory containing `summary.json`, `board.jsonl`, and `walk.webm`.

**board.jsonl** (per step, in each `runs/<org-key>/<run-id>/board.jsonl`):
- `step_id`: unique identifier for the step in the guide.
- `status`: final status after step-verifier review.
- `text`: the step instruction text (private, never committed).
- `note`: reason for the status, including evidence or why it failed.
- `page`: PDF page number.
- `suspect`: optional flag when check-fixer or step-verifier sees something unusual.

**lessons.json** (aggregated, in `~/.workshop-validator/lessons.json`):
- De-duplicated step rows with `kind`, `title` (step text), `status`, `detail`, `seen` (count), `first`, and `last` (dates).
- Root cause and team lessons with `fix`, `never` (rule), and `pr` fields.
- A lesson seen multiple times has `seen > 1`, which lessons-keeper prints as RECURRING.

### What is missing

1. **Validator version per run**: `history.json` does not store the commit SHA or tag that produced the run. Without this, we cannot distinguish a flaky check from a code-change effect.
2. **Cross-run step correlation**: `board.jsonl` is per run. We have no index mapping (guide, step_id, instance) to a list of (run_id, status, commit). Detecting flaky checks requires scanning all runs for a guide on an instance, reading each `board.jsonl`, and comparing step statuses.
3. **Quarantine storage**: there is no file or database table tracking which steps are in quarantine, their cause category, when they entered, or what stable runs they need to exit.

## Quarantine system

### Quarantine storage (private only)

A new file `~/.workshop-validator/quarantine.json` (mode 0600) stores flaky steps:

```json
{
  "entries": [
    {
      "guide": "intro.pdf",
      "step_id": "S-12",
      "step_text": "Click Save",
      "instance": "XX00",
      "cause": "timing",
      "first_seen": "2026-10-20",
      "runs": [
        {"run_id": "run-a", "status": "PASS", "commit": "a0026af"},
        {"run_id": "run-b", "status": "UI_MISMATCH", "commit": "a0026af"}
      ],
      "stable_count": 0,
      "stable_needed": 3
    }
  ]
}
```

Fields:
- `guide`, `step_id`, `step_text`, `instance`: identity of the flaky step.
- `cause`: one of `timing`, `selector`, `state-leak`, `platform`, or `unknown`.
- `first_seen`: ISO date when the flakiness was first detected.
- `runs`: list of dicts with `run_id`, `status`, and `commit` showing the different outcomes.
- `stable_count`: how many consecutive runs gave the same status with no code change (reset to 0 when a fix PR merges or when status differs again).
- `stable_needed`: number of stable runs required to exit quarantine (default 3).

### Cause categories

**timing**: the check depends on a browser state that is not fully loaded or settled. Examples: a button is clicked before it is enabled, text is read before it appears, a modal is dismissed before it finishes animating.

**selector**: the check uses a DOM selector (CSS, text match, or accessibility role) that matches multiple elements or that matches different elements on different page states. Examples: "Save" matches both a top button and a modal button; a dynamic ID changes between page loads.

**state-leak**: the check depends on a previous step's side effect that is not always present. Examples: a previous step's conditional branch left a field empty on one run but filled on another; a cache or session token from a prior run affects the current step.

**platform**: Salesforce platform behavior is inconsistent. Examples: HTTP 429 appears unpredictably; a Lightning component sometimes renders one control, sometimes another; a Setup page shows different tabs based on feature flags or org edition.

**unknown**: the cause is not yet diagnosed.

When a fix PR addresses a flaky check, the PR description must name the cause. If the cause was `unknown`, the PR changes it to the specific category. The fix itself adds synchronization (timing), tightens the selector (selector), adds a precondition check (state-leak), or adds a platform condition like org edition (platform). Every fix PR must include a regression test that fails without the fix.

### How reports show flaky steps

A step in quarantine is shown with its **real status** (the one from the current run), never artificially upgraded to PASS. Next to the status chip, a second chip labeled `FLAKY (timing)` or `FLAKY (unknown)` appears in a muted color (orange or yellow). The step note includes: "This step is in quarantine due to [cause]. It has given [list of statuses] across [N] runs on this instance. [stable_count]/[stable_needed] stable runs so far."

Example in `report.html`:

```
12. Click Save
Status: [UI_MISMATCH] [FLAKY (timing)]
Note: Button "Save" was not found. Similar controls: "Save & New", "Cancel".
This step is in quarantine due to timing. It has given PASS, UI_MISMATCH across 3 runs on XX00. 0/3 stable runs so far.
```

The history page (`reports/index.html`) adds a flaky-step filter tab showing all steps currently in quarantine, grouped by guide and instance.

The evidence-auditor (W-008 binding: a flaky check may never become a PASS) verifies that `verify_step` in `evidence.py` never upgrades a status because a step is in quarantine. The quarantine chip is purely informational; the status is the one from the current run's evidence.

### Exiting quarantine

A flaky step leaves quarantine in one of two ways:

1. **Fix PR merged**: a reviewed PR that changes validator code (not the guide), adds a regression test, names the cause category, and explicitly lists the quarantined step in the PR description. After the PR merges, the next run on the same instance resets `stable_count` to 0. If the step gives the same status for 3 consecutive runs with no further code changes, it exits quarantine.

2. **Stable runs**: the step gives the same status (any status) for 3 consecutive runs with no validator code change, and no fix PR claimed it. This means the flakiness resolved on its own (possibly a Salesforce platform fix or a guide side effect that stabilized). The step exits quarantine with a note in `quarantine.json` marking it as `resolved_stable` with the final status and the 3 run IDs.

When a step exits quarantine, its entry in `quarantine.json` moves to an `archived` list with the exit reason, date, and final status. Archived entries are kept for 90 days, then dropped on next write.

## Implementation changes

This is a design-only brief; no code changes happen here. A follow-on build brief (to be written by the producer) would implement these modules and tests:

### 1. `workshop_validator/flaky_detector.py`

**What it does**: scans `history.json` and each run's `board.jsonl` to detect flaky steps. Called after each run by `automate.py`.

**Key functions**:
- `detect_flaky(private_root: Path, current_run_id: str) -> list[dict]`: reads `history.json`, groups runs by (guide, instance), loads each `board.jsonl`, compares statuses per step_id, filters for runs with the same commit SHA, returns a list of newly detected flaky steps.
- `add_to_quarantine(private_root: Path, flaky_steps: list[dict]) -> None`: merges new flaky steps into `quarantine.json`, de-duplicating by (guide, step_id, instance), updates `runs` list, sets `cause` to `unknown` if not already diagnosed.

**Acceptance test**: synthetic test with 2 fake runs (same guide, instance, commit; different statuses for step S-1) confirms `detect_flaky` returns 1 entry and `add_to_quarantine` writes it to `quarantine.json`.

### 2. `workshop_validator/quarantine_store.py`

**What it does**: load, update, and save `quarantine.json`. Provides functions for checking if a step is in quarantine, updating stable_count, archiving resolved steps.

**Key functions**:
- `load_quarantine(private_root: Path) -> dict`: returns the quarantine dict or empty default.
- `is_quarantined(quar: dict, guide: str, step_id: str, instance: str) -> dict | None`: returns the quarantine entry if found, else None.
- `record_run(quar: dict, guide: str, step_id: str, instance: str, run_id: str, status: str, commit: str) -> None`: appends the run to the entry's `runs` list, updates `stable_count` (increment if status matches previous and commit unchanged, else reset to 1).
- `mark_fixed(quar: dict, guide: str, step_id: str, instance: str, pr: str, cause: str) -> None`: sets the cause category, records the fix PR, resets `stable_count` to 0.
- `archive_stable(quar: dict, guide: str, step_id: str, instance: str, final_status: str) -> None`: moves the entry to `archived` with exit reason `resolved_stable`.
- `save_quarantine(private_root: Path, quar: dict) -> None`: writes to `quarantine.json` with mode 0600, drops archived entries older than 90 days.

**Acceptance test**: synthetic test creates a quarantine entry, calls `record_run` 3 times with the same status and commit, confirms `stable_count` reaches 3 and `archive_stable` moves it to `archived`.

### 3. `workshop_validator/automate.py` (changes)

**What it does**: after appending to `history.json` and before writing the final report, call `flaky_detector.detect_flaky` and `quarantine_store.record_run` for every step in the current run.

**Changes**:
- After line 313 (`history_path = append_history(...)`), call `flaky_detector.detect_flaky(private_root, run_id)` and `flaky_detector.add_to_quarantine(private_root, newly_detected)`.
- After rendering the report, call `quarantine_store.record_run` for each step, updating `stable_count`.
- Store the current commit SHA in `summary.json` and `history.json` (new field `commit`).

**Acceptance test**: smoke test confirms `quarantine.json` is created after a run with a flaky step (synthetic board.jsonl with 2 runs, same guide/instance/commit, different statuses).

### 4. `workshop_validator/step_board.py` (changes)

**What it does**: when rendering `report.html`, check `quarantine.json` for each step. If quarantined, add a FLAKY chip and the quarantine note to the step's display.

**Changes**:
- In `write_run_report`, load `quarantine.json` via `quarantine_store.load_quarantine`.
- For each step in the report, call `quarantine_store.is_quarantined(quar, guide, step.step_id, instance)`.
- If quarantined, add a `<span class="chip chip-flaky">FLAKY ({cause})</span>` after the status chip, and append the quarantine note to `step.note`.
- Add a flaky-step summary section to the report header showing the count of quarantined steps in this run.

**Acceptance test**: synthetic test with a fake quarantine entry and a board step confirms the rendered HTML contains `<span class="chip chip-flaky">FLAKY (timing)</span>`.

### 5. `workshop_validator/step_board.py` history page (changes)

**What it does**: add a flaky-step filter tab to `reports/index.html` listing all currently quarantined steps, grouped by guide and instance.

**Changes**:
- In `write_history_index`, load `quarantine.json`.
- Add a new tab "Flaky Steps" showing a table: Guide, Step ID, Step Text, Instance, Cause, Runs, Stable Count.
- Each row links to the most recent run's `report.html` for that (guide, instance).

**Acceptance test**: synthetic test confirms the history page HTML includes a tab with class `tab-flaky` and a table row for a quarantined step.

### 6. `workshop_validator/evidence.py` (assertion)

**What it does**: assert that `verify_step` never upgrades a status because a step is in quarantine. The quarantine does not grant PASS.

**Changes**:
- Add a comment in `verify_step` above the final return: "Quarantine status: a flaky step is shown with its real status from the current run, never artificially upgraded. The FLAKY chip is added by step_board rendering only."
- No logic change; the function already returns the verified status from current evidence.

**Acceptance test**: read the existing unit tests for `verify_step` in `tests/test_evidence.py`. Confirm none of them pass a quarantine flag or change the status based on historical flakiness. Write a note in the test docstring: "Quarantine does not affect verify_step; status is always based on current run evidence."

### 7. `workshop_validator/team_gates.py` (new gate)

**What it does**: add a gate that checks evidence.py never reads quarantine.json or takes a quarantine parameter.

**Changes**:
- Add `def gate_evidence_no_quarantine() -> tuple[bool, str]` that greps `evidence.py` for the word "quarantine" (case-insensitive). If found in logic (not just comments), return `(False, "evidence.py must not read quarantine data; quarantine is display-only")`.
- Call this gate in `team_gates all`.

**Acceptance test**: manually add the word "quarantine" to a function in `evidence.py`, run `team_gates all`, confirm it fails with the expected message. Revert, confirm it passes.

## Code change summary

| File | Change | Acceptance test |
| --- | --- | --- |
| `workshop_validator/flaky_detector.py` | New module: detect flaky steps by comparing runs | Synthetic 2-run test detects 1 flaky step |
| `workshop_validator/quarantine_store.py` | New module: load/save/update quarantine.json | Synthetic test records 3 stable runs and archives |
| `workshop_validator/automate.py` | Call detector after each run, store commit SHA | Smoke test creates quarantine.json after flaky run |
| `workshop_validator/step_board.py` (report) | Add FLAKY chip and note to quarantined steps | Synthetic test confirms chip in rendered HTML |
| `workshop_validator/step_board.py` (history) | Add flaky-step filter tab to history page | Synthetic test confirms tab and table row |
| `workshop_validator/evidence.py` | Assert no quarantine logic affects verify_step | Test docstring confirms no quarantine parameter |
| `workshop_validator/team_gates.py` | New gate: evidence.py never reads quarantine | Manual test with planted word confirms gate fails |

Every test is synthetic (no live Salesforce, no real credentials, no real guide text).

## Register row proposal

See the proposed register row in `~/.workshop-validator/team/decisions/inbox/2026-10-26-writer-flaky-checks.md`.
