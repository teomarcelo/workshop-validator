"""Deterministic merge gates for the producer team. Each prints one summary line.

    python -m workshop_validator.team_gates all [--base origin/main] [--private]
    python -m workshop_validator.team_gates em-dash|decision-rows|register|clock12
    python -m workshop_validator.team_gates merge-when-green <pr> [--dry-run]
    python -m workshop_validator.team_gates worktree-cleanup [--dry-run]

Gates on docs look only at lines a branch adds, so old text never blocks a PR.
"""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import re
import subprocess
import sys
import tempfile
import time

from . import team_kit
from .team_state import _write_private

EM_DASH = "\u2014"
TEXT_SUFFIXES = (".md", ".mdc")
ROW_PATTERN = re.compile(r"^\|\s*(W-\d{3,})\s*\|")
CLOCK24 = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b(?!\s*(?:AM|PM|am|pm))")
VERDICT = re.compile(r"^VERDICT:\s*(APPROVE|CLEAR|VETO|BLOCK|REQUEST_CHANGES)\b", re.MULTILINE)
PASSING = {"APPROVE", "CLEAR"}
ALWAYS_REVIEWERS = ("code-reviewer", "safety-scanner")
RULE_PATHS = ("workshop_validator/", ".claude/", ".cursor/", "CLAUDE.md")


def _git(repo: Path, *args: str, check=True) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=check).stdout


# ---- pure checks (unit tested) ---------------------------------------------

def added_lines(diff: str) -> list[tuple[str, str]]:
    """(file, line) for every added line in a unified diff."""
    out, current = [], None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and current:
            out.append((current, line[1:]))
    return out


def em_dash_hits(diff: str) -> list[str]:
    return [f for f, line in added_lines(diff) if f.endswith(TEXT_SUFFIXES) and EM_DASH in line]


def removed_rows(diff: str) -> list[str]:
    """Decision rows removed (and not re-added) by a diff."""
    gone, kept = set(), set()
    for line in diff.splitlines():
        if line.startswith(("---", "+++")):
            continue
        match = ROW_PATTERN.match(line[1:]) if line[:1] in "+-" else None
        if match:
            (gone if line[0] == "-" else kept).add(match.group(1))
    return sorted(gone - kept)


def deleted_decision_files(name_status: str) -> list[str]:
    return [line.split("\t", 1)[1] for line in name_status.splitlines()
            if line.startswith("D\t") and line.split("\t", 1)[1].startswith("docs/decisions/")]


def register_ids(text: str) -> list[str]:
    return [m.group(1) for line in text.splitlines() if (m := ROW_PATTERN.match(line))]


def missing_register_rows(snapshot: str, current: str) -> list[str]:
    return sorted(set(register_ids(snapshot)) - set(register_ids(current)))


def clock24_hits(text: str) -> list[str]:
    """Times shown to the owner must be 12-hour with AM/PM. ISO timestamps count as hits."""
    return [m.group(0) for m in CLOCK24.finditer(text)]


def is_review_stale(content: str, head_oid: str) -> bool:
    """A review is stale if it doesn't contain 'HEAD: <oid>' matching the PR head."""
    for line in content.splitlines():
        if line.startswith("HEAD:"):
            review_oid = line.split(":", 1)[1].strip()
            # Match if review oid is a prefix (at least 7 chars) of the current head
            if len(review_oid) >= 7 and head_oid.startswith(review_oid):
                return False
            # Or if they're exactly equal
            if review_oid == head_oid:
                return False
    return True


def review_verdicts(reviews: Path, pr: int, head_oid: str | None = None) -> dict[str, str]:
    """Load review verdicts. If head_oid is given, mark stale reviews as STALE."""
    verdicts = {}
    for path in sorted(reviews.glob(f"pr-{pr}-*.md")):
        role = path.stem[len(f"pr-{pr}-"):]
        content = path.read_text(encoding="utf-8")
        found = VERDICT.findall(content)
        verdict = found[-1] if found else "MISSING"
        if head_oid and verdict in PASSING and is_review_stale(content, head_oid):
            verdict = "STALE"
        verdicts[role] = verdict
    return verdicts


