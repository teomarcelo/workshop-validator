"""Private producer dashboard: `~/.workshop-validator/team/dashboard.html`.

Fed by now.json, open pull requests, the newest register rows and the chain
status. Rendered at every state change (never on a timer). Every time shown
is 12-hour local with AM/PM; raw ISO timestamps are never written.
"""

from datetime import datetime, timezone
from html import escape
from pathlib import Path
import argparse
import json
import subprocess
import webbrowser

from . import team_kit
from .team_gates import ROW_PATTERN
from .team_state import _write_private
from .theme import COLORS_DARK, COLORS_LIGHT, FONT_SYSTEM

BANNERS = {
    "WORKING": ("Working", "pass"), "IDLE": ("Idle", "needs_review"),
    "STALLED": ("Stalled", "fail"), "WAITING": ("Waiting on you", "needs_review"),
    "DONE": ("Done", "pass"),
}
STATE_CHIP = {"running": "pass", "next": "link", "waiting": "needs_review", "done": "not_run"}


def _when(value) -> str:
    try:
        return team_kit.clock12(datetime.fromisoformat(value)) if value else ""
    except ValueError:
        return ""


def newest_rows(register: Path, limit: int = 8) -> list[list[str]]:
    if not register.is_file():
        return []
    rows = [line for line in register.read_text(encoding="utf-8").splitlines() if ROW_PATTERN.match(line)]
    return [[cell.strip() for cell in row.strip().strip("|").split("|", 3)] for row in rows[-limit:]][::-1]


