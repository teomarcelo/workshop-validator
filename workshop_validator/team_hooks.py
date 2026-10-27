"""Claude Code hooks for the producer team (stdlib only; read JSON on stdin).

- `write-guard` (PreToolUse on Write/Edit/NotebookEdit): an agent writes only
  inside its own checkout or worktree, the private team folder, or a scratch
  folder. It fails closed: when the workspace or target does not resolve, the
  write is denied (exit 2). The settings command adds `|| exit 2` so a crash
  denies too. Review verdicts (`team/reviews/`) are never written this way;
  `team_kit review` saves them. Settings, hooks, `team_kit.py`, role files and
  workflows are written only from an agent worktree under `.claude/worktrees/`.
  This guards against mistakes; a same-user agent with Bash is not stopped.
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
PROTECTED_FROM_MAIN = {".claude/settings.json", ".claude/settings.local.json",
                       "workshop_validator/team_hooks.py", "workshop_validator/team_kit.py",
                       "workshop_validator/team_gates.py", "workshop_validator/team_state.py",
                       "workshop_validator/watchdog.py"}
PROTECTED_PREFIXES_FROM_MAIN = (".claude/agents/", ".claude/workflows/")


def _git_root(cwd: Path) -> Path | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out).resolve() if out else None


def _main_root(cwd: Path) -> Path | None:
    """The main checkout that owns this workspace (the parent of the common .git)."""
    try:
        out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
                             cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return Path(out).resolve().parent if out else None


def is_agent_worktree(root: Path, main_root: Path | None) -> bool:
    """True when root sits strictly inside <main checkout>/.claude/worktrees/ (path containment)."""
    if main_root is None:
        return False
    worktrees = main_root.resolve() / ".claude" / "worktrees"
    if not _inside_allow(root, worktrees):
        return False
    # Check it's strictly inside (not the worktrees dir itself)
    try:
        rel = root.resolve().relative_to(worktrees.resolve())
        return bool(rel.parts)
    except ValueError:
        return False


def is_protected(rel: str) -> bool:
    rel_lower = rel.casefold()
    return any(rel_lower == p.casefold() for p in PROTECTED_FROM_MAIN) or \
           any(rel_lower.startswith(p.casefold()) for p in PROTECTED_PREFIXES_FROM_MAIN)


def _inside_allow(path: Path, root: Path) -> bool:
    """Test containment for ALLOW decisions (exact case, respects filesystem)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def _inside_deny(path: Path, root: Path) -> bool:
    """Test containment for DENY decisions (case-insensitive for protection)."""
    try:
        path_str = str(path.resolve()).casefold()
        root_str = str(root.resolve()).casefold()
        # Ensure root ends with separator for proper containment check
        if not root_str.endswith(os.sep):
            root_str += os.sep
        return path_str.startswith(root_str)
    except (ValueError, OSError):
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
                   scratch: list[Path] | None = None, main_root=_main_root) -> tuple[bool, str]:
    """Return (allowed, reason). Pure apart from the injectable git lookups."""
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
    if any(target.name.casefold() == n.casefold() for n in NEVER_WRITE):
        return False, "write guard: credential and session files are never written by agents"
    if _inside_deny(target, (home / ".workshop-validator" / "team" / "reviews").resolve()):
        return False, ("write guard: review verdicts are saved only with "
                       "`team_kit review --pr N --role R --head SHA`")
    worktrees = root / ".claude" / "worktrees"
    in_own_workspace = _inside_allow(target, root) and not (_inside_deny(target, worktrees) and not _inside_allow(root, worktrees))
    # Hooks, gates, settings and role files change only from an agent worktree.
    if in_own_workspace:
        try:
            rel = target.relative_to(root).as_posix()
        except ValueError:
            # Target resolved differently than root (e.g., case mismatch on case-sensitive FS)
            return False, "write guard: target path case does not match workspace root"
        if is_protected(rel) and not is_agent_worktree(root, main_root(cwd)):
            return False, f"write guard: {rel} can only be written from a worktree, not the main checkout"
        return True, "inside own workspace"
    allowed = [home / ".workshop-validator" / "team", home / ".claude" / "plans",
               team_kit.transcripts_dir(root) / "memory",
               *(scratch_roots() if scratch is None else scratch)]
    if any(_inside_allow(target, base.resolve()) for base in allowed):
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
    # Check main checkout is on main branch
    from . import public_safety
    main_hint = "Review in a temp worktree or with git show/diff; never check out anything in the main checkout."
    clean_ok, clean_msg = public_safety.check_main_clean(repo, check_dirty=False)
    if not clean_ok:
        return f"Main checkout problem: {clean_msg}. {main_hint}"
    # Chain status comes before the dirty-file check, so a stray file never hides an IDLE/STALLED chain.
    state, reason = (status_fn or team_kit.status)(repo, team)
    if state in {"IDLE", "STALLED"}:
        return (f"Chain is {state}: {reason}. Start the next ready step (or fix the stall) "
                "and mark it in now.json before ending the turn.")
    clean_ok, clean_msg = public_safety.check_main_clean(repo)
    if not clean_ok:
        return f"Main checkout problem: {clean_msg}. {main_hint}"
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