SECURITY_PATHS = (".github/", ".claude/settings.json", "workshop_validator/team_hooks.py",
                  "workshop_validator/team_gates.py", "workshop_validator/team_kit.py",
                  "workshop_validator/watchdog.py")


def required_reviewers(changed: list[str]) -> list[str]:
    roles = list(ALWAYS_REVIEWERS)
    if any(name.startswith(RULE_PATHS) for name in changed):
        roles.append("rule-auditor")
    if any(name.startswith(".github/") or name == ".claude/settings.json" or
           name in SECURITY_PATHS[2:] for name in changed):
        roles.append("security-reviewer")
    return roles


def merge_blockers(checks: list[dict], verdicts: dict[str, str], required: list[str]) -> list[str]:
    problems = []
    if not checks:
        problems.append("no CI checks reported")
    for check in checks:
        if check.get("bucket") != "pass" and check.get("bucket") != "skipping":
            problems.append(f"CI {check.get('name')}: {check.get('bucket') or check.get('state')}")
    for role in required:
        if verdicts.get(role) not in PASSING:
            problems.append(f"review {role}: {verdicts.get(role, 'MISSING')}")
    for role, verdict in verdicts.items():
        if verdict not in PASSING and role not in required:
            problems.append(f"review {role}: {verdict}")
    return problems


# ---- gate commands ---------------------------------------------------------

def _merge_base(repo: Path, base: str) -> str:
    return _git(repo, "merge-base", base, "HEAD").strip()


def _diff(repo: Path, base: str, *extra: str) -> str:
    """Branch changes plus uncommitted edits, so the gate works before a commit too."""
    return _git(repo, "diff", *extra, _merge_base(repo, base))


def gate_em_dash(repo: Path, base: str) -> bool:
    hits = em_dash_hits(_diff(repo, base))
    print(f"em-dash: {'PASS' if not hits else 'FAIL ' + str(len(hits)) + ' added line(s) in ' + ', '.join(sorted(set(hits)))}")
    return not hits


def gate_decision_rows(repo: Path, base: str) -> bool:
    rows = removed_rows(_diff(repo, base))
    files = deleted_decision_files(_diff(repo, base, "--name-status"))
    ok = not rows and not files
    print(f"decision-rows: {'PASS' if ok else 'FAIL removed ' + ', '.join(rows + files)}")
    return ok


def gate_register(team: Path) -> bool:
    """Private register is append-only: every row in the last snapshot still exists."""
    register, snapshot = team / "DECISIONS.md", team / ".register-snapshot"
    if not register.is_file():
        print("register: SKIP no private register")
        return True
    current = register.read_text(encoding="utf-8")
    missing = missing_register_rows(snapshot.read_text(encoding="utf-8"), current) if snapshot.is_file() else []
    if not missing:
        _write_private(snapshot, current)
    print(f"register: {'PASS ' + str(len(register_ids(current))) + ' rows' if not missing else 'FAIL deleted ' + ', '.join(missing)}")
    return not missing


def gate_clock12(team: Path) -> bool:
    files = [team / "dashboard.html", *sorted((team / "standups").glob("*.md"))]
    bad = [p.name for p in files if p.is_file() and clock24_hits(p.read_text(encoding="utf-8"))]
    print(f"clock12: {'PASS' if not bad else 'FAIL 24-hour or raw times in ' + ', '.join(bad)}")
    return not bad


# ---- merge when green ------------------------------------------------------

def _gh_json(repo: Path, *args: str):
    out = subprocess.run(["gh", *args], cwd=repo, capture_output=True, text=True)
    return json.loads(out.stdout or "null")


def merged_tree_gates(repo: Path, oid: str) -> bool:
    """Run tests and gates on origin/main merged with the exact oid, in a temp worktree."""
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "merged"
        _git(repo, "worktree", "add", "--detach", str(tree), "origin/main")
        try:
            merge = subprocess.run(["git", "merge", "--no-edit", "--no-ff", oid],
                                   cwd=tree, capture_output=True, text=True)
            if merge.returncode:
                print("merged-tree: FAIL merge conflict with main")
                return False
            python = repo / ".venv" / "bin" / "python"
            for cmd in ([str(python), "-m", "unittest", "discover", "-s", "tests", "-q"],
                        [str(python), "-m", "workshop_validator.public_safety"],
                        [str(python), "-m", "compileall", "-q", "workshop_validator", "tests"]):
                result = subprocess.run(cmd, cwd=tree, capture_output=True, text=True)
                if result.returncode:
                    print(f"merged-tree: FAIL {' '.join(cmd[1:4])}")
                    return False
            print("merged-tree: PASS tests, public_safety, compileall")
            return True
        finally:
            _git(repo, "worktree", "remove", "--force", str(tree), check=False)


