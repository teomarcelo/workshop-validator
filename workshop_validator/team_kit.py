"""Producer-team memory: plan file, run ledger, chain status and producer-live.

Everything here is private and lives in `~/.workshop-validator/team/` (0600).
Nothing in this module reads guide text, credentials or browser state.

Chain states (one line from `status`):
- WORKING: a step is running and something moved in the last 30 minutes.
- STALLED: a step is running but nothing moved for 30 minutes, or disk is under 15 GB.
- IDLE: a step is ready (no blocker, prereqs met) and nothing is running.
- WAITING: only owner- or outside-blocked steps are left.
- DONE: every step is done.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

from .team_state import _write_private, is_open, load as load_job

STATES = ("next", "running", "waiting", "done")
STALL_MINUTES = 30
MIN_FREE_GB = 15
NUDGE_GRACE_MINUTES = 30
PRODUCER_ALIVE_MINUTES = 120


def team_root(private_root: Path | None = None) -> Path:
    root = Path(private_root) if private_root else Path.home() / ".workshop-validator"
    return root / "team"


def now_path(team: Path) -> Path:
    return team / "progress" / "now.json"


def ledger_path(team: Path) -> Path:
    return team / "runs" / "runs.jsonl"


def producer_live_path(team: Path) -> Path:
    return team / "producer-live"


def _utc(now=None) -> datetime:
    return now or datetime.now(timezone.utc)


def clock12(moment: datetime) -> str:
    """Local 12-hour time with AM/PM, the only format shown to the owner."""
    local = moment.astimezone()
    return local.strftime("%b %-d, %-I:%M %p")


# ---- plan file -------------------------------------------------------------

def load_plan(team: Path) -> dict:
    path = now_path(team)
    if not path.is_file():
        return {"steps": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_plan(team: Path, plan: dict, now=None) -> None:
    for step in plan.get("steps", []):
        if step.get("state") not in STATES:
            raise ValueError(f"step {step.get('id')}: state must be one of {STATES}")
    plan["updated"] = _utc(now).isoformat()
    _write_private(now_path(team), json.dumps(plan, indent=2) + "\n")


def set_step(team: Path, step_id: str, state: str, now=None, **fields) -> dict:
    plan = load_plan(team)
    for step in plan["steps"]:
        if step.get("id") == step_id:
            step["state"] = state
            step.update({k: v for k, v in fields.items() if v is not None})
            step["changed"] = _utc(now).isoformat()
            save_plan(team, plan, now)
            return step
    raise KeyError(step_id)


def unmet_prereqs(step: dict, plan: dict, base: Path | None = None) -> list[str]:
    """Prereqs are `step:<id>` (that step is done) or `file:<path>` (exists)."""
    done = {s.get("id") for s in plan.get("steps", []) if s.get("state") == "done"}
    missing = []
    for item in step.get("prereqs", []):
        kind, _, value = str(item).partition(":")
        if kind == "step" and value not in done:
            missing.append(item)
        elif kind == "file":
            path = Path(value).expanduser()
            if not path.is_absolute() and base is not None:
                path = base / path
            if not path.exists():
                missing.append(item)
        elif kind not in {"step", "file"}:
            missing.append(item)
    return missing


def ready_steps(plan: dict, base: Path | None = None) -> list[dict]:
    return [s for s in plan.get("steps", []) if s.get("state") == "next"
            and not s.get("blocker") and not unmet_prereqs(s, plan, base)]


# ---- ledger ----------------------------------------------------------------

LEDGER_FIELDS = ("role", "task", "model", "tokens", "tool_calls", "minutes",
                 "verdict", "findings", "escapes", "gate")


def append_ledger(team: Path, row: dict, now=None) -> dict:
    unknown = set(row) - set(LEDGER_FIELDS)
    if unknown:
        raise ValueError(f"unknown ledger fields: {sorted(unknown)}")
    if not row.get("role") or not row.get("task"):
        raise ValueError("role and task are required")
    entry = {"time": _utc(now).isoformat(), **row}
    path = ledger_path(team)
    old = path.read_text(encoding="utf-8") if path.is_file() else ""
    _write_private(path, old + json.dumps(entry, sort_keys=True) + "\n")
    return entry


# ---- producer-live ---------------------------------------------------------

def claim_producer(team: Path, session_id: str = "pending", now=None) -> dict:
    payload = {"session_id": session_id, "claimed": _utc(now).isoformat()}
    _write_private(producer_live_path(team), json.dumps(payload) + "\n")
    return payload


def load_producer(team: Path) -> dict | None:
    path = producer_live_path(team)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def release_producer(team: Path) -> None:
    producer_live_path(team).unlink(missing_ok=True)


def bind_producer(team: Path, session_id: str) -> bool:
    """A pending claim adopts the first session id a hook sees."""
    live = load_producer(team)
    if live and live.get("session_id") == "pending" and session_id:
        live["session_id"] = session_id
        _write_private(producer_live_path(team), json.dumps(live) + "\n")
        return True
    return False


# ---- activity and status ---------------------------------------------------

def transcripts_dir(repo: Path) -> Path:
    slug = str(Path(repo).resolve()).replace("/", "-").replace(".", "-")
    return Path.home() / ".claude" / "projects" / slug


def _newest_mtime(paths) -> float:
    newest = 0.0
    for path in paths:
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def _branch_commit_time(repo: Path, branch: str) -> float:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct", branch], cwd=repo,
                             capture_output=True, text=True, check=True).stdout.strip()
        return float(out or 0)
    except (OSError, subprocess.CalledProcessError, ValueError):
        return 0.0


def last_activity(repo: Path, team: Path, plan: dict) -> float:
    """Newest of: plan edits, team_state heartbeat, agent transcripts, step-branch commits."""
    times = [_newest_mtime([now_path(team)])]
    job = load_job(team.parent)
    if job and is_open(job) and job.get("heartbeat"):
        try:
            times.append(datetime.fromisoformat(job["heartbeat"]).timestamp())
        except ValueError:
            pass
    tdir = transcripts_dir(repo)
    if tdir.is_dir():
        times.append(_newest_mtime(tdir.rglob("*.jsonl")))
    for step in plan.get("steps", []):
        if step.get("state") == "running" and step.get("branch"):
            times.append(_branch_commit_time(repo, step["branch"]))
    return max(times)


def free_gb(path: Path) -> float:
    return shutil.disk_usage(path).free / 1e9


def chain_status(plan: dict, *, activity: float, now: datetime, disk_free_gb: float,
                 base: Path | None = None) -> tuple[str, str]:
    """Pure decision; returns (STATE, one-line reason)."""
    steps = plan.get("steps", [])
    running = [s for s in steps if s.get("state") == "running"]
    if disk_free_gb < MIN_FREE_GB:
        return "STALLED", f"only {disk_free_gb:.0f} GB free (minimum {MIN_FREE_GB})"
    if running:
        quiet = (now.timestamp() - activity) / 60
        ids = ", ".join(s.get("id", "?") for s in running)
        if quiet >= STALL_MINUTES:
            return "STALLED", f"{ids} running, nothing moved for {quiet:.0f} min"
        return "WORKING", f"{ids} running"
    ready = ready_steps(plan, base)
    if ready:
        return "IDLE", f"{ready[0].get('id')} is ready and nothing is running"
    open_steps = [s for s in steps if s.get("state") != "done"]
    if open_steps:
        asks = "; ".join(f"{s.get('id')}: {s.get('blocker') or 'prereqs ' + ','.join(unmet_prereqs(s, plan, base))}"
                         for s in open_steps[:3])
        return "WAITING", asks
    return "DONE", "every step is done"


def status(repo: Path, team: Path, now=None) -> tuple[str, str]:
    plan = load_plan(team)
    now = _utc(now)
    return chain_status(plan, activity=last_activity(repo, team, plan), now=now,
                        disk_free_gb=free_gb(team if team.exists() else Path.home()), base=repo)


def owner_asks_not_doable(plan: dict, base: Path | None = None) -> list[str]:
    """Every owner ask must be doable today: a blocked step's prereqs are met."""
    return [s.get("id") for s in plan.get("steps", [])
            if s.get("blocker") and s.get("state") != "done" and unmet_prereqs(s, plan, base)]


