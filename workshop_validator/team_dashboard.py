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


NO_RUNS = "No runs found"
FAIL_STATUSES = ("UI_MISMATCH", "FUNCTIONAL_FAIL")
BLOCKED_STATUSES = ("ENV_BLOCKED", "AUTH_BLOCKED")


def _clean(value) -> str:
    """One-line text: newlines and tabs become spaces, other control and format characters go."""
    import unicodedata
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    text = "".join(" " if c in "\r\n\t\v\f\x85\u2028\u2029" else c for c in text)
    return " ".join("".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf")).split())


def _count(counts: dict, *names: str) -> int:
    total = 0
    for name in names:
        value = counts.get(name, 0)
        total += value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
    return total


def _read_json_dict(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def _newest_run(private_root: Path | None) -> Path | None:
    runs = (Path(private_root) if private_root else Path.home() / ".workshop-validator") / "runs"
    found = []
    try:
        for org_dir in runs.iterdir():
            if not org_dir.is_dir() or org_dir.is_symlink():
                continue
            for run in org_dir.iterdir():
                summary = run / "summary.json"
                if run.is_dir() and summary.is_file():
                    found.append((summary.stat().st_mtime, run.name, run))
    except OSError:
        return None
    return max(found)[2] if found else None


def _furthest_step(board: dict | None) -> str:
    """step_id of the last step in board order (sections > tasks > steps) not NOT_RUN, or ""."""
    steps = []
    sections = board.get("sections") if board else None
    for section in sections if isinstance(sections, list) else []:
        tasks = section.get("tasks") if isinstance(section, dict) else None
        for task in tasks if isinstance(tasks, list) else []:
            items = task.get("steps") if isinstance(task, dict) else None
            steps.extend(s for s in (items if isinstance(items, list) else []) if isinstance(s, dict))
    for step in reversed(steps):
        if step.get("status") != "NOT_RUN" and _clean(step.get("step_id")):
            return _clean(step.get("step_id"))
    return ""


def status_line(run_dir: Path | None = None, private_root: Path | None = None) -> str:
    """`<guide>; <furthest step>; PASS n / NEEDS_REVIEW n / FAIL n / BLOCKED n / NOT_RUN n; <failures>`.

    Reads the given run folder, else the newest run under `<private_root>/runs`.
    Returns NO_RUNS when there is no readable summary.json. Never raises.
    """
    if run_dir is None:
        run_dir = _newest_run(private_root)
    summary = _read_json_dict(Path(run_dir) / "summary.json") if run_dir else None
    if summary is None:
        return NO_RUNS
    furthest = _furthest_step(_read_json_dict(Path(run_dir) / "board.json"))
    furthest = furthest or _clean(summary.get("stop_step")) or "none"
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    tally = (f"PASS {_count(counts, 'PASS')} / NEEDS_REVIEW {_count(counts, 'NEEDS_REVIEW')}"
             f" / FAIL {_count(counts, *FAIL_STATUSES)} / BLOCKED {_count(counts, *BLOCKED_STATUSES)}"
             f" / NOT_RUN {_count(counts, 'NOT_RUN')}")
    parts = [_clean(summary.get("guide_name")) or "unknown guide", furthest, tally]
    failures = summary.get("failures") if isinstance(summary.get("failures"), list) else []
    shown = [f"{_clean(f.get('step_id'))}: {_clean(f.get('status'))}: {_clean(f.get('note'))[:80]}"
             for f in failures if isinstance(f, dict)][:3]
    if shown:
        parts.append(" | ".join(shown))
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
        run_dir = Path(args.run_dir).expanduser() if args.run_dir else None
        private_root = Path(args.private_root).expanduser() if args.private_root else None
        line = status_line(run_dir, private_root)
        print(line)
        return 1 if line == NO_RUNS else 0

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