def wait_main_ci(repo: Path, sha: str, timeout_s: int = 1500) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        runs = _gh_json(repo, "run", "list", "--branch", "main", "--limit", "10",
                        "--json", "headSha,status,conclusion") or []
        mine = [r for r in runs if r.get("headSha") == sha]
        if mine and all(r.get("status") == "completed" for r in mine):
            return "success" if all(r.get("conclusion") == "success" for r in mine) else "failure"
        time.sleep(30)
    return "timeout"


def revert_by_pr(repo: Path, pr: int, sha: str) -> str:
    """Create a revert PR in a temporary worktree. Returns PR URL or raises on error."""
    branch = f"automation/{datetime.now().date().isoformat()}-revert-pr-{pr}-{sha[:7]}"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "revert"
            _git(repo, "fetch", "origin", "main")
            _git(repo, "worktree", "add", "--detach", str(tree), "origin/main")
            try:
                _git(tree, "checkout", "-b", branch)
                _git(tree, "revert", "--no-edit", sha)
                _git(tree, "push", "-u", "origin", branch)
                url = subprocess.run(["gh", "pr", "create", "--title", f"revert: PR #{pr} broke main CI",
                                      "--body", f"Main CI failed after #{pr} merged ({sha[:10]}). Same-turn revert per the merge policy."],
                                     cwd=tree, capture_output=True, text=True, check=True).stdout.strip()
                return url
            finally:
                _git(repo, "worktree", "remove", "--force", str(tree), check=False)
    except subprocess.CalledProcessError as exc:
        print(f"merge-when-green: REVERT FAILED PR #{pr}: {exc}", file=sys.stderr)
        raise


def merge_when_green(repo: Path, team: Path, pr: int, dry_run: bool = False) -> int:
    info = _gh_json(repo, "pr", "view", str(pr), "--json",
                    "headRefName,headRefOid,isCrossRepository,files,state,baseRefName")
    if not info or info.get("state") != "OPEN" or info.get("baseRefName") != "main":
        print(f"merge-when-green: REFUSED PR #{pr} is not an open PR into main")
        return 2
    if info.get("isCrossRepository"):
        print(f"merge-when-green: REFUSED PR #{pr} is from a cross-repository fork")
        return 2
    head = info["headRefName"]
    head_oid = info.get("headRefOid", "")
    if not head.startswith("automation/"):
        print(f"merge-when-green: REFUSED head branch is not automation/*")
        return 2
    checks = _gh_json(repo, "pr", "checks", str(pr), "--json", "name,state,bucket") or []
    changed = [f["path"] for f in info.get("files", [])]
    problems = merge_blockers(checks, review_verdicts(team / "reviews", pr, head_oid),
                              required_reviewers(changed))
    if problems:
        print(f"merge-when-green: WAIT {'; '.join(problems[:5])}")
        return 1
    _git(repo, "fetch", "origin", head)
    if not merged_tree_gates(repo, head_oid):
        return 1
    if dry_run:
        print(f"merge-when-green: READY PR #{pr} (dry run, not merged)")
        return 0
    merged = subprocess.run(["gh", "pr", "merge", str(pr), "--squash", "--delete-branch",
                             "--match-head-commit", head_oid],
                            cwd=repo, capture_output=True, text=True)
    if merged.returncode:
        print(f"merge-when-green: FAIL gh pr merge exited {merged.returncode}")
        return 1
    sha = (_gh_json(repo, "pr", "view", str(pr), "--json", "mergeCommit") or {}).get("mergeCommit", {}).get("oid", "")
    result = wait_main_ci(repo, sha) if sha else "timeout"
    if result == "success":
        print(f"merge-when-green: MERGED PR #{pr}, main CI green")
        return 0
    if result == "failure":
        try:
            url = revert_by_pr(repo, pr, sha)
            print(f"merge-when-green: REVERTING PR #{pr}, main CI red; revert PR {url}. Log an escape.")
        except subprocess.CalledProcessError:
            pass
        return 1
    print(f"merge-when-green: MERGED PR #{pr}, main CI not finished; check `gh run list --branch main`")
    return 3


