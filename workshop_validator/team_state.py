"""State tracking for the AI agent team's validation job.

Stores job state, phase progress, and activity log in `<private_root>/team/`.
All state files are mode 0600, directory 0700.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import os
import sys

PHASES = [
    "preflight", "run", "verify", "triage", "fix", "test",
    "audit", "reset", "lessons", "report", "publish", "done",
]

STATE_NAME = "state.json"
LOG_NAME = "team-log.json"


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(root: Path) -> Path:
    return Path(root) / "team" / STATE_NAME


def _log_path(root: Path) -> Path:
    return Path(root) / "team" / LOG_NAME


def start_job(root: Path, pdf: str, mode: str, *, now=None) -> dict:
    """Start a new validation job."""
    if now is None:
        now = _now_utc()
    state = {
        "job_id": datetime.fromisoformat(now).strftime("%Y%m%dT%H%M%SZ"),
        "pdf": pdf,
        "mode": mode,
        "phase": "preflight",
        "iteration": 0,
        "run_dir": None,
        "outcome": None,
        "started": now,
        "heartbeat": now,
        "lock": None,
        "restarts": [],
    }
    _write_private(_state_path(root), json.dumps(state, indent=2) + "\n")
    _write_private(_log_path(root), json.dumps([], indent=1) + "\n")
    return state


def load(root: Path) -> dict:
    """Load the current job state, or None if no job exists."""
    path = _state_path(root)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def advance(root: Path, phase: str, run_dir: str = None, detail: str = "", *, now=None) -> dict:
    """Advance to the next phase and refresh heartbeat."""
    state = load(root)
    if state is None:
        raise ValueError("No active job to advance")
    if phase not in PHASES:
        raise ValueError(f"Unknown phase: {phase}")
    if now is None:
        now = _now_utc()
    state["phase"] = phase
    state["heartbeat"] = now
    if run_dir is not None:
        state["run_dir"] = run_dir
    if phase == "run":
        state["iteration"] = state.get("iteration", 0) + 1
    _write_private(_state_path(root), json.dumps(state, indent=2) + "\n")
    if detail:
        log(root, agent="system", action=f"advance-{phase}", detail=detail, now=now)
    return state


def heartbeat(root: Path, *, now=None) -> dict:
    """Update the heartbeat timestamp."""
    state = load(root)
    if state is None:
        raise ValueError("No active job for heartbeat")
    if now is None:
        now = _now_utc()
    state["heartbeat"] = now
    _write_private(_state_path(root), json.dumps(state, indent=2) + "\n")
    return state


def log(root: Path, agent: str, action: str, detail: str = "", link: str = "", *, now=None) -> None:
    """Append an entry to the team log."""
    path = _log_path(root)
    entries = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    if now is None:
        now = _now_utc()
    entry = {"time": now, "agent": agent, "action": action}
    if detail:
        entry["detail"] = detail
    if link:
        entry["link"] = link
    entries.append(entry)
    _write_private(path, json.dumps(entries, indent=1) + "\n")


def finish(root: Path, outcome: str, *, now=None) -> dict:
    """Mark the job as done with the given outcome."""
    state = load(root)
    if state is None:
        raise ValueError("No active job to finish")
    if now is None:
        now = _now_utc()
    state["phase"] = "done"
    state["outcome"] = outcome
    state["heartbeat"] = now
    _write_private(_state_path(root), json.dumps(state, indent=2) + "\n")
    log(root, agent="system", action="finish", detail=outcome, now=now)
    return state


def is_stale(state: dict, now: datetime, minutes: int = 15) -> bool:
    """Check if the heartbeat is older than the threshold."""
    if state is None or not state.get("heartbeat"):
        return False
    heartbeat = datetime.fromisoformat(state["heartbeat"])
    return now - heartbeat > timedelta(minutes=minutes)


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive (injectable for tests)."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def acquire_lock(root: Path, pid: int, *, alive=None, now=None) -> bool:
    """Acquire the job lock. Fails if held by a live process."""
    if alive is None:
        alive = _pid_alive
    if now is None:
        now = _now_utc()
    state = load(root)
    if state is None:
        raise ValueError("No active job to lock")
    lock = state.get("lock")
    if lock is not None:
        holder = lock.get("pid")
        if holder and alive(holder):
            return False
    state["lock"] = {"pid": pid, "started": now}
    state["heartbeat"] = now
    _write_private(_state_path(root), json.dumps(state, indent=2) + "\n")
    return True


def release_lock(root: Path, *, now=None) -> dict:
    """Release the job lock."""
    state = load(root)
    if state is None:
        raise ValueError("No active job to unlock")
    if now is None:
        now = _now_utc()
    state["lock"] = None
    state["heartbeat"] = now
    _write_private(_state_path(root), json.dumps(state, indent=2) + "\n")
    return state


def is_open(state: dict) -> bool:
    """Check if the job is still open (not done)."""
    return state is not None and state.get("phase") != "done"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage AI team job state.")
    parser.add_argument("--private-root", default=str(Path.home() / ".workshop-validator"))
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a new job")
    start.add_argument("--pdf", required=True)
    start.add_argument("--mode", required=True, choices=["quick", "full"])

    adv = sub.add_parser("advance", help="Advance to the next phase")
    adv.add_argument("--phase", required=True, choices=PHASES)
    adv.add_argument("--run-dir")
    adv.add_argument("--detail", default="")

    lg = sub.add_parser("log", help="Append a log entry")
    lg.add_argument("--agent", required=True)
    lg.add_argument("--action", required=True)
    lg.add_argument("--detail", default="")
    lg.add_argument("--link", default="")

    sub.add_parser("heartbeat", help="Refresh the heartbeat")

    fin = sub.add_parser("finish", help="Mark job as done")
    fin.add_argument("--outcome", required=True)

    sub.add_parser("show", help="Print the current state")

    args = parser.parse_args(argv)
    root = Path(args.private_root).expanduser()

    if args.command == "start":
        state = start_job(root, args.pdf, args.mode)
        print(json.dumps(state, indent=2))
    elif args.command == "advance":
        state = advance(root, args.phase, args.run_dir, args.detail)
        print(json.dumps(state, indent=2))
    elif args.command == "log":
        log(root, args.agent, args.action, args.detail, args.link)
        print("Logged.")
    elif args.command == "heartbeat":
        state = heartbeat(root)
        print(json.dumps(state, indent=2))
    elif args.command == "finish":
        state = finish(root, args.outcome)
        print(json.dumps(state, indent=2))
    elif args.command == "show":
        state = load(root)
        if state is None:
            print("No active job.")
            return 1
        print(json.dumps(state, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