# ---- watchdog decision -----------------------------------------------------

def watchdog_action(state: str, live: dict | None, *, now: datetime, idle_since: datetime | None,
                    nudged_at: datetime | None, session_active_at: datetime | None) -> str:
    """NONE, NUDGE (message the live producer) or TAKEOVER (run the wake task)."""
    if state not in {"IDLE", "STALLED"}:
        return "NONE"
    alive = bool(live) and session_active_at is not None and \
        now - session_active_at < timedelta(minutes=PRODUCER_ALIVE_MINUTES)
    if not alive:
        return "TAKEOVER"
    if nudged_at and now - nudged_at >= timedelta(minutes=NUDGE_GRACE_MINUTES):
        return "TAKEOVER"
    if nudged_at:
        return "NONE"
    return "NUDGE"


def _read_time(path: Path) -> datetime | None:
    try:
        return datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def watchdog_check(repo: Path, team: Path, now=None) -> str:
    """Record idle-since / nudged-at markers and print the action for the watchdog task."""
    now = _utc(now)
    state, reason = status(repo, team, now)
    idle_file, nudge_file = team / "idle-since", team / "nudged-at"
    if state not in {"IDLE", "STALLED"}:
        idle_file.unlink(missing_ok=True)
        nudge_file.unlink(missing_ok=True)
        return f"NONE {state}: {reason}"
    if not idle_file.is_file():
        _write_private(idle_file, now.isoformat())
    live = load_producer(team)
    active = None
    if live:
        sid = live.get("session_id")
        transcript = transcripts_dir(repo) / f"{sid}.jsonl"
        if sid != "pending" and transcript.is_file():
            active = datetime.fromtimestamp(transcript.stat().st_mtime, timezone.utc)
        else:
            active = _read_time_iso(live.get("claimed"))
    action = watchdog_action(state, live, now=now, idle_since=_read_time(idle_file),
                             nudged_at=_read_time(nudge_file), session_active_at=active)
    if action == "NUDGE":
        _write_private(nudge_file, now.isoformat())
    if action == "TAKEOVER":
        nudge_file.unlink(missing_ok=True)
    session = (live or {}).get("session_id", "none")
    return f"{action} {state}: {reason} (producer session {session})"


