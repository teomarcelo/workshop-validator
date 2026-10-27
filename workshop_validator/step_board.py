"""Group guide steps into the pass/fail board.

This is a view of an existing inventory and optional run statuses.
It does not execute Salesforce steps or invent a pass.
"""

from dataclasses import asdict, dataclass
from html import escape
from pathlib import Path
import json
import os
import re
from typing import Mapping, Sequence

from .guide_inventory import GuideInventory, GuideStep
from .report import ReportStep
from . import theme
from .org_instance import UNKNOWN, label as instance_label


_SETUP = re.compile(r"^(?:set[\s-]*up|setup)\b", re.IGNORECASE)
_EXERCISE = re.compile(
    r"^(Exercise\s+\d+(?:-\d+)?|Use Case\s+\d+(?:-\d+)?|Extra Credit\s+#?\d+)(?:\s*:\s*|\s+|$)(.*)$",
    re.IGNORECASE,
)
_FAIL_REASONS = {
    "UI_MISMATCH": "The guide target did not match the observed page.",
    "FUNCTIONAL_FAIL": "The result did not meet a required check.",
    "ENV_BLOCKED": (
        "The environment blocked this step. That is not evidence the guide is wrong."
    ),
    "AUTH_BLOCKED": "Authentication or permissions blocked this step.",
    "NEEDS_REVIEW": "The step ran, but the result is not a pass.",
}
_DOCUMENT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%2316352c'/%3E%3C/svg%3E">
<title>@@TITLE@@</title>
<style>
* { box-sizing: border-box; }
html { color-scheme: light; }
body {
  margin: 0;
  color: #1c2430;
  background: #f3f1eb;
  font: 16px/1.5 "Segoe UI", system-ui, sans-serif;
}
h1, h2, h3 {
  font-family: Georgia, "Iowan Old Style", Palatino, serif;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.25;
}
h1 { margin: 0; font-size: 2rem; }
a { color: #0c5c4a; }
a:hover { color: #083d32; }
.kicker {
  margin: 0 0 6px;
  font: 600 0.75rem/1 system-ui, sans-serif;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #b7d0c6;
}
.lede { margin: 8px 0 0; color: #d7e6df; }
header { background: #16352c; color: #f7f4ec; }
header .wrap { padding-top: 28px; padding-bottom: 24px; }
.wrap {
  max-width: 920px;
  margin: 0 auto;
  padding-left: 24px;
  padding-right: 24px;
}
main.wrap { padding-top: 22px; padding-bottom: 56px; }
.card, article.task {
  background: #fff;
  border: 1px solid #e3ddd2;
  border-radius: 16px;
  box-shadow: 0 1px 2px rgba(28, 36, 32, 0.04);
}
.card { padding: 20px; }
.summary { position: sticky; top: 0; z-index: 2; background: #f3f1eb; padding: 8px 0 12px; }
.notice {
  margin: 0 0 16px;
  padding: 12px 14px;
  border-left: 4px solid #a37b18;
  border-radius: 8px;
  background: #fbf6ea;
}
.notice.failed { background: #fdf2f1; border-left-color: #9f1c1c; }
.notice.ok { background: #eef8f3; border-left-color: #0d6b45; }
.notice.pending { background: #fbf6ea; border-left-color: #a37b18; }
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.metric { background: #f7f6f2; border-radius: 10px; padding: 12px; }
.metric strong {
  display: block;
  font-size: 1.8rem;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}
.metric span, .task-roll, .printed, .footnote, .helper, .unmatched { color: #5c665e; }
.metric.quiet strong { color: #243028; }
.metric.pass strong { color: #0d6b45; }
.metric.fail strong { color: #9f1c1c; }
.metric.pending strong { color: #6a5b3a; }
.task-roll, .unmatched, .footnote, .helper { font-size: 0.92rem; }
.task-roll { margin: 12px 0 0; }
.unmatched { margin: 12px 0 0; }
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-top: 28px;
}
section.exercise:first-of-type .section-head { margin-top: 4px; }
.section-head h2 { margin: 0; font-size: 1.45rem; }
.printed { margin: 4px 0 0; }
article.task { padding: 14px 16px 6px; margin: 10px 0 0; overflow: hidden; }
article.task.fail { border-color: #e4b4ae; background: #fffdfc; }
article.task.pass { border-color: #b7dfc9; }
article.task h3 { margin: 0 0 8px; font-size: 1.05rem; overflow-wrap: anywhere; }
ol { list-style: none; margin: 0; padding: 0; }
li { padding: 8px 0; }
li + li { border-top: 1px solid #efeae2; }
li.fail {
  background: #fff1ef;
  margin: 0 -16px;
  padding-left: 12px;
  padding-right: 16px;
  border-left: 4px solid #9f1c1c;
}
.line {
  display: grid;
  grid-template-columns: 2.25rem minmax(0, 1fr) auto;
  column-gap: 12px;
  align-items: baseline;
}
.num { color: #5c665e; font-variant-numeric: tabular-nums; }
.text { overflow-wrap: anywhere; }
.reason {
  margin: 4px 0 2px calc(2.25rem + 12px);
  color: #7f1d1d;
  font-size: 0.92rem;
}
.badge {
  display: inline-block;
  font: 700 11px/1 system-ui, sans-serif;
  letter-spacing: 0.04em;
  border-radius: 999px;
  padding: 5px 8px;
  white-space: nowrap;
}
.badge.pass { background: #e5f6ee; color: #0d6b45; }
.badge.fail { background: #fde8e6; color: #9f1c1c; }
.badge.not-run { background: #eceae4; color: #3e4942; }
h3 .badge { margin-left: 8px; vertical-align: 2px; }
label { display: block; font-weight: 600; margin: 16px 0 6px; }
.fields label:first-child { margin-top: 0; }
input[type="text"], input[type="password"] {
  width: 100%;
  border: 1px solid #d5d0c6;
  background: #fff;
  border-radius: 10px;
  padding: 10px 12px;
  font: 16px/1.4 system-ui, sans-serif;
  color: #1c2430;
}
input[type="file"] { width: 100%; font: 15px/1.4 system-ui, sans-serif; }
button {
  appearance: none;
  margin-top: 18px;
  background: #0f6e56;
  color: #fff;
  border: 0;
  border-radius: 10px;
  padding: 12px 18px;
  font: 600 16px/1 system-ui, sans-serif;
  cursor: pointer;
}
button:hover { background: #0c5b48; }
button:disabled { opacity: 0.7; cursor: wait; }
.error {
  margin: 0 0 16px;
  padding: 10px 12px;
  border-radius: 8px;
  background: #fdf2f1;
  color: #7f1d1d;
}
.footnote { margin-top: 22px; }
.recording { margin-top: 16px; }
.recording h2 { margin: 0 0 6px; font-size: 1.25rem; }
.recording video {
  display: block;
  width: 100%;
  max-height: 560px;
  background: #1c2430;
  border-radius: 10px;
}
.nowrap { white-space: nowrap; }
.back { margin: 8px 0 0; }
.run-progress { margin: 0 0 16px; }
.run-progress h2 { margin: 0 0 10px; font-size: 1.25rem; }
.run-progress .freeze {
  margin: 0 0 14px;
  padding: 12px 14px;
  border-left: 4px solid #9f1c1c;
  border-radius: 8px;
  background: #fdf2f1;
  color: #7f1d1d;
  overflow-wrap: anywhere;
}
.run-progress .freeze p { margin: 0; }
.run-progress .freeze p + p { margin-top: 6px; }
.current-step {
  margin: 0 0 12px;
  padding: 12px 14px 12px 16px;
  border-radius: 10px;
  background: #e7f5ef;
  box-shadow: inset 4px 0 0 #0f6e56;
  font-weight: 650;
  overflow-wrap: anywhere;
}
.current-step .eyebrow, ol.progress li .eyebrow {
  display: block;
  margin-bottom: 4px;
  font: 600 0.72rem/1.2 system-ui, sans-serif;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #0c5c4a;
}
.progress-label {
  margin: 16px 0 0;
  font: 600 0.75rem/1 system-ui, sans-serif;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #5c665e;
}
ol.checks { list-style: none; margin: 4px 0 0; padding: 0; }
ol.checks li {
  position: relative;
  padding: 6px 0 6px 1.4rem;
  overflow-wrap: anywhere;
  color: #243028;
}
ol.checks li::before {
  content: "";
  position: absolute;
  left: 0;
  top: 12px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #a37b18;
}
ol.progress { margin: 8px 0 0; padding: 0; list-style: none; }
ol.progress li { padding: 8px 0 8px 1.4rem; border-top: 1px solid #e4ebe4; position: relative; overflow-wrap: anywhere; }
ol.progress li:first-child { border-top: 0; }
ol.progress li::before { content: ""; position: absolute; left: 0; top: 14px; width: 8px; height: 8px; border-radius: 50%; background: #8aa396; }
ol.progress li.current::before { background: #0f6e56; }
ol.progress li.current { font-weight: 650; background: #e7f5ef; box-shadow: inset 4px 0 0 #0f6e56; }
:focus-visible { outline: 2px solid #0f6e56; outline-offset: 2px; }
@media (max-width: 720px) {
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  h1 { font-size: 1.7rem; }
  .wrap { padding-left: 16px; padding-right: 16px; }
}
@media (max-width: 560px) {
  .line { grid-template-columns: 2rem minmax(0, 1fr); }
  .line .badge { grid-column: 2; justify-self: start; margin-top: 4px; }
  .reason { margin-left: 0; }
  .section-head { flex-wrap: wrap; }
}
@media print {
  header { background: #fff; color: #000; }
  .lede, .kicker { color: #333; }
  body, .summary { background: #fff; }
  .summary { position: static; }
  .card, article.task { box-shadow: none; }
}
</style>
</head>
<body>
<header>
  <div class="wrap">
    <p class="kicker">AGT503 workshop</p>
    <h1>Workshop Validator</h1>
    <p class="lede">@@LEDE@@</p>
  </div>
</header>
<main class="wrap">
@@BODY@@
</main>
<script>
var form = document.querySelector("form");
if (form) {
  form.addEventListener("submit", function () {
    var button = form.querySelector("button");
    if (!button) return;
    button.disabled = true;
    button.textContent = "Running…";
  });
}
</script>
</body>
</html>
"""


@dataclass(frozen=True)
class StepOutcome:
    status: str
    reason: str = ""
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepView:
    number: str
    title: str
    label: str
    css: str
    reason: str
    step_id: str = ""
    status: str = ""
    chips: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskView:
    lead: str
    label: str
    css: str
    steps: tuple[StepView, ...]


@dataclass(frozen=True)
class SectionView:
    title: str
    subtitle: str
    failed_tasks: int
    tasks: tuple[TaskView, ...]


@dataclass(frozen=True)
class Board:
    run_id: str
    notice: str
    notice_tone: str
    sections: tuple[SectionView, ...]
    passed: int
    failed: int
    not_run: int
    tasks_passed: int
    tasks_failed: int
    tasks_not_run: int
    unmatched: int


def outcomes_from_report_steps(steps: Sequence[ReportStep]) -> dict[str, StepOutcome]:
    """Map the existing report records onto guide step ids."""
    outcomes = {}
    for step in steps:
        outcomes[step.step_id] = StepOutcome(
            status=step.status,
            reason=step.note,
            evidence_ids=tuple(step.evidence_ids),
        )
    return outcomes


def build_board(
    inventory: GuideInventory,
    *,
    outcomes: Mapping[str, StepOutcome] | None = None,
    notice: str,
    run_id: str,
) -> Board:
    """Build Setup, then the remaining sections, in printed order within each group."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("A run id is required")
    if not isinstance(notice, str):
        raise ValueError("A notice is required")
    supplied = dict(outcomes or {})
    known = {step.step_id for step in inventory.steps}
    unmatched = sum(1 for key in supplied if key not in known)
    sections = _sections(inventory.steps, supplied)
    labels = [
        step.label
        for section in sections
        for task in section.tasks
        for step in task.steps
    ]
    task_labels = [task.label for section in sections for task in section.tasks]
    failed = labels.count("FAIL")
    passed = labels.count("PASS")
    not_run = labels.count("NOT RUN")
    if failed:
        tone = "failed"
    elif passed and not not_run:
        tone = "ok"
    else:
        tone = "pending"
    return Board(
        run_id=run_id.strip(),
        notice=notice,
        notice_tone=tone,
        sections=sections,
        passed=passed,
        failed=failed,
        not_run=not_run,
        tasks_passed=task_labels.count("PASS"),
        tasks_failed=task_labels.count("FAIL"),
        tasks_not_run=task_labels.count("NOT RUN"),
        unmatched=unmatched,
    )


_RECORDING_HREF = re.compile(r"^/runs/[0-9a-f]{32}/video$")
_RESULTS_HREF = re.compile(r"^/runs/[0-9a-f]{32}/results\.pdf$")


def recording_section(href: str) -> str:
    """Player for one run's saved session. Anything else renders nothing."""
    if not isinstance(href, str) or not _RECORDING_HREF.fullmatch(href):
        return ""
    safe = escape(href, quote=True)
    return (
        "<section class='recording card'>"
        "<h2>Browser recording</h2>"
        "<p class='helper'>Play the Playwright session from sign-in through the guide walk.</p>"
        f"<video controls preload='metadata' src='{safe}'>"
        f"<a href='{safe}'>Open the recording</a>"
        "</video>"
        f"<p class='back'><a href='{safe}'>Open the recording</a></p>"
        "</section>"
    )


def results_pdf_section(href: str) -> str:
    """Download link for one run's saved results PDF."""
    if not isinstance(href, str) or not _RESULTS_HREF.fullmatch(href):
        return ""
    safe = escape(href, quote=True)
    return (
        "<section class='card'>"
        "<h2>Results PDF</h2>"
        "<p class='helper'>Every step in this run, saved as a PDF.</p>"
        f"<p class='back'><a href='{safe}'>Download the results PDF</a></p>"
        "</section>"
    )


def write_results_pdf(board: Board, path: Path) -> None:
    """Write the pass, fail, and not-run board to a private PDF."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "ResultsTitle", parent=styles["Heading1"], fontName="Times-Bold", fontSize=16, leading=20,
    )
    heading = ParagraphStyle(
        "ResultsHeading", parent=styles["Heading2"], fontName="Times-Bold", fontSize=12, leading=15,
    )
    body = ParagraphStyle(
        "ResultsBody", parent=styles["Normal"], fontName="Times-Roman", fontSize=10, leading=13,
    )
    story = [
        Paragraph("Workshop Validator results", title),
        Spacer(1, 8),
        Paragraph(_pdf_text(board.notice), body),
        Spacer(1, 6),
        Paragraph(
            f"PASS {board.passed}. FAIL {board.failed}. NOT RUN {board.not_run}.",
            body,
        ),
        Spacer(1, 12),
    ]
    for section in board.sections:
        story.append(Paragraph(_pdf_text(section.title), heading))
        if section.subtitle:
            story.append(Paragraph(_pdf_text(section.subtitle), body))
        for task in section.tasks:
            story.append(Paragraph(f"{_pdf_text(task.lead)}: {task.label}", body))
            for step in task.steps:
                line = f"{_pdf_text(step.number)}. {_pdf_text(step.title)}: {step.label}"
                story.append(Paragraph(line, body))
                if step.reason:
                    story.append(Paragraph(_pdf_text(step.reason), body))
            story.append(Spacer(1, 8))
    path.parent.mkdir(mode=0o700, exist_ok=True)
    SimpleDocTemplate(
        str(path), pagesize=letter, title="Workshop Validator results",
    ).build(story)
    path.chmod(0o600)


def _pdf_text(value: str) -> str:
    return escape(value or "").replace("\n", " ")


def render_document(*, title: str, lede: str, body: str) -> str:
    """Full page. Body is HTML this module built; title and lede are escaped.

    Uses the original cream/serif template style for compatibility with existing code.
    """
    page = _DOCUMENT.replace("@@TITLE@@", escape(title))
    page = page.replace("@@LEDE@@", escape(lede))
    return page.replace("@@BODY@@", body)


_STEP_LINE = re.compile(r"^Step \d+ of \d+:\s+\S")
_CHECK_LINE = re.compile(
    r"(?i)(?:\bchecked\b|\bchecking\b|30[- ]seconds?|\b(?:at|after) \d+ seconds?\b)"
)
_FREEZE_LINE = re.compile(
    r"(?i)(?:"
    r"\bfroze\b|\bfrozen\b|\bfreezing\b|"
    r"\bafter 5 minutes\b|\breached 5 minutes\b|"
    r"\bcould not find\b.+\bthe page showed\b|"
    r"\bwould lock the trial org\b|"
    r"\bthe browser is closed\b"
    r")"
)


def is_check_line(line: object) -> bool:
    return isinstance(line, str) and bool(_CHECK_LINE.search(line))


def is_freeze_line(line: object) -> bool:
    return isinstance(line, str) and bool(_FREEZE_LINE.search(line))


def _is_step_line(line: str) -> bool:
    return bool(_STEP_LINE.match(line)) and not is_check_line(line)


def _clean_lines(log: Sequence[str]) -> tuple[str, ...]:
    lines = []
    for line in log:
        if not isinstance(line, str):
            continue
        text = " ".join(line.split())
        if text:
            lines.append(text)
    return tuple(lines)


def _unique_freezes(*groups: Sequence[str]) -> tuple[str, ...]:
    found = []
    for group in groups:
        for line in group:
            if not isinstance(line, str):
                continue
            text = " ".join(line.split())
            if text and is_freeze_line(text) and text not in found:
                found.append(text)
    return tuple(found)


def _board_freeze_reasons(board: Board) -> tuple[str, ...]:
    reasons = []
    for section in board.sections:
        for task in section.tasks:
            for item in task.steps:
                reasons.append(item.reason)
    return _unique_freezes(reasons)


def render_activity(
    log: Sequence[str] = (),
    *,
    live: bool = False,
    footer: str = "",
    freeze_reasons: Sequence[str] = (),
) -> str:
    """Progress card for a run.

    A live or frozen run puts the freeze reason at the top of the card,
    then the current step, then that step's checks, then earlier lines
    from the start of the run.
    """
    lines = _clean_lines(log)
    freezes = _unique_freezes(lines, freeze_reasons)
    if not lines and not freezes:
        return ""
    if not lines:
        reasons = "".join(f"<p>{escape(line)}</p>" for line in freezes)
        body = f"<div class='freeze'>{reasons}</div><h2>Progress</h2>"
    elif live or freezes:
        body = _focused_activity(lines, freezes)
    else:
        rows = "".join(f"<li class='done'>{escape(line)}</li>" for line in lines)
        body = f"<h2>What this run did</h2><ol class='progress'>{rows}</ol>"
    return f"<section class='card run-progress'>{body}{footer}</section>"


def _focused_activity(lines: tuple[str, ...], freezes: tuple[str, ...]) -> str:
    freeze_at = {index for index, line in enumerate(lines) if is_freeze_line(line)}
    step_at = [index for index, line in enumerate(lines) if _is_step_line(line)]
    if step_at:
        current_at = step_at[-1]
    else:
        current_at = max(
            (index for index in range(len(lines)) if index not in freeze_at),
            default=len(lines) - 1,
        )
    check_at = [
        index
        for index in range(current_at + 1, len(lines))
        if index not in freeze_at
    ]
    shown = {current_at, *check_at, *freeze_at}
    rest = [index for index in range(len(lines)) if index not in shown]
    eyebrow = "Current step" if _is_step_line(lines[current_at]) else "Now"
    parts = []
    if freezes:
        reasons = "".join(f"<p>{escape(line)}</p>" for line in freezes)
        parts.append(f"<div class='freeze'>{reasons}</div>")
    parts.append("<h2>Progress</h2>")
    parts.append(
        "<p class='current-step'>"
        f"<span class='eyebrow'>{escape(eyebrow)}</span>"
        f"{escape(lines[current_at])}</p>"
    )
    if check_at:
        rows = "".join(
            f"<li class='check'>{escape(lines[index])}</li>" for index in check_at
        )
        parts.append("<p class='progress-label'>Checks</p>")
        parts.append(f"<ol class='checks'>{rows}</ol>")
    if rest:
        rows = "".join(
            f"<li class='done'>{escape(lines[index])}</li>" for index in rest
        )
        parts.append("<p class='progress-label'>From the start</p>")
        parts.append(f"<ol class='progress'>{rows}</ol>")
    return "".join(parts)


def render_board(
    board: Board,
    *,
    recording_href: str = "",
    results_href: str = "",
    activity: tuple[str, ...] = (),
) -> str:
    tone = board.notice_tone if board.notice_tone in {"ok", "pending", "failed"} else "pending"
    reasons = _board_freeze_reasons(board)
    activity_html = render_activity(activity, freeze_reasons=reasons)
    frozen = bool(reasons) or any(is_freeze_line(line) for line in activity)
    parts = []
    if activity_html and frozen:
        parts.append(activity_html)
    parts.extend((
        "<div class='summary'><div class='card'>",
        f"<p class='notice {tone}'>{escape(board.notice)}</p>",
        _metrics(board),
        f"<p class='task-roll'>{escape(_task_roll(board))}</p>",
        "</div></div>",
        recording_section(recording_href),
        results_pdf_section(results_href),
    ))
    if activity_html and not frozen:
        parts.append(activity_html)
    if board.unmatched:
        parts.append(
            f"<p class='unmatched'>{escape(_unmatched_sentence(board.unmatched))}</p>"
        )
    parts.extend(_render_section(section) for section in board.sections)
    parts.append(
        "<p class='footnote'>A task passes only when every step in it passes. "
        "A step that was not run stays NOT RUN.</p>"
        "<p class='back'><a href='/'>Check another guide</a></p>"
    )
    if board.passed or board.failed:
        lede = "Pass, fail, and not run, in guide order."
    else:
        lede = "The guide was read. Steps that were not executed are NOT RUN."
    return render_document(
        title="Validation results",
        lede=lede,
        body="".join(parts),
    )


def _section_meta(exercise: str | None) -> tuple[str, str, str]:
    printed = (exercise or "").strip()
    if not printed:
        return ("body", "Guide", "")
    if _SETUP.match(printed):
        subtitle = "" if printed.lower() == "setup" else printed
        return ("setup", "Setup", subtitle)
    match = _EXERCISE.match(printed)
    if match:
        return ("body", match.group(1), match.group(2).strip())
    return ("body", printed, "")


def _sections(
    steps: tuple[GuideStep, ...],
    outcomes: Mapping[str, StepOutcome],
) -> tuple[SectionView, ...]:
    raw: list[dict] = []
    for step in steps:
        kind, title, subtitle = _section_meta(step.exercise)
        task_name = (step.task or "").strip() or "Steps"
        if raw and raw[-1]["title"] == title and raw[-1]["kind"] == kind:
            section = raw[-1]
        else:
            section = {"kind": kind, "title": title, "subtitle": subtitle, "tasks": []}
            raw.append(section)
        if section["tasks"] and section["tasks"][-1]["name"] == task_name:
            bucket = section["tasks"][-1]
        else:
            bucket = {"name": task_name, "steps": []}
            section["tasks"].append(bucket)
        bucket["steps"].append(step)
    ordered = [item for item in raw if item["kind"] == "setup"]
    ordered.extend(item for item in raw if item["kind"] != "setup")
    views = []
    for section in ordered:
        tasks = []
        failed_tasks = 0
        for bucket in section["tasks"]:
            step_views = tuple(
                _view_step(step, outcomes.get(step.step_id))
                for step in bucket["steps"]
            )
            label = _rollup([item.label for item in step_views])
            if label == "FAIL":
                failed_tasks += 1
            tasks.append(TaskView(
                lead=f"{section['title']}, {bucket['name']}: ",
                label=label,
                css=_css(label),
                steps=step_views,
            ))
        views.append(SectionView(
            title=section["title"],
            subtitle=section["subtitle"],
            failed_tasks=failed_tasks,
            tasks=tuple(tasks),
        ))
    return tuple(views)


def _view_step(step: GuideStep, outcome: StepOutcome | None) -> StepView:
    outcome = outcome or StepOutcome("NOT_RUN")
    label, css, reason = _display(outcome)
    chips = _detect_chips(outcome)
    return StepView(
        step.number,
        step.text,
        label,
        css,
        reason,
        step_id=step.step_id,
        status=outcome.status if isinstance(outcome.status, str) else "",
        chips=chips,
    )


def _display(outcome: StepOutcome) -> tuple[str, str, str]:
    """Return the visible label, CSS hook, and reason.

    PASS requires evidence. A step that is still running is NOT RUN.
    """
    status = outcome.status if isinstance(outcome.status, str) else ""
    note = outcome.reason.strip() if isinstance(outcome.reason, str) else ""
    evidence = _evidence(outcome)
    if status == "PASS":
        if evidence:
            return ("PASS", "pass", "")
        return (
            "FAIL",
            "fail",
            "A pass was reported without evidence, so it is shown as a failure.",
        )
    if status in _FAIL_REASONS:
        return ("FAIL", "fail", note or _FAIL_REASONS[status])
    if status == "RUNNING":
        return ("NOT RUN", "not-run", "This step was still running, so it was not marked failed.")
    if status == "RETRY_WAIT":
        return (
            "NOT RUN",
            "not-run",
            "This step is waiting on a rate limit and was not finished.",
        )
    if status == "NOT_RUN":
        return ("NOT RUN", "not-run", note)
    return ("FAIL", "fail", "This status is not a pass.")


def _evidence(outcome: StepOutcome) -> tuple[str, ...]:
    raw = outcome.evidence_ids
    if not isinstance(raw, (tuple, list)):
        return ()
    return tuple(item.strip() for item in raw if isinstance(item, str) and item.strip())


def _rollup(labels: list[str]) -> str:
    if any(label == "FAIL" for label in labels):
        return "FAIL"
    if labels and all(label == "PASS" for label in labels):
        return "PASS"
    return "NOT RUN"


def _css(label: str) -> str:
    if label == "PASS":
        return "pass"
    if label == "FAIL":
        return "fail"
    return "not-run"


def _metrics(board: Board) -> str:
    items = (
        ("Passed", board.passed, "pass"),
        ("Failed", board.failed, "fail"),
        ("Not run", board.not_run, "pending"),
        ("Tasks passed", board.tasks_passed, "pass"),
    )
    parts = []
    for label, value, kind in items:
        tone = "quiet" if value == 0 else kind
        parts.append(
            f"<div class='metric {tone}'><strong>{value}</strong>"
            f"<span>{escape(label)}</span></div>"
        )
    return "<div class='metrics'>" + "".join(parts) + "</div>"


def _task_roll(board: Board) -> str:
    return (
        f"Tasks: {board.tasks_passed} passed, "
        f"{board.tasks_failed} failed, {board.tasks_not_run} not run."
    )


def _unmatched_sentence(count: int) -> str:
    if count == 1:
        return "1 recorded result did not match a guide step, so it was left off this board."
    return (
        f"{count} recorded results did not match a guide step, "
        "so they were left off this board."
    )


def _failed_task_label(count: int) -> str:
    if count == 1:
        return "1 task failed"
    return f"{count} tasks failed"


def _render_section(section: SectionView) -> str:
    badge = ""
    if section.failed_tasks:
        badge = (
            f"<span class='badge fail'>{escape(_failed_task_label(section.failed_tasks))}</span>"
        )
    subtitle = (
        f"<p class='printed'>{escape(section.subtitle)}</p>" if section.subtitle else ""
    )
    tasks = "".join(_render_task(task) for task in section.tasks)
    return (
        "<section class='exercise'>"
        "<div class='section-head'>"
        f"<h2>{escape(section.title)}</h2>{badge}</div>"
        f"{subtitle}{tasks}</section>"
    )


def _render_task(task: TaskView) -> str:
    steps = "".join(_render_step(step) for step in task.steps)
    return (
        f"<article class='task {task.css}' data-status='{escape(task.label, quote=True)}'>"
        f"<h3>{escape(task.lead)}"
        f"<span class='badge {task.css}'>{escape(task.label)}</span></h3>"
        f"<ol>{steps}</ol></article>"
    )


def _render_step(step: StepView) -> str:
    reason = f"<p class='reason'>{escape(step.reason)}</p>" if step.reason else ""
    return (
        f"<li class='{step.css}' data-status='{escape(step.label, quote=True)}'>"
        "<div class='line'>"
        f"<span class='num'>{escape(step.number)}.</span>"
        f"<span class='text'>{escape(step.title)}</span>"
        f"<span class='badge {step.css}'>{escape(step.label)}</span>"
        f"</div>{reason}</li>"
    )


def _detect_chips(outcome: StepOutcome) -> tuple[str, ...]:
    """Detect special chip labels from outcome evidence and notes."""
    chips = []
    evidence_text = " ".join(outcome.evidence_ids).lower()
    note = (outcome.reason or "").lower()

    if "already-set" in evidence_text or "already set" in note:
        chips.append("already-set")

    if "reviewed alias" in note or "renamed in ui" in note:
        chips.append("renamed")

    return tuple(chips)


def load_verdicts(run_dir: Path) -> dict:
    """Load verdicts.json if it exists, return {step_id: {verdict, evidence, ...}}."""
    verdicts_path = Path(run_dir) / "verdicts.json"
    if not verdicts_path.is_file():
        return {}
    try:
        data = json.loads(verdicts_path.read_text(encoding="utf-8"))
        verdicts_list = data.get("verdicts", []) if isinstance(data, dict) else []
        return {
            v["step_id"]: v
            for v in verdicts_list
            if isinstance(v, dict) and "step_id" in v
        }
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


def verdict_badge_html(verdict: str) -> str:
    """Return HTML for a verdict badge."""
    if not verdict:
        return ""
    css_map = {
        "REAL": "fail",
        "FALSE_FAILURE": "pass",
        "FALSE_PASS": "fail",
        "CONFIRMED_PASS": "pass",
        "UNCLEAR": "needs-review",
    }
    css = css_map.get(verdict, "needs-review")
    return f"<span class='pill {css}'>{escape(verdict.replace('_', ' '))}</span>"


def render_run_report(
    board: Board,
    *,
    summary: dict,
    guide_name: str = "",
    team_log: list[dict] | None = None,
    history_href: str | None = None,
    run_dir: Path | None = None,
    instance_runs: Sequence[dict] | None = None,
) -> str:
    """Render a full HTML run report.

    instance_runs lists the latest run of this guide on each org instance
    ({instance, label, href, current}); with two or more, the header shows
    a switcher between them.
    """
    outcome = summary.get("outcome", "inconclusive")
    reason = summary.get("reason", "")
    first_failure = summary.get("first_failure")
    org = summary.get("org", 1)
    orgs = summary.get("orgs", 1)

    # Load verdicts if run_dir provided
    verdicts = {}
    mode_str = ""
    verified_count = 0
    if run_dir:
        verdicts = load_verdicts(run_dir)
        verified_count = len(verdicts)
        # Try to load mode from verdicts.json
        verdicts_path = Path(run_dir) / "verdicts.json"
        if verdicts_path.is_file():
            try:
                data = json.loads(verdicts_path.read_text(encoding="utf-8"))
                mode = data.get("mode", "")
                if mode == "quick":
                    mode_str = "Quick mode"
                elif mode == "full":
                    mode_str = "Full AI review"
            except (OSError, json.JSONDecodeError):
                pass

    # Determine overall verdict
    if outcome == "success":
        verdict_class = "verified"
        verdict_text = "VERIFIED"
    elif outcome == "failed":
        verdict_class = "failed"
        verdict_text = "FAILED"
    else:
        verdict_class = "incomplete"
        verdict_text = "INCOMPLETE"

    # Header with guide name, run id, date, org number
    header_parts = [
        "<header>",
        "<div class='wrap'>",
        f"<h1>{escape(guide_name or 'Workshop Guide')}</h1>",
        "<div class='meta'>",
        f"Run ID: {escape(board.run_id)} | ",
        f"Org {org} of {orgs} | ",
        escape(instance_label(summary.get("instance"))),
    ]
    if mode_str and verified_count:
        header_parts.append(f" | {mode_str} | {verified_count} steps verified by AI")
    elif mode_str:
        header_parts.append(f" | {mode_str}")
    header_parts.append("</div>")
    header_parts.append(instance_switcher(instance_runs or ()))
    header_parts.extend([
        "</div>",
        "</header>",
    ])

    # Banner with verdict and reason
    banner_parts = [
        f"<div class='banner {verdict_class}'>",
        f"<strong>{verdict_text}</strong>",
    ]
    if reason:
        banner_parts.append(f"<div class='reason'>{escape(reason)}</div>")
    banner_parts.append("</div>")

    # Stats tiles, counted from the board so chips and blocked steps are included.
    steps = [step for section in board.sections for task in section.tasks for step in task.steps]
    statuses = [step.status or step.label for step in steps]
    pass_count = statuses.count("PASS")
    fail_count = sum(statuses.count(name) for name in ("FAIL", "UI_MISMATCH", "FUNCTIONAL_FAIL"))
    needs_review_count = statuses.count("NEEDS_REVIEW")
    blocked_count = statuses.count("ENV_BLOCKED") + statuses.count("AUTH_BLOCKED")
    not_run_count = len(statuses) - pass_count - fail_count - needs_review_count - blocked_count
    already_set_count = sum("already-set" in step.chips for step in steps)
    renamed_count = sum("renamed" in step.chips for step in steps)

    stats_parts = [
        "<div class='stats'>",
        f"<div class='stat pass'><span class='stat-value'>{pass_count}</span><span class='stat-label'>Passed</span></div>",
        f"<div class='stat fail'><span class='stat-value'>{fail_count}</span><span class='stat-label'>Failed</span></div>",
        f"<div class='stat needs-review'><span class='stat-value'>{needs_review_count}</span><span class='stat-label'>Needs review</span></div>",
        f"<div class='stat'><span class='stat-value'>{blocked_count}</span><span class='stat-label'>Blocked</span></div>",
        f"<div class='stat'><span class='stat-value'>{not_run_count}</span><span class='stat-label'>Not run</span></div>",
    ]

    if already_set_count > 0:
        stats_parts.append(
            f"<div class='stat'><span class='stat-value'>{already_set_count}</span><span class='stat-label'>Already set by org</span></div>"
        )

    if renamed_count > 0:
        stats_parts.append(
            f"<div class='stat'><span class='stat-value'>{renamed_count}</span><span class='stat-label'>Renamed in UI</span></div>"
        )

    stats_parts.extend([
        f"<div class='stat pass'><span class='stat-value'>{board.tasks_passed}</span><span class='stat-label'>Tasks passed</span></div>",
        f"<div class='stat fail'><span class='stat-value'>{board.tasks_failed}</span><span class='stat-label'>Tasks failed</span></div>",
        "</div>",
    ])

    # First failure card
    first_failure_card = ""
    if first_failure:
        step_id = first_failure.get("step_id", "")
        first_failure_card = (
            "<div class='card'>"
            "<h3>First Failure</h3>"
            f"<p>Step <a href='#{escape(step_id, quote=True)}'>{escape(step_id)}</a> "
            f"on page {escape(str(first_failure.get('page', '?')))} "
            f"({escape(first_failure.get('status', 'FAIL'))})</p>"
            "</div>"
        )

    # Sections with progress bars
    sections_html = []
    for section in board.sections:
        # Count statuses in this section
        section_counts = {"PASS": 0, "FAIL": 0, "NEEDS_REVIEW": 0, "NOT_RUN": 0}
        has_failure = False
        has_needs_review = False

        for task in section.tasks:
            for step in task.steps:
                if step.label == "PASS":
                    section_counts["PASS"] += 1
                elif step.label == "FAIL":
                    if step.status == "NEEDS_REVIEW":
                        section_counts["NEEDS_REVIEW"] += 1
                        has_needs_review = True
                    else:
                        section_counts["FAIL"] += 1
                        has_failure = True
                else:
                    section_counts["NOT_RUN"] += 1

        total = sum(section_counts.values())
        open_attr = " open" if (has_failure or has_needs_review) else ""

        # Build progress bar
        progress_segments = []
        for status, count in section_counts.items():
            if count > 0:
                width = (count / total * 100) if total > 0 else 0
                css_class = status.lower().replace("_", "-")
                progress_segments.append(
                    f"<div class='progress-segment {css_class}' style='width: {width}%'>{count}</div>"
                )

        progress_bar = (
            "<div class='progress-bar'>" + "".join(progress_segments) + "</div>"
        )

        # Build section
        sections_html.append(
            f"<div class='section'>"
            f"<h2>{escape(section.title)}</h2>"
        )
        if section.subtitle:
            sections_html.append(f"<p>{escape(section.subtitle)}</p>")

        sections_html.append(progress_bar)

        # Tasks as details
        for task in section.tasks:
            task_status_text = task.label
            task_css = task.css

            # Detect if any step has failure/needs review
            task_has_failure = any(s.label == "FAIL" for s in task.steps)
            task_open = " open" if task_has_failure else ""

            sections_html.append(
                f"<details{task_open}>"
                f"<summary>"
                f"{escape(task.lead)}"
                f"<span class='pill {task_css}'>{escape(task_status_text)}</span>"
                "</summary>"
                "<div class='details-content'>"
                "<ul class='step-list'>"
            )

            for step in task.steps:
                # Determine pill class - show "needs-review" for NEEDS_REVIEW status
                if step.status == "NEEDS_REVIEW":
                    pill_css = "needs-review"
                    pill_text = "Needs review"
                else:
                    pill_css = step.css
                    pill_text = step.label

                step_anchor = f" id='{escape(step.step_id, quote=True)}'" if step.step_id else ""

                # Check for verdict
                verdict_data = verdicts.get(step.step_id)
                verdict_html = ""
                verdict_evidence_html = ""
                if verdict_data:
                    verdict = verdict_data.get("verdict", "")
                    evidence = verdict_data.get("evidence", "")
                    if verdict:
                        verdict_html = f" {verdict_badge_html(verdict)}"
                        # Override status for UNCLEAR and FALSE_PASS - never show as PASS
                        if verdict in ("UNCLEAR", "FALSE_PASS") and pill_text == "PASS":
                            pill_css = "needs-review"
                            pill_text = "Needs review"
                    if evidence:
                        verdict_evidence_html = f"<div class='step-reason'><strong>AI verdict:</strong> {escape(evidence)}</div>"

                sections_html.append(
                    f"<li class='step-item'{step_anchor}>"
                    f"<span class='step-number'>{escape(step.number)}.</span>"
                    f"<span class='step-text'>{escape(step.title)}</span>"
                    f"<span class='pill {pill_css}'>{escape(pill_text)}</span>{verdict_html}"
                )

                if verdict_evidence_html:
                    sections_html.append(verdict_evidence_html)

                if step.reason:
                    sections_html.append(
                        f"<div class='step-reason'>{escape(step.reason)}</div>"
                    )

                if step.chips:
                    chips_html = []
                    for chip in step.chips:
                        if chip == "already-set":
                            chips_html.append("<span class='chip already-set'>Already set by org</span>")
                        elif chip == "renamed":
                            chips_html.append("<span class='chip renamed'>Renamed in UI</span>")
                    if chips_html:
                        sections_html.append("<div class='chips'>" + "".join(chips_html) + "</div>")

                sections_html.append("</li>")

            sections_html.append("</ul></div></details>")

        sections_html.append("</div>")

    # Team activity timeline
    timeline_html = ""
    if team_log:
        timeline_items = []
        for entry in team_log:
            time_str = escape(entry.get("time", ""))
            agent = escape(entry.get("agent", ""))
            action = escape(entry.get("action", ""))
            detail = escape(entry.get("detail", ""))
            link = entry.get("link")

            timeline_items.append(
                "<div class='timeline-item'>"
                f"<div class='timeline-time'>{time_str}</div>"
                f"<div class='timeline-action'>{agent}: {action}</div>"
            )
            if detail:
                timeline_items.append(f"<div class='timeline-detail'>{detail}</div>")
            if link:
                timeline_items.append(f"<div class='timeline-detail'><a href='{escape(link, quote=True)}'>View</a></div>")
            timeline_items.append("</div>")

        if timeline_items:
            timeline_html = (
                "<div class='card'>"
                "<h3>Team Activity</h3>"
                "<div class='timeline'>" + "".join(timeline_items) + "</div>"
                "</div>"
            )

    # Footer
    footer_parts = [
        "<div class='footer'>",
        "<p><strong>Note:</strong> A step shows PASS only when evidence is recorded. ",
        "Steps without evidence are shown as FAIL. ",
        "This report contains private guide content.</p>",
    ]
    if history_href:
        footer_parts.append(f"<p><a href='{escape(history_href, quote=True)}'>View all runs</a></p>")
    footer_parts.append("</div>")

    # Assemble full page
    body = (
        "\n".join(header_parts) +
        "<main class='wrap'>" +
        "\n".join(banner_parts) +
        "\n".join(stats_parts) +
        first_failure_card +
        "\n".join(sections_html) +
        timeline_html +
        "\n".join(footer_parts) +
        "</main>"
    )

    return (
        "<!doctype html>\n"
        "<html lang='en'>\n"
        "<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<meta name='robots' content='noindex, nofollow'>\n"
        f"<title>{escape(guide_name or 'Workshop Validation Report')}</title>\n"
        "<style>\n"
        + theme.get_shared_css() + SWITCHER_CSS +
        "</style>\n"
        "</head>\n"
        "<body>\n"
        + body +
        "\n</body>\n"
        "</html>"
    )


def write_run_report(
    board: Board,
    run_dir: Path,
    *,
    summary: dict,
    guide_name: str = "",
    team_log: list[dict] | None = None,
    instance_runs: Sequence[dict] | None = None,
) -> Path:
    """Write run report HTML and board JSON to run directory."""
    run_dir = Path(run_dir)
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    # Write report HTML
    report_path = run_dir / "report.html"
    html = render_run_report(
        board,
        summary=summary,
        guide_name=guide_name,
        team_log=team_log,
        # runs/<org-key>/<run-id>/report.html -> reports/index.html
        history_href="../../../reports/index.html",
        run_dir=run_dir,
        instance_runs=instance_runs,
    )
    report_path.write_text(html, encoding="utf-8")
    report_path.chmod(0o600)

    # Write board JSON for re-rendering
    board_path = run_dir / "board.json"
    board_dict = asdict(board)
    board_path.write_text(json.dumps(board_dict, indent=2), encoding="utf-8")
    board_path.chmod(0o600)

    # Write summary JSON
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    summary_path.chmod(0o600)

    # Write team log if provided
    if team_log:
        team_log_path = run_dir / "team-log.json"
        team_log_path.write_text(json.dumps(team_log, indent=2), encoding="utf-8")
        team_log_path.chmod(0o600)

    return report_path


def load_board(path: Path) -> Board:
    """Load board from board.json file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    # Reconstruct nested dataclasses
    sections = []
    for section_data in data["sections"]:
        tasks = []
        for task_data in section_data["tasks"]:
            steps = tuple(StepView(**step_data) for step_data in task_data["steps"])
            tasks.append(TaskView(**{**task_data, "steps": steps}))
        sections.append(SectionView(**{**section_data, "tasks": tuple(tasks)}))

    return Board(**{**data, "sections": tuple(sections)})


_FAIL_STATUSES = ("FAIL", "UI_MISMATCH", "FUNCTIONAL_FAIL")

SWITCHER_CSS = """
.instances { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; align-items: center; }
.instances .lead { font-size: 13px; opacity: .8; margin-right: 4px; }
.instances a, .instances button {
  font: inherit; font-size: 13px; padding: 4px 12px; border-radius: 999px; cursor: pointer;
  border: 1px solid currentColor; background: transparent; color: inherit; text-decoration: none; opacity: .75;
}
.instances a.current, .instances button.current { opacity: 1; font-weight: 600; background: rgba(127,127,127,.18); }
.live { padding: 12px 16px; border-radius: 8px; background: rgba(127,127,127,.12); margin: 16px 0; }
.live ol { margin: 8px 0 0; padding-left: 20px; font-size: 14px; }
"""


def _instance_key(entry: dict) -> str:
    return str((entry.get("instance") or {}).get("instance") or UNKNOWN)


def instance_switcher(instance_runs: Sequence[dict]) -> str:
    """Links between the latest report of this guide on each org instance."""
    if len(instance_runs) < 2:
        return ""
    links = []
    for item in instance_runs:
        css = " class='current' aria-current='page'" if item.get("current") else ""
        links.append(
            f"<a{css} href='{escape(str(item.get('href', '')), quote=True)}'>"
            f"{escape(str(item.get('label') or item.get('instance') or UNKNOWN))}</a>"
        )
    return "<nav class='instances' aria-label='Org instance'><span class='lead'>Org instance:</span>" + "".join(links) + "</nav>"


def latest_by_instance(history: Sequence[dict], guide: str) -> list[dict]:
    """Newest history entry of a guide per org instance, ordered by instance."""
    latest: dict[str, dict] = {}
    for entry in history:
        if entry.get("guide") != guide or not entry.get("run_dir"):
            continue
        key = _instance_key(entry)
        if key not in latest or str(entry.get("run_id", "")) > str(latest[key].get("run_id", "")):
            latest[key] = entry
    return [latest[key] for key in sorted(latest)]


def instance_runs_for(history: Sequence[dict], guide: str, run_dir: Path) -> list[dict]:
    """Switcher entries for the report in run_dir, with hrefs relative to it."""
    here = Path(run_dir).resolve()
    items = []
    for entry in latest_by_instance(history, guide):
        target = Path(entry["run_dir"]).resolve()
        items.append({
            "instance": _instance_key(entry),
            "label": instance_label(entry.get("instance")),
            "href": os.path.relpath(target / "report.html", here),
            "current": target == here,
        })
    if not any(item["current"] for item in items):
        return []
    return items


def refresh_instance_reports(private_root: Path, guide: str) -> list[Path]:
    """Re-render the latest report per instance so each links to the others."""
    path = Path(private_root) / "reports" / "history.json"
    if not path.is_file():
        return []
    history = json.loads(path.read_text(encoding="utf-8"))
    latest = latest_by_instance(history, guide)
    if len(latest) < 2:
        return []
    written = []
    for entry in latest:
        run_dir = Path(entry["run_dir"])
        board_path, summary_path = run_dir / "board.json", run_dir / "summary.json"
        if not board_path.is_file() or not summary_path.is_file():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        written.append(write_run_report(
            load_board(board_path), run_dir, summary=summary,
            guide_name=summary.get("guide_name") or guide,
            instance_runs=instance_runs_for(history, guide, run_dir),
        ))
    return written


def history_filter(history: Sequence[dict]) -> str:
    """Tabs that filter the history table by org instance, when runs used several."""
    keys = sorted({_instance_key(entry) for entry in history})
    if len(keys) < 2:
        return ""
    buttons = ["<button type='button' class='current' data-filter=''>All instances</button>"]
    buttons += [
        f"<button type='button' data-filter='{escape(key, quote=True)}'>{escape(key)}</button>"
        for key in keys
    ]
    script = (
        "<script>document.querySelectorAll('.instances button').forEach(function(b){"
        "b.addEventListener('click',function(){var f=b.dataset.filter;"
        "document.querySelectorAll('.instances button').forEach(function(x){x.classList.toggle('current',x===b);});"
        "document.querySelectorAll('#runs tbody tr').forEach(function(r){"
        "r.hidden=!!f&&r.dataset.instance!==f;});});});</script>"
    )
    return ("<nav class='instances' aria-label='Filter by org instance'>"
            "<span class='lead'>Org instance:</span>" + "".join(buttons) + "</nav>\n" + script + "\n")


def render_live_report(
    *,
    guide_name: str,
    run_id: str,
    org: int,
    orgs: int,
    lines: Sequence[str],
    counts: Mapping[str, int] | None = None,
    instance: dict | None = None,
    refresh_s: int = 3,
    ended: str = "",
) -> str:
    """A self-refreshing page shown while a run is still going.

    With ended set, the page stops refreshing and shows why the run ended
    (used when no steps ran, so there is no final report).
    """
    counts = dict(counts or {})
    done = sum(counts.values())
    tiles = "".join(
        f"<div class='stat {css}'><span class='stat-value'>{value}</span><span class='stat-label'>{name}</span></div>"
        for name, css, value in (
            ("Passed", "pass", counts.get("PASS", 0)),
            ("Failed", "fail", sum(counts.get(k, 0) for k in _FAIL_STATUSES)),
            ("Needs review", "needs-review", counts.get("NEEDS_REVIEW", 0)),
            ("Steps checked", "", done),
        )
    )
    recent = "".join(f"<li>{escape(line)}</li>" for line in list(lines)[-15:])
    return (
        "<!doctype html>\n<html lang='en'>\n<head>\n<meta charset='utf-8'>\n"
        + ("" if ended else f"<meta http-equiv='refresh' content='{int(refresh_s)}'>\n") +
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<meta name='robots' content='noindex, nofollow'>\n"
        f"<title>{'' if ended else 'Running: '}{escape(guide_name or 'Workshop guide')}</title>\n"
        "<style>\n" + theme.get_shared_css() + SWITCHER_CSS + "</style>\n</head>\n<body>\n"
        "<header><div class='wrap'>"
        f"<h1>{escape(guide_name or 'Workshop Guide')}</h1>"
        f"<div class='meta'>Run ID: {escape(run_id)} | Org {org} of {orgs} | "
        f"{escape(instance_label(instance))}</div></div></header>\n"
        "<main class='wrap'>"
        + (
            f"<div class='banner incomplete'><strong>INCOMPLETE</strong><div class='reason'>{escape(ended)}</div></div>"
            if ended else
            "<div class='banner incomplete'><strong>RUNNING</strong>"
            f"<div class='reason'>This page refreshes every {int(refresh_s)} seconds and becomes the final report when the run ends.</div></div>"
        ) +
        f"<div class='stats'>{tiles}</div>"
        f"<div class='live'><strong>Latest activity</strong><ol>{recent}</ol></div>"
        "<div class='footer'><p>This page contains private guide content.</p></div>"
        "</main>\n</body>\n</html>"
    )


def write_history_index(private_root: Path) -> Path:
    """Write history index HTML from history.json."""
    private_root = Path(private_root)
    history_path = private_root / "reports" / "history.json"

    if not history_path.exists():
        raise FileNotFoundError(f"History file not found: {history_path}")

    history = json.loads(history_path.read_text(encoding="utf-8"))

    # Sort by run_id (newest first, assuming timestamp-based IDs)
    history.sort(key=lambda x: x.get("run_id", ""), reverse=True)

    # Build table rows
    rows = []
    for entry in history:
        run_id = escape(entry.get("run_id", ""))
        guide = escape(entry.get("guide", ""))
        org = entry.get("org", 1)
        orgs = entry.get("orgs", 1)
        outcome = entry.get("outcome", "inconclusive")
        reason = escape(entry.get("reason", ""))
        counts = entry.get("counts", {})
        run_dir = entry.get("run_dir", "")
        instance = _instance_key(entry)
        fails = sum(counts.get(name, 0) for name in _FAIL_STATUSES)

        # Outcome pill
        if outcome == "success":
            outcome_class = "pass"
            outcome_text = "VERIFIED"
        elif outcome == "failed":
            outcome_class = "fail"
            outcome_text = "FAILED"
        else:
            outcome_class = "not-run"
            outcome_text = "INCOMPLETE"

        # Report link (file:// absolute path)
        report_href = ""
        if run_dir:
            report_path = Path(run_dir) / "report.html"
            if report_path.exists():
                report_href = f"file://{report_path.resolve()}"

        rows.append(
            f"<tr data-instance='{escape(instance, quote=True)}'>"
            f"<td>{run_id}</td>"
            f"<td>{guide}</td>"
            f"<td>Org {org} of {orgs}</td>"
            f"<td>{escape(instance_label(entry.get('instance')))}</td>"
            f"<td><span class='pill {outcome_class}'>{outcome_text}</span></td>"
            f"<td>{reason}</td>"
            f"<td>Pass: {counts.get('PASS', 0)} | Fail: {fails} | "
            f"Review: {counts.get('NEEDS_REVIEW', 0)} | Not run: {counts.get('NOT_RUN', 0)}</td>"
            f"<td><a href='{escape(report_href, quote=True)}'>View</a></td>"
            "</tr>"
        )

    html = (
        "<!doctype html>\n"
        "<html lang='en'>\n"
        "<head>\n"
        "<meta charset='utf-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>\n"
        "<meta name='robots' content='noindex, nofollow'>\n"
        "<title>Run History</title>\n"
        "<style>\n"
        + theme.get_shared_css() + SWITCHER_CSS +
        "</style>\n"
        "</head>\n"
        "<body>\n"
        "<header><div class='wrap'><h1>Run History</h1></div></header>\n"
        "<main class='wrap'>\n"
        + history_filter(history) +
        "<table id='runs'>\n"
        "<thead>\n"
        "<tr><th>Run ID</th><th>Guide</th><th>Org</th><th>Instance</th><th>Outcome</th><th>Reason</th><th>Counts</th><th>Report</th></tr>\n"
        "</thead>\n"
        "<tbody>\n"
        + "\n".join(rows) +
        "</tbody>\n"
        "</table>\n"
        "<div class='footer'><p>This index contains private guide content.</p></div>\n"
        "</main>\n"
        "</body>\n"
        "</html>"
    )

    index_path = private_root / "reports" / "index.html"
    index_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    index_path.write_text(html, encoding="utf-8")
    index_path.chmod(0o600)

    return index_path


def main(argv: list[str] | None = None) -> int:
    """CLI. Accepts `render <dir>` / `history <root>` and the documented
    `--render <dir>` / `--history <root>` forms."""
    import argparse
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    argv = [{"--render": "render", "--history": "history"}.get(arg, arg) for arg in argv]
    parser = argparse.ArgumentParser(description="Workshop validator step board CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    render_parser = subparsers.add_parser("render", help="Re-render report from run directory")
    render_parser.add_argument("run_dir", type=Path, help="Run directory with board.json")
    history_parser = subparsers.add_parser("history", help="Rebuild history index")
    history_parser.add_argument("private_root", type=Path, help="Private root directory")
    args = parser.parse_args(argv)

    if args.command == "history":
        print(f"History index written to {write_history_index(args.private_root.expanduser())}")
        return 0
    run_dir = args.run_dir.expanduser()
    board_path = run_dir / "board.json"
    summary_path = run_dir / "summary.json"
    for needed in (board_path, summary_path):
        if not needed.exists():
            print(f"Error: {needed.name} not found in {run_dir}", file=sys.stderr)
            return 1
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    team_log = None
    team_log_path = run_dir / "team-log.json"
    if team_log_path.exists():
        team_log = json.loads(team_log_path.read_text(encoding="utf-8"))
    guide = summary.get("guide_name", "")
    instance_runs = None
    # runs/<org-key>/<run-id> -> private root
    history_path = run_dir.resolve().parents[2] / "reports" / "history.json" if len(run_dir.resolve().parents) > 2 else None
    if guide and history_path is not None and history_path.is_file():
        history = json.loads(history_path.read_text(encoding="utf-8"))
        instance_runs = instance_runs_for(history, guide, run_dir)
    report_path = write_run_report(
        load_board(board_path), run_dir, summary=summary, guide_name=guide,
        team_log=team_log, instance_runs=instance_runs,
    )
    print(f"Report written to {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
