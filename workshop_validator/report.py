"""Private, escaped HTML evidence report; deliberately never claims false PASS."""

from collections import Counter
from dataclasses import dataclass
from html import escape
from pathlib import Path

VALID = frozenset({"PASS", "UI_MISMATCH", "FUNCTIONAL_FAIL", "ENV_BLOCKED",
                   "AUTH_BLOCKED", "NEEDS_REVIEW", "NOT_RUN", "RUNNING", "RETRY_WAIT"})


@dataclass(frozen=True)
class ReportStep:
    step_id: str
    page: int
    title: str
    status: str
    evidence_ids: tuple[str, ...] = ()
    note: str = ""
    required: bool = True


def render_report(run_id: str, steps: list[ReportStep], *, inventory_verified: bool = False) -> str:
    """Create HTML for private storage, escaping every potentially private field.

    Full verification requires a complete, independently reviewed source
    inventory and PASS for all required steps. No numeric confidence is invented.
    """
    if not run_id.strip() or not steps:
        raise ValueError("Run ID and step inventory are required")
    if len({s.step_id for s in steps}) != len(steps):
        raise ValueError("Duplicate step identifiers")
    if any(s.status not in VALID or s.page < 1 for s in steps):
        raise ValueError("Invalid step status or page")
    if any(s.status == "PASS" and not s.evidence_ids for s in steps):
        raise ValueError("PASS requires evidence IDs")
    verified = bool(inventory_verified) and all(
        s.status == "PASS" for s in steps if s.required
    ) and any(s.required for s in steps)
    counts = Counter(s.status for s in steps)
    result = "VERIFIED" if verified else "INCOMPLETE"
    total = len(steps)
    summary = f"{counts['PASS']}/{total} candidate steps passed; inventory {'reconciled' if inventory_verified else 'not reconciled'}"
    rows = []
    for s in steps:
        evidence = ", ".join(escape(e) for e in s.evidence_ids) or "No evidence"
        rows.append(
            "<tr>" + "".join(
                f"<td>{escape(v)}</td>" for v in (
                    s.step_id, str(s.page), s.title, s.status, s.note
                )
            ) + f"<td>{evidence}</td></tr>"
        )
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Workshop validation evidence</title>"
            "<style>body{font:15px system-ui;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#162331;}"
            "table{border-collapse:collapse;width:100%;}th,td{padding:.7rem;border-bottom:1px solid #ddd;text-align:left;}"
            "th{background:#edf3f9;}td{vertical-align:top;word-break:break-word}.muted{color:#5b6673}</style>"
            "</head><body><h1>Workshop validator report</h1>"
            f"<p>Run: {escape(run_id)} | Result: <strong>{result}</strong></p>"
            f"<p class='muted'>{escape(summary)}</p>"
            "<p>Step status is evidence-dependent. Missing, blocked and unverified steps are not passed.</p>"
            "<table><thead><tr><th>Step ID</th><th>Page</th><th>Instruction</th>"
            "<th>Status</th><th>Finding</th><th>Evidence IDs</th></tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></body></html>")


def write_private_report(path: str | Path, html: str) -> None:
    """Write a new report with restrictive permissions; never overwrite."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open('x', encoding='utf-8') as f:
        f.write(html)
    dest.chmod(0o600)
