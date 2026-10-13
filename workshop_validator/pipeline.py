"""Evidence-gated, dependency-aware one-run workshop validation pipeline.

The caller supplies a trusted authored browser plan and an authenticated
Playwright Page from an APPROVED PRIVATE runner. No login, secrets, PDF-derived
private content, or public artifact uploads occur here. Actual AI-assisted
planning, rate-limit retry queues and cloud deployment are separate milestones.
"""

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Callable, Sequence

from .browser_worker import (UIAction, UIAssertion, BrowserActionError,
                             perform_actions, collect_observations)
from .checkpoints import CheckpointStore
from .dependencies import Step
from .evidence import StepContext, Observation
from .report import ReportStep, render_report


class RateLimitDetected(RuntimeError):
    """Only raise this from a trustworthy observed HTTP 429 classification."""


class PlanMismatch(ValueError):
    """A trusted source-action inventory and executable plan diverge."""


@dataclass(frozen=True)
class PlannedStep:
    step_id: str
    page: int
    title: str
    actions: tuple[UIAction, ...]
    assertions: tuple[UIAssertion, ...]
    dependencies: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class PipelineOutcome:
    run_id: str
    statuses: dict[str, str]
    source_reconciled: bool
    html: str
    fully_verified: bool


def _default_execute(page, step: PlannedStep, context: StepContext) -> list[Observation]:
    perform_actions(page, step.actions)
    return collect_observations(page, context, step.assertions)


def _evidence_ids(db_path: str | Path, run_id: str) -> dict[str, tuple[str, ...]]:
    with sqlite3.connect(str(db_path)) as db:
        rows = db.execute(
            "SELECT step_id, verification_json FROM checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchall()
    result = {}
    for step_id, data in rows:
        try:
            ids = json.loads(data or "{}").get("evidence_ids", [])
        except (TypeError, ValueError):
            ids = []
        result[step_id] = tuple(ids) if isinstance(ids, list) else ()
    return result


def validate_plan(page, db_path: str | Path, run_id: str, org_id: str,
                  plans: Sequence[PlannedStep], *,
                  source_action_ids: tuple[str, ...] | None = None,
                  source_reconciled: bool = False,
                  executor_id: str = "playwright-worker",
                  verifier_id: str = "independent-verifier",
                  execute_step: Callable | None = None) -> PipelineOutcome:
    """Run each eligible step once, then produce a fail-closed private HTML report.

    A reconciled source MUST supply the exact same set of action IDs as plans.
    Thus a caller cannot report COMPLETE while silently omitting a guide step.
    Callers must only set source_reconciled based on a genuine reviewed source
    reconciliation, never an executor's unverified opinion.

    This is a single worker's local execution loop. No automatic HTTP 429
    retries or durable cloud restart/re-authentication are claimed.
    """
    plans = tuple(plans)
    if not plans or len(plans) > 5000:
        raise PlanMismatch("A nonempty, bounded set of steps is required")
    ids = [step.step_id for step in plans]
    if len(ids) != len(set(ids)):
        raise PlanMismatch("Duplicate step IDs")
    if any(type(s.page) is not int or s.page < 1 or not s.title.strip() for s in plans):
        raise PlanMismatch("Invalid page or instruction title")
    if source_reconciled and source_action_ids is None:
        raise PlanMismatch("Cannot claim complete source without action IDs")
    if source_action_ids is not None:
        if len(source_action_ids) != len(set(source_action_ids)) or set(source_action_ids) != set(ids):
            raise PlanMismatch("Executable plan does not cover all source actions")
    if not executor_id.strip() or not verifier_id.strip() or executor_id == verifier_id:
        raise PlanMismatch("Executor and verifier identities must be distinct")
    if any(not s.assertions for s in plans):
        raise PlanMismatch("All steps need independently verifiable assertions")

    executor = execute_step or _default_execute
    store = CheckpointStore(db_path)
    store.create_run(
        run_id, org_id,
        [Step(s.step_id, s.dependencies, s.required) for s in plans],
        {s.step_id: [a.spec for a in s.assertions] for s in plans}
    )
    by_id = {s.step_id: s for s in plans}
    while True:
        sched = store.schedule(run_id)
        if not sched.runnable:
            break
        for step_id in sched.runnable:
            step = by_id[step_id]
            attempt = store.start_step(run_id, step_id)
            context = StepContext(
                run_id, org_id, step_id, executor_id, verifier_id
            )
            try:
                observations = executor(page, step, context)
            except RateLimitDetected:
                store.block_step(run_id, step_id, attempt, "ENV_BLOCKED")
            except BrowserActionError:
                # A missing target/failed browser action has no trusted
                # supporting observations; verifier must refuse PASS.
                store.finish_step(run_id, step_id, attempt, context, ())
            else:
                store.finish_step(run_id, step_id, attempt, context, observations)

    statuses = {s.step_id: store.status(run_id, s.step_id)[0] for s in plans}
    ids_by_step = _evidence_ids(db_path, run_id)
    report_items = []
    for s in plans:
        status = statuses[s.step_id]
        findings = {
            "ENV_BLOCKED": "Environment blocked; independent steps may continue.",
            "NOT_RUN": "Prerequisite not verified or execution not started.",
            "NEEDS_REVIEW": "Required observation missing, ambiguous or invalid.",
            "FUNCTIONAL_FAIL": "Observed UI did not satisfy a required assertion.",
        }
        report_items.append(ReportStep(
            s.step_id, s.page, s.title, status,
            ids_by_step.get(s.step_id, ()),
            findings.get(status, ""),
            s.required,
        ))
    html = render_report(run_id, report_items,
                         inventory_verified=source_reconciled)
    fully_verified = bool(store.schedule(run_id).complete and source_reconciled)
    return PipelineOutcome(run_id, statuses, source_reconciled, html, fully_verified)