def open_prs(repo: Path) -> list[dict]:
    try:
        out = subprocess.run(["gh", "pr", "list", "--state", "open", "--json", "number,title,headRefName"],
                             cwd=repo, capture_output=True, text=True, timeout=30).stdout
        return json.loads(out or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []


def render(plan: dict, state: str, reason: str, prs: list[dict], rows: list[list[str]], now: datetime) -> str:
    label, tone = BANNERS.get(state, (state, "not_run"))
    css_vars = lambda colors: ";".join(f"--{k}:{v}" for k, v in colors.items())
    steps = "".join(
        f"<tr><td>{escape(str(s.get('id', '')))}</td><td>{escape(str(s.get('title', '')))}</td>"
        f"<td><span class='chip' style='color:var(--{STATE_CHIP.get(s.get('state'), 'text')})'>"
        f"{escape(str(s.get('state', '')))}</span></td>"
        f"<td>{escape(str(s.get('blocker') or ''))}</td><td>{escape(_when(s.get('changed')))}</td></tr>"
        for s in plan.get("steps", []))
    pr_rows = "".join(f"<li>#{p.get('number')} {escape(str(p.get('title', '')))}</li>" for p in prs) or "<li>None open</li>"
    reg = "".join(f"<tr><td>{escape(r[0])}</td><td>{escape(r[2] if len(r) > 2 else '')}</td>"
                  f"<td>{escape((r[3] if len(r) > 3 else '')[:220])}</td></tr>" for r in rows)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Workshop Validator team</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>:root{{{css_vars(COLORS_LIGHT)}}}@media (prefers-color-scheme: dark){{:root{{{css_vars(COLORS_DARK)}}}}}
body{{font-family:{FONT_SYSTEM};background:var(--bg);color:var(--text);margin:0;padding:24px;line-height:1.45}}
main{{max-width:1100px;margin:0 auto}}section{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin:16px 0}}
.banner{{padding:14px 20px;border-radius:10px;font-weight:600;color:var(--{tone});background:var(--{tone}_bg, var(--surface_alt))}}
table{{width:100%;border-collapse:collapse}}td,th{{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}}
th{{color:var(--text_secondary);font-weight:600}}.chip{{font-weight:600}}.muted{{color:var(--text_muted)}}</style></head>
<body><main><h1>Workshop Validator team</h1>
<div class="banner" role="status">{escape(label)}: {escape(reason)}</div>
<p class="muted">Updated {escape(team_kit.clock12(now))}</p>
<section><h2>Plan</h2><table><thead><tr><th>Step</th><th>Task</th><th>State</th><th>Blocker</th><th>Changed</th></tr></thead>
<tbody>{steps}</tbody></table></section>
<section><h2>Open pull requests</h2><ul>{pr_rows}</ul></section>
<section><h2>Newest decisions</h2><table><thead><tr><th>Row</th><th>Status</th><th>Ruling</th></tr></thead><tbody>{reg}</tbody></table></section>
</main></body></html>
"""


def status_line(run_dir: Path | None = None) -> str:
    """Generate one-line status from newest run or given run_dir."""
    if run_dir is None:
        # Find newest run
        home = Path.home() / ".workshop-validator"
        runs = home / "runs"
        if not runs.exists():
            return "No runs found"

        # Find all run directories
        run_dirs = []
        for org_dir in runs.iterdir():
            if org_dir.is_dir():
                for run in org_dir.iterdir():
                    if run.is_dir() and (run / "summary.json").exists():
                        run_dirs.append(run)

        if not run_dirs:
            return "No runs found"

        # Get newest by modification time
        run_dir = max(run_dirs, key=lambda p: p.stat().st_mtime)

    # Read summary.json and board.json
    summary_file = run_dir / "summary.json"
    board_file = run_dir / "board.json"

    if not summary_file.exists():
        return "No summary.json found"

    try:
        summary = json.loads(summary_file.read_text())
    except (OSError, ValueError):
        return "Cannot read summary.json"

    # Read board.json if it exists
    board = {}
    if board_file.exists():
        try:
            board = json.loads(board_file.read_text())
        except (OSError, ValueError):
            pass

    # Extract guide name
    guide_name = summary.get("guide", "Unknown guide")

    # Get furthest step (last step with status != NOT_RUN)
    steps = board.get("steps", [])
    furthest_step = "None"
    for step in reversed(steps):
        if step.get("status") != "NOT_RUN":
            furthest_step = step.get("id", "Unknown")
            break

    # Count statuses
    counts = {"PASS": 0, "NEEDS_REVIEW": 0, "FAIL": 0, "NOT_RUN": 0}
    for step in steps:
        status = step.get("status", "NOT_RUN")
        if status in ("UI_MISMATCH", "FUNCTIONAL_FAIL", "ENV_BLOCKED", "AUTH_BLOCKED"):
            counts["FAIL"] += 1
        elif status in counts:
            counts[status] += 1
        else:
            counts["FAIL"] += 1

    # Get top 3 failures
    failures = []
    for step in steps:
        status = step.get("status", "NOT_RUN")
        if status in ("UI_MISMATCH", "FUNCTIONAL_FAIL", "ENV_BLOCKED", "AUTH_BLOCKED", "NEEDS_REVIEW"):
            step_id = step.get("id", "Unknown")
            reason = (step.get("note", "") or "")[:80]
            failures.append(f"{step_id}: {status}: {reason}")
            if len(failures) >= 3:
                break

    # Build status line
    parts = [
        guide_name,
        furthest_step,
        f"PASS {counts['PASS']} / NEEDS_REVIEW {counts['NEEDS_REVIEW']} / FAIL {counts['FAIL']} / NOT_RUN {counts['NOT_RUN']}"
    ]

    if failures:
        parts.append(" | ".join(failures))

    return "; ".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the private team dashboard.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--private-root", default=None)
    parser.add_argument("--open", action="store_true", help="Open it in the default browser")
    parser.add_argument("--status-line", action="store_true", help="Print one-line status")
    parser.add_argument("--run-dir", help="Run directory for status-line")
    args = parser.parse_args(argv)

    if args.status_line:
        run_dir = Path(args.run_dir) if args.run_dir else None
        print(status_line(run_dir))
        return 0

    repo = Path(args.repo).resolve()
    team = team_kit.team_root(Path(args.private_root).expanduser() if args.private_root else None)
    now = datetime.now(timezone.utc)
    state, reason = team_kit.status(repo, team, now)
    html = render(team_kit.load_plan(team), state, reason, open_prs(repo), newest_rows(team / "DECISIONS.md"), now)
    target = team / "dashboard.html"
    _write_private(target, html)
    if args.open:
        webbrowser.open(target.as_uri())
    print(f"dashboard: {state} ({len(team_kit.load_plan(team).get('steps', []))} steps) -> team/dashboard.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
