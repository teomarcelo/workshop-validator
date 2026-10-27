"""Quick, escaped HTML instructor report; never claims an unsupported PASS."""

from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path
import re


VALID = frozenset({"PASS", "UI_MISMATCH", "FUNCTIONAL_FAIL", "ENV_BLOCKED",
                   "AUTH_BLOCKED", "NEEDS_REVIEW", "NOT_RUN", "RUNNING", "RETRY_WAIT"})
SCREENSHOT = re.compile(r"^screenshots/[A-Za-z0-9_.-]+\.png$")
BEHAVIORAL = frozenset({
    "TEST_ORACLE_INSENSITIVE", "INSUFFICIENT_PRECONDITION_EVIDENCE",
    "EXECUTED_PATH_INCONCLUSIVE", "ACTION_REGRESSED_EXPECTED_STATE",
    "ACTION_HELPED_ON_TESTED_BASELINE", "ACTION_DID_NOT_FIX_RESULT",
    "ACTION_NECESSITY_UNTESTED", "PRECONDITION_ALREADY_MET_UNPROVEN",
    "SKIP_PATH_NOT_TESTED", "SKIP_PATH_INCONCLUSIVE", "SKIP_PATH_FAILED",
    "NEEDS_NEGATIVE_CONTROL", "REDUNDANCY_CANDIDATE",
})


@dataclass(frozen=True)
class ReportStep:
    step_id: str
    page: int
    title: str
    status: str
    evidence_ids: tuple[str, ...] = ()
    note: str = ""
    required: bool = True
    screenshot_href: str | None = None
    behavioral_classification: str | None = None
    behavioral_evidence_ids: tuple[str, ...] = ()
    t_start: float | None = None
    t_end: float | None = None
    suspect: tuple[str, ...] = ()


