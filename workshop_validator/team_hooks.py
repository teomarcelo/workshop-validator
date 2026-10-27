"""Claude Code hooks for the producer team (stdlib only; read JSON on stdin).

- `write-guard` (PreToolUse on Write/Edit/NotebookEdit): an agent writes only
  inside its own checkout or worktree, the private team folder, or a scratch
  folder. It fails closed: when the workspace or target does not resolve, the
  write is denied (exit 2). The settings command adds `|| exit 2` so a crash
  denies too.
- `stop` (Stop): for the producer session only, block ending a turn while the
  chain is IDLE or STALLED, or while the plan or register is newer than the
  dashboard. A second stop in a row (`stop_hook_active`) is always let through.
"""

from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

from . import team_kit

NEVER_WRITE = {"verification-orgs.json", "browser-state.json"}


def _git_root(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out).resolve() if out else None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_target(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve()


def scratch_roots() -> list[Path]:
    roots = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}
    if os.environ.get("TMPDIR"):
        roots.add(Path(os.environ["TMPDIR"]).resolve())
    return sorted(roots)


def guard_decision(payload: dict, *, home: Path | None = None, git_root=_git_root,
                   scratch: list[Path] | None = None) -> tuple[bool, str]:
    """Return (allowed, reason). Pure apart from the injectable git lookup."""
    home = (home or Path.home()).resolve()
    tool_input = payload.get("tool_input") or {}
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    cwd_raw = payload.get("cwd")
    if not raw or not cwd_raw:
        return False, "write guard: no target or working folder in the hook input"
    cwd = Path(cwd_raw).expanduser()
    if not cwd.is_dir():
        return False, "write guard: working folder does not resolve"
    root = git_root(cwd)
    if root is None:
        return False, "write guard: workspace is not a git checkout; refusing"
    try:
        target = _resolve_target(raw, cwd)
    except (OSError, RuntimeError):
        return False, "write guard: target path does not resolve"
    if target.name in NEVER_WRITE:
        return False, "write guard: credential and session files are never written by agents"
    worktrees = root / ".claude" / "worktrees"
    if _inside(target, root) and not (_inside(target, worktrees) and not _inside(root, worktrees)):
        return True, "inside own workspace"
    allowed = [home / ".workshop-validator" / "team", home / ".claude" / "plans",
               team_kit.transcripts_dir(root) / "memory",
               *(scratch_roots() if scratch is None else scratch)]
    if any(_inside(target, base.resolve()) for base in allowed):
        return True, "inside an allowed private or scratch folder"
    return False, f"write guard: outside this agent's workspace ({root.name}); write in your own worktree"


def stop_decision(payload: dict, *, team: Path, repo: Path, status_fn=None) -> str | None:
    """Return a block reason, or None to let the turn end."""
    if payload.get("stop_hook_active"):
        return None
    session = payload.get("session_id") or ""
    team_kit.bind_producer(team, session)
    live = team_kit.load_producer(team)
    if not live or live.get("session_id") != session:
        return None
    state, reason = (status_fn or team_kit.status)(repo, team)
    if state in {"IDLE", "STALLED"}:
        return (f"Chain is {state}: {reason}. Start the next ready step (or fix the stall) "
                "and mark it in now.json before ending the turn.")
    dashboard = team / "dashboard.html"
    sources = [team_kit.now_path(team), team / "DECISIONS.md"]
    newest = max((p.stat().st_mtime for p in sources if p.is_file()), default=0)
    if newest and (not dashboard.is_file() or dashboard.stat().st_mtime < newest):
        return ("Dashboard is stale: the plan or register changed after the last render. "
                "Run `.venv/bin/python -m workshop_validator.team_dashboard` first.")
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = argv[0] if argv else ""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    if mode == "write-guard":
        try:
            allowed, reason = guard_decision(payload)
        except Exception as exc:  # fail closed
            allowed, reason = False, f"write guard error: {type(exc).__name__}"
        if not allowed:
            print(reason, file=sys.stderr)
            return 2
        return 0
    if mode == "stop":
        repo = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".").resolve()
        try:
            reason = stop_decision(payload, team=team_kit.team_root(), repo=repo)
        except Exception:  # never trap a session because the check broke
            return 0
        if reason:
            print(json.dumps({"decision": "block", "reason": reason}))
        return 0
    print("usage: team_hooks write-guard|stop", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