# ---- worktree cleanup ------------------------------------------------------

def parse_worktrees(porcelain: str) -> list[dict]:
    items, current = [], {}
    for line in porcelain.splitlines() + [""]:
        if not line:
            if current:
                items.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value or True
    return items


def worktree_idle_time(path: Path, commit_time: float) -> float:
    """Idle hours = newest of commit time and file mtimes in the worktree (skip .git)."""
    if not path.is_dir():
        return 999.0
    file_mtimes = [p.stat().st_mtime for p in path.rglob("*")
                   if p.is_file() and ".git" not in p.parts]
    newest = max([commit_time] + file_mtimes, default=commit_time)
    return (time.time() - newest) / 3600


def worktree_cleanup(repo: Path, dry_run: bool = False, idle_hours: float = 1.0) -> int:
    """Remove agent worktrees whose PR merged, that are clean and idle for an hour."""
    removed, kept = [], 0
    main_tree = repo.resolve()
    for item in parse_worktrees(_git(repo, "worktree", "list", "--porcelain")):
        path = Path(item.get("worktree", ""))
        branch = str(item.get("branch", "")).removeprefix("refs/heads/")
        if path.resolve() == main_tree or not branch:
            continue
        dirty = _git(path, "status", "--porcelain", check=False).strip() if path.is_dir() else ""
        commit_time = team_kit._branch_commit_time(repo, branch)
        idle = worktree_idle_time(path, commit_time)
        merged_prs = _gh_json(repo, "pr", "list", "--state", "merged", "--head", branch,
                              "--json", "number,headRefOid") or []
        # Only delete branch if its current oid matches the merged PR's headRefOid
        branch_oid = _git(repo, "rev-parse", branch, check=False).strip() if branch else ""
        safe_to_delete = any(pr.get("headRefOid") == branch_oid for pr in merged_prs) if merged_prs else False
        if dirty or idle < idle_hours or not safe_to_delete:
            kept += 1
            continue
        if not dry_run:
            _git(repo, "worktree", "remove", str(path), check=False)
            _git(repo, "branch", "-D", branch, check=False)
        removed.append(path.name)
    _git(repo, "worktree", "prune", check=False)
    verb = "would remove" if dry_run else "removed"
    print(f"worktree-cleanup: {verb} {len(removed)}, kept {kept}, {team_kit.free_gb(repo):.0f} GB free")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Producer-team merge gates.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--private-root", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("all", "em-dash", "decision-rows"):
        p = sub.add_parser(name)
        p.add_argument("--base", default="origin/main")
        if name == "all":
            p.add_argument("--private", action="store_true", help="Also run the private-data gates")
    sub.add_parser("register")
    sub.add_parser("clock12")
    mwg = sub.add_parser("merge-when-green")
    mwg.add_argument("pr", type=int)
    mwg.add_argument("--dry-run", action="store_true")
    wc = sub.add_parser("worktree-cleanup")
    wc.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    team = team_kit.team_root(Path(args.private_root).expanduser() if args.private_root else None)
    if args.cmd == "em-dash":
        return 0 if gate_em_dash(repo, args.base) else 1
    if args.cmd == "decision-rows":
        return 0 if gate_decision_rows(repo, args.base) else 1
    if args.cmd == "register":
        return 0 if gate_register(team) else 1
    if args.cmd == "clock12":
        return 0 if gate_clock12(team) else 1
    if args.cmd == "merge-when-green":
        return merge_when_green(repo, team, args.pr, args.dry_run)
    if args.cmd == "worktree-cleanup":
        return worktree_cleanup(repo, args.dry_run)
    results = [gate_em_dash(repo, args.base), gate_decision_rows(repo, args.base)]
    if args.private:
        results += [gate_register(team), gate_clock12(team)]
    print(f"team gates: {'PASS' if all(results) else 'FAIL'} ({sum(results)}/{len(results)})")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