def render_report(run_id: str, steps: list[ReportStep], *, inventory_verified: bool = False) -> str:
    """Private HTML with accurate counts, evidence IDs and safe relative links.

    A run is VERIFIED only when a source audit is complete, at least one
    required step exists and every required step has independently proven PASS.
    Never mistake textual resemblance, screenshots or model confidence for
    assertion-grade evidence.
    """
    if not isinstance(run_id, str) or not run_id.strip() or not steps:
        raise ValueError("Run ID and step inventory are required")
    if len({s.step_id for s in steps}) != len(steps):
        raise ValueError("Duplicate step identifiers")
    if any(s.status not in VALID or type(s.page) is not int or s.page < 1 for s in steps):
        raise ValueError("Invalid step status or page")
    if any(s.status == "PASS" and not s.evidence_ids for s in steps):
        raise ValueError("PASS requires independently verified evidence IDs")
    if any(s.screenshot_href is not None and
           (not isinstance(s.screenshot_href, str) or not SCREENSHOT.fullmatch(s.screenshot_href))
           for s in steps):
        raise ValueError("Invalid private screenshot reference")

    if any(s.behavioral_classification is not None and
           (s.behavioral_classification not in BEHAVIORAL or
            not s.behavioral_evidence_ids) for s in steps):
        raise ValueError("Behavioral diagnostics require known classification and evidence IDs")
    if any(s.behavioral_classification is None and s.behavioral_evidence_ids for s in steps):
        raise ValueError("Behavioral evidence cannot be presented without a classification")

    counts = Counter(s.status for s in steps)
    required = [s for s in steps if s.required]
    verified = bool(inventory_verified) and bool(required) and all(s.status == "PASS" for s in required)
    result = "VERIFIED" if verified else "INCOMPLETE"
    n = len(steps)
    blocked = sum(counts[name] for name in ("ENV_BLOCKED", "AUTH_BLOCKED", "RETRY_WAIT"))
    failed = sum(counts[name] for name in ("UI_MISMATCH", "FUNCTIONAL_FAIL", "NEEDS_REVIEW"))
    pending = counts["NOT_RUN"] + counts["RUNNING"]
    cards = (("Steps", n), ("Passed", counts["PASS"]), ("Blocked", blocked),
             ("Need review", failed), ("Not run", pending))
    metric_html = "".join(
        f"<div class='metric'><strong>{value}</strong><span>{escape(label)}</span></div>"
        for label, value in cards
    )
    rows = []
    for s in steps:
        ev = ", ".join(escape(e) for e in s.evidence_ids) or "<span class='muted'>No evidence</span>"
        screenshot = (f"<a href='{escape(s.screenshot_href, quote=True)}'>View screenshot</a>"
                      if s.screenshot_href else "<span class='muted'>Not captured</span>")
        behavior = (
            f"<strong>{escape(s.behavioral_classification)}</strong><br>"
            "<span class='muted'>Diagnostic only; no guide edit approved.</span><br>"
            + ", ".join(escape(e) for e in s.behavioral_evidence_ids)
            if s.behavioral_classification else "<span class='muted'>Not probed</span>"
        )
        cells = [
            escape(s.step_id), str(s.page), escape(s.title),
            f"<span class='status'>{escape(s.status)}</span>",
            escape(s.note), behavior, ev, screenshot,
        ]
        rows.append("<tr>" + "".join(f"<td>{value}</td>" for value in cells) + "</tr>")

    safe_run = escape(run_id)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Workshop Validator - Instructor Summary</title>"
        "<style>"
        "*{box-sizing:border-box}body{font:15px system-ui,-apple-system,Segoe UI,sans-serif;margin:0;"
        "color:#1a3040;background:#f4f7fa}header{background:#086676;color:white;padding:32px max(5vw,24px)}"
        "h1{margin:0 0 6px;font-size:27px}main{max-width:1220px;margin:30px auto;padding:0 24px}"
        ".metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}"
        ".metric,.panel{background:white;border:1px solid #dce6ed;border-radius:10px;padding:20px;"
        "box-shadow:0 1px 6px #132f4810}.metric strong{display:block;font-size:30px;color:#056876}"
        ".metric span,.muted{color:#617581}.warning{background:#fff6e6;border-left:4px solid #a87510;"
        "padding:18px;margin:22px 0;border-radius:6px}table{border-collapse:collapse;width:100%;}"
        "th,td{padding:13px;border-bottom:1px solid #e0e7ed;text-align:left;vertical-align:top;"
        "overflow-wrap:anywhere}th{background:#eef4f8;white-space:nowrap}"
        ".panel{overflow-x:auto;margin-top:20px}.status{font-weight:700;font-size:12px}"
        "a{color:#075d85}.meta{color:#e0edf1;margin-top:6px}</style></head><body>"
        "<header><h1>Workshop validation summary</h1>"
        f"<p class='meta'>Run: {safe_run}</p></header>"
        "<main><p class='meta' style='color:#45616e'>"
        f"Result: <strong>{result}</strong> | "
        f"Source inventory: {'RECONCILED' if inventory_verified else 'NOT RECONCILED'}"
        "</p><section class='metrics'>" + metric_html + "</section>"
        "<div class='warning'><strong>Evidence policy:</strong> A step is PASS only after all "
        "required assertions match independent recorded observations. A step PASS does not prove "
        "its manual action was necessary or that a guide edit is safe. Blocked, ambiguous "
        "and unreviewed steps remain unverified. This report and its links are PRIVATE.</div>"
        "<section class='panel'><h2>Step-by-step findings</h2>"
        "<table><thead><tr><th>Step ID</th><th>PDF page</th><th>Instruction</th>"
        "<th>Status</th><th>Finding</th><th>Behavioral probe</th><th>Evidence IDs</th><th>Screenshot</th>"
        "</tr></thead><tbody>" + "".join(rows) +
        "</tbody></table></section></main></body></html>"
    )


def write_private_report(path: str | Path, html: str) -> None:
    """Write a new report with owner-only permissions and no overwrite."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("x", encoding="utf-8") as f:
        f.write(html)
    dest.chmod(0o600)