def _read_time_iso(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Producer-team plan, ledger and chain status.")
    parser.add_argument("--private-root", default=None)
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="One line: WORKING / STALLED / IDLE / WAITING / DONE")
    sub.add_parser("watchdog", help="NONE / NUDGE / TAKEOVER for the watchdog task")
    sub.add_parser("prereqs", help="Fail when an owner ask is not doable today")
    step = sub.add_parser("step", help="Set a plan step's state")
    step.add_argument("id")
    step.add_argument("state", choices=STATES)
    step.add_argument("--branch")
    step.add_argument("--pr")
    step.add_argument("--blocker")
    led = sub.add_parser("ledger", help="Append one run-ledger row")
    for name in LEDGER_FIELDS:
        led.add_argument(f"--{name.replace('_', '-')}")
    sub.add_parser("claim", help="Claim producer-live (session bound by the next hook)")
    sub.add_parser("release", help="Release producer-live before a clear")
    args = parser.parse_args(argv)

    team = team_root(Path(args.private_root).expanduser() if args.private_root else None)
    repo = Path(args.repo).resolve()
    if args.cmd == "status":
        state, reason = status(repo, team)
        print(f"{state}: {reason}")
        return 0
    if args.cmd == "watchdog":
        print(watchdog_check(repo, team))
        return 0
    if args.cmd == "prereqs":
        bad = owner_asks_not_doable(load_plan(team), repo)
        print(f"Owner asks: {'all doable today' if not bad else 'not doable yet: ' + ', '.join(bad)}")
        return 1 if bad else 0
    if args.cmd == "step":
        try:
            set_step(team, args.id, args.state, branch=args.branch, pr=args.pr, blocker=args.blocker)
        except KeyError:
            print(f"No step {args.id} in now.json", file=sys.stderr)
            return 1
        print(f"{args.id} -> {args.state}")
        return 0
    if args.cmd == "ledger":
        row = {k: getattr(args, k) for k in LEDGER_FIELDS if getattr(args, k) is not None}
        try:
            append_ledger(team, row)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Ledger row added: {row['role']} / {row['task']}")
        return 0
    if args.cmd == "claim":
        claim_producer(team)
        print("producer-live claimed (pending session)")
        return 0
    release_producer(team)
    print("producer-live released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
