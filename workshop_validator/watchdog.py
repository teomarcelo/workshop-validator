"""Watchdog for restarting stalled AI agent team jobs.

Checks for stale heartbeats and restarts the validation loop when safe.
Limits restarts to 3 per hour to avoid infinite loops.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

from .team_state import is_open, is_stale, load, _write_private

RESTART_CAP_PER_HOUR = 3
STALE_THRESHOLD_MINUTES = 15
STALLED_NAME = "stalled.json"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _stalled_path(root: Path) -> Path:
    return Path(root) / "team" / STALLED_NAME


def decide(state: dict, now: datetime = None) -> str:
    """Decide the action: idle, alive, restart, or stalled."""
    if now is None:
        now = _now_utc()
    if not is_open(state):
        return "idle"
    if not is_stale(state, now, minutes=STALE_THRESHOLD_MINUTES):
        return "alive"
    lock = state.get("lock")
    if lock:
        pid = lock.get("pid")
        if pid:
            try:
                os.kill(pid, 0)
                return "alive"
            except (OSError, ProcessLookupError):
                pass
    restarts = state.get("restarts", [])
    recent = [
        r for r in restarts
        if now - datetime.fromisoformat(r) < timedelta(hours=1)
    ]
    if len(recent) >= RESTART_CAP_PER_HOUR:
        return "stalled"
    return "restart"


def record_restart(root: Path, now: datetime = None) -> None:
    """Record a restart timestamp in the state."""
    if now is None:
        now = _now_utc()
    state = load(root)
    if state is None:
        return
    restarts = state.get("restarts", [])
    restarts.append(now.isoformat())
    state["restarts"] = restarts
    from .team_state import _state_path
    _write_private(_state_path(root), json.dumps(state, indent=2) + "\n")


def record_stalled(root: Path, reason: str, now: datetime = None) -> None:
    """Write the stalled marker file."""
    if now is None:
        now = _now_utc()
    path = _stalled_path(root)
    if path.is_file():
        return
    payload = {"time": now.isoformat(), "reason": reason}
    _write_private(path, json.dumps(payload, indent=2) + "\n")


def claude_path() -> str | None:
    """The Claude Code CLI. launchd has a short PATH, so common places are tried too."""
    override = os.environ.get("WORKSHOP_VALIDATOR_CLAUDE")
    candidates = [override] if override else []
    candidates += [shutil.which("claude"), str(Path.home() / ".local" / "bin" / "claude"),
                   "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
    for item in candidates:
        if item and os.access(item, os.X_OK):
            return item
    return None


def launch_resume(repo_root: Path, log_path: Path, venv_python: Path) -> None:
    """Launch a detached `claude -p "/validate-guide --resume"` (injectable for tests)."""
    del venv_python  # the skill finds the venv itself
    claude = claude_path()
    if claude is None:
        raise FileNotFoundError("The claude CLI was not found; set WORKSHOP_VALIDATOR_CLAUDE.")
    cmd = [
        claude,
        "-p", "/validate-guide --resume",
        "--permission-mode", "acceptEdits",
    ]
    log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            stdout=f,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def _team_lesson(private_root: Path, cause: str, state: dict, now: datetime) -> None:
    """Record a restart or stall as a team lesson, keyed by the phase it died in."""
    from .lessons import TEAM, record_cause
    phase = str(state.get("phase") or "unknown phase")
    try:
        record_cause(
            private_root, kind=TEAM, title=f"watchdog: {phase}", cause=cause,
            never="Heartbeat at least every 3 minutes while waiting on a run or agent.",
            today=now.date().isoformat(),
        )
    except (OSError, ValueError):
        pass


def run_once(repo_root: Path, private_root: Path, *, launcher=None, now=None) -> int:
    """Check and act on the current job state."""
    if launcher is None:
        launcher = launch_resume
    if now is None:
        now = _now_utc()
    state = load(private_root)
    action = decide(state, now=now)
    if action in {"idle", "alive"}:
        return 0
    if action == "stalled":
        if not _stalled_path(private_root).is_file():
            record_stalled(private_root, f"Too many restarts: {RESTART_CAP_PER_HOUR}/hour", now=now)
            _team_lesson(private_root, f"Job stalled after {RESTART_CAP_PER_HOUR} restarts in an hour",
                         state, now)
            print(f"Job stalled after {RESTART_CAP_PER_HOUR} restarts. Marker written.")
        return 0
    record_restart(private_root, now=now)
    _team_lesson(private_root, "Heartbeat went stale and the job was restarted", state, now)
    from .team_state import log
    log(private_root, agent="watchdog", action="restart", detail="Stale heartbeat detected", now=now.isoformat())
    venv = repo_root / ".venv" / "bin" / "python"
    log_path = private_root / "team" / "watchdog.log"
    try:
        launcher(repo_root, log_path, venv)
    except OSError as exc:
        _team_lesson(private_root, f"Restart could not launch: {exc}", state, now)
        print(f"Restart failed: {exc}", file=sys.stderr)
        return 1
    print(f"Restarted validation job. Log: {log_path}")
    return 0


def cleanup_worktrees(repo_root: Path) -> None:
    """Remove merged, clean, idle agent worktrees; never fails the watchdog."""
    from .team_gates import worktree_cleanup
    try:
        worktree_cleanup(repo_root)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(f"worktree-cleanup skipped: {exc}", file=sys.stderr)


def install_plist(template: Path, home: Path, venv_python: Path, repo_root: Path, private_root: Path) -> str:
    """Generate the plist file and return the launchctl command."""
    content = template.read_text(encoding="utf-8")
    content = content.replace("{{PYTHON}}", str(venv_python))
    content = content.replace("{{REPO}}", str(repo_root))
    content = content.replace("{{PRIVATE_ROOT}}", str(private_root))
    search = [str(home / ".local" / "bin"), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    content = content.replace("{{PATH}}", ":".join(search))
    target = home / "Library" / "LaunchAgents" / "com.workshop-validator.watchdog.plist"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"launchctl load {target}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Watchdog for AI team jobs.")
    parser.add_argument("--private-root", default=str(Path.home() / ".workshop-validator"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--once", action="store_true", help="Check once and exit")
    parser.add_argument("--install", action="store_true", help="Install launchd plist")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall launchd plist")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    private_root = Path(args.private_root).expanduser()

    if args.install:
        template = repo_root / "scripts" / "com.workshop-validator.watchdog.plist"
        if not template.is_file():
            print(f"Template not found: {template}", file=sys.stderr)
            return 1
        venv = repo_root / ".venv" / "bin" / "python"
        home = Path.home()
        cmd = install_plist(template, home, venv, repo_root, private_root)
        print(f"Installed plist to ~/Library/LaunchAgents/")
        print(f"To start: {cmd}")
        return 0

    if args.uninstall:
        plist = Path.home() / "Library" / "LaunchAgents" / "com.workshop-validator.watchdog.plist"
        if plist.is_file():
            print(f"To stop: launchctl unload {plist}")
            print(f"Then delete: {plist}")
        else:
            print("Plist not found.")
        return 0

    if args.once:
        code = run_once(repo_root, private_root)
        cleanup_worktrees(repo_root)
        return code

    parser.error("Use --once, --install, or --uninstall")


if __name__ == "__main__":
    sys.exit(main())
