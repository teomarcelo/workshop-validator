"""Deterministic merge gates for the producer team. Each prints one summary line.

    python -m workshop_validator.team_gates all [--base origin/main] [--private]
    python -m workshop_validator.team_gates em-dash|decision-rows|register|clock12
    python -m workshop_validator.team_gates prove-tests <pr>
    python -m workshop_validator.team_gates merge-when-green <pr> [--dry-run]
    python -m workshop_validator.team_gates worktree-cleanup [--dry-run]

Gates on docs look only at lines a branch adds, so old text never blocks a PR.
"""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
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


def _default_runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _git(repo: Path, *args: str, check=True, runner=None) -> str:
    """Run git through `runner(cmd, cwd)` (tests inject a fake); raise on failure when check."""
    result = (runner or _default_runner)(["git", *args], Path(repo))
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args],
                                            result.stdout, result.stderr)
    return result.stdout or ""


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
    """Stale unless the first non-empty line is 'HEAD: <oid>' matching the PR head.

    `team_kit review` writes that line; a HEAD line anywhere else does not count.
    """
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if not first.startswith("HEAD:") or not head_oid:
        return True
    review_oid = first.split(":", 1)[1].strip()
    if review_oid == head_oid:
        return False
    return not (len(review_oid) >= 7 and head_oid.startswith(review_oid))


def review_verdicts(reviews: Path, pr: int, head_oid: str | None = None) -> dict[str, str]:
    """Load review verdicts. If head_oid is given, mark stale reviews as STALE."""
    verdicts = {}
    for path in sorted(reviews.glob(f"pr-{pr}-*.md")):
        role = path.stem[len(f"pr-{pr}-"):]
        content = path.read_text(encoding="utf-8")
        found = VERDICT.findall(content)
        verdict = found[-1] if found else "MISSING"
        # Fail closed: empty/missing head_oid or stale review marks PASSING verdicts as STALE
        if verdict in PASSING and (not head_oid or not head_oid.strip() or is_review_stale(content, head_oid)):
            verdict = "STALE"
        verdicts[role] = verdict
    return verdicts


SECURITY_PREFIXES = (".github/", ".claude/agents/", ".claude/workflows/")
SECURITY_FILES = {".claude/settings.json", ".claude/settings.local.json",
                  "workshop_validator/team_hooks.py", "workshop_validator/team_gates.py",
                  "workshop_validator/team_kit.py", "workshop_validator/watchdog.py",
                  "workshop_validator/public_safety.py", "workshop_validator/team_state.py"}


def required_reviewers(changed: list[str]) -> list[str]:
    roles = list(ALWAYS_REVIEWERS)
    if any(name.startswith(RULE_PATHS) for name in changed):
        roles.append("rule-auditor")
    if any(name.startswith(SECURITY_PREFIXES) or name in SECURITY_FILES for name in changed):
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

def _gh_json(repo: Path, *args: str, runner=None):
    out = (runner or _default_runner)(["gh", *args], Path(repo))
    return json.loads(out.stdout or "null")


def merged_tree_gates(repo: Path, oid: str, runner=None) -> bool:
    """Run tests and gates on origin/main merged with the exact oid, in a temp worktree."""
    run = runner or _default_runner
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "merged"
        _git(repo, "worktree", "add", "--detach", str(tree), "origin/main", runner=runner)
        try:
            merge = run(["git", "merge", "--no-edit", "--no-ff", oid], tree)
            if merge.returncode:
                print("merged-tree: FAIL merge conflict with main")
                return False
            python = repo / ".venv" / "bin" / "python"
            for cmd in ([str(python), "-m", "unittest", "discover", "-s", "tests", "-q"],
                        [str(python), "-m", "workshop_validator.public_safety"],
                        [str(python), "-m", "compileall", "-q", "workshop_validator", "tests"]):
                result = run(cmd, tree)
                if result.returncode:
                    print(f"merged-tree: FAIL {' '.join(cmd[1:4])}")
                    return False
            print("merged-tree: PASS tests, public_safety, compileall")
            return True
        finally:
            _git(repo, "worktree", "remove", "--force", str(tree), check=False, runner=runner)


def wait_main_ci(repo: Path, sha: str, timeout_s: int = 1500, runner=None) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        runs = _gh_json(repo, "run", "list", "--branch", "main", "--limit", "10",
                        "--json", "headSha,status,conclusion", runner=runner) or []
        mine = [r for r in runs if r.get("headSha") == sha]
        if mine and all(r.get("status") == "completed" for r in mine):
            return "success" if all(r.get("conclusion") == "success" for r in mine) else "failure"
        time.sleep(30)
    return "timeout"


def revert_by_pr(repo: Path, pr: int, sha: str, runner=None) -> int:
    """Open a revert PR from a temporary worktree (never the owner checkout).

    Prints one line. Returns 0 when the revert PR is open, 1 with a REVERT FAILED
    line otherwise. The local revert branch is always deleted afterwards, so a
    second attempt does not collide with it.
    """
    run = runner or _default_runner
    branch = f"automation/{datetime.now().date().isoformat()}-revert-pr-{pr}-{sha[:7]}"
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "revert"
        added = False
        try:
            _git(repo, "fetch", "origin", "main", runner=runner)
            _git(repo, "worktree", "add", "--detach", str(tree), "origin/main", runner=runner)
            added = True
            _git(tree, "checkout", "-b", branch, runner=runner)
            _git(tree, "revert", "--no-edit", sha, runner=runner)
            _git(tree, "push", "-u", "origin", branch, runner=runner)
            created = run(["gh", "pr", "create", "--title", f"revert: PR #{pr} broke main CI",
                           "--body", f"Main CI failed after #{pr} merged ({sha[:10]}). "
                           "Same-turn revert per the merge policy."], tree)
            if created.returncode:
                raise subprocess.CalledProcessError(created.returncode, ["gh", "pr", "create"])
            print(f"merge-when-green: REVERTING PR #{pr}, main CI red; revert PR "
                  f"{(created.stdout or '').strip()}. Log an escape.")
            return 0
        except subprocess.CalledProcessError as exc:
            step = " ".join(str(part) for part in list(exc.cmd)[:2])
            print(f"merge-when-green: REVERT FAILED PR #{pr}: {step} exited {exc.returncode}; "
                  "revert by hand and log an escape.")
            return 1
        finally:
            if added:
                _git(repo, "worktree", "remove", "--force", str(tree), check=False, runner=runner)
            _git(repo, "branch", "-D", branch, check=False, runner=runner)


def merge_when_green(repo: Path, team: Path, pr: int, dry_run: bool = False, runner=None) -> int:
    run = runner or _default_runner
    info = _gh_json(repo, "pr", "view", str(pr), "--json",
                    "headRefName,headRefOid,isCrossRepository,files,state,baseRefName",
                    runner=runner)
    if not info or info.get("state") != "OPEN" or info.get("baseRefName") != "main":
        print(f"merge-when-green: REFUSED PR #{pr} is not an open PR into main")
        return 2
    if info.get("isCrossRepository"):
        print(f"merge-when-green: REFUSED PR #{pr} is from a cross-repository fork")
        return 2
    head = info.get("headRefName") or ""
    head_oid = str(info.get("headRefOid") or "").strip()
    if not head_oid:
        print(f"merge-when-green: REFUSED PR #{pr} has no headRefOid to pin the merge to")
        return 2
    if not head.startswith("automation/"):
        print("merge-when-green: REFUSED head branch is not automation/*")
        return 2
    checks = _gh_json(repo, "pr", "checks", str(pr), "--json", "name,state,bucket",
                      runner=runner) or []
    changed = [f["path"] for f in info.get("files", [])]
    problems = merge_blockers(checks, review_verdicts(team / "reviews", pr, head_oid),
                              required_reviewers(changed))
    if problems:
        print(f"merge-when-green: WAIT {'; '.join(problems[:5])}")
        return 1
    # Fetch main too, so the merged-tree gates run against current main.
    fetched = run(["git", "fetch", "origin", "main", head], repo)
    if fetched.returncode:
        print(f"merge-when-green: FAIL git fetch origin main {head} exited {fetched.returncode}")
        return 1
    if not merged_tree_gates(repo, head_oid, runner=runner):
        return 1
    # Run prove-tests gate
    base_oid = _git(repo, "rev-parse", "origin/main", runner=runner).strip()
    if not prove_tests(repo, base_oid, head_oid, runner=runner):
        return 1
    if dry_run:
        print(f"merge-when-green: READY PR #{pr} (dry run, not merged)")
        return 0
    merged = run(["gh", "pr", "merge", str(pr), "--squash", "--delete-branch",
                  "--match-head-commit", head_oid], repo)
    if merged.returncode:
        print(f"merge-when-green: FAIL gh pr merge exited {merged.returncode}")
        return 1
    view = _gh_json(repo, "pr", "view", str(pr), "--json", "mergeCommit", runner=runner) or {}
    sha = (view.get("mergeCommit") or {}).get("oid", "")
    result = wait_main_ci(repo, sha, runner=runner) if sha else "timeout"
    if result == "success":
        print(f"merge-when-green: MERGED PR #{pr}, main CI green")
        return 0
    if result == "failure":
        revert_by_pr(repo, pr, sha, runner=runner)
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


def worktree_cleanup(repo: Path, dry_run: bool = False, idle_hours: float = 1.0,
                     runner=None) -> int:
    """Remove agent worktrees whose PR merged, that are clean and idle for an hour."""
    removed, kept = [], 0
    main_tree = repo.resolve()
    for item in parse_worktrees(_git(repo, "worktree", "list", "--porcelain", runner=runner)):
        path = Path(item.get("worktree", ""))
        branch = str(item.get("branch", "")).removeprefix("refs/heads/")
        if path.resolve() == main_tree or not branch:
            continue
        dirty = _git(path, "status", "--porcelain", check=False, runner=runner).strip() if path.is_dir() else ""
        commit_time = team_kit._branch_commit_time(repo, branch)
        idle = worktree_idle_time(path, commit_time)
        merged_prs = _gh_json(repo, "pr", "list", "--state", "merged", "--head", branch,
                              "--json", "number,headRefOid", runner=runner) or []
        # Only delete branch if its current oid matches the merged PR's headRefOid
        branch_oid = _git(repo, "rev-parse", branch, check=False, runner=runner).strip() if branch else ""
        safe_to_delete = bool(branch_oid) and any(pr.get("headRefOid") == branch_oid for pr in merged_prs)
        # Also check if branch tip is an ancestor of any merged PR's headRefOid
        if not safe_to_delete and branch_oid and merged_prs:
            for pr in merged_prs:
                pr_oid = pr.get("headRefOid")
                if pr_oid:
                    # Try to fetch the oid (may fail if it's been GC'd)
                    _git(repo, "fetch", "origin", pr_oid, check=False, runner=runner)
                    # Check if branch_oid is ancestor of pr_oid
                    run = runner or _default_runner
                    result = run(["git", "merge-base", "--is-ancestor", branch_oid, pr_oid], repo)
                    if result.returncode == 0:
                        safe_to_delete = True
                        break
        if dirty or idle < idle_hours or not safe_to_delete:
            kept += 1
            continue
        if not dry_run:
            _git(repo, "worktree", "remove", str(path), check=False, runner=runner)
            _git(repo, "branch", "-D", branch, check=False, runner=runner)
        removed.append(path.name)
    _git(repo, "worktree", "prune", check=False, runner=runner)
    verb = "would remove" if dry_run else "removed"
    print(f"worktree-cleanup: {verb} {len(removed)}, kept {kept}, {team_kit.free_gb(repo):.0f} GB free")
    return 0


# ---- prove-tests -----------------------------------------------------------

def parse_new_test_ids(diff: str) -> list[str]:
    """Extract full test ids (module.Class.method) from def test_* lines in added hunks."""
    ids = []
    current_file = None
    current_class = None
    lines = diff.splitlines()

    for i, line in enumerate(lines):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            current_class = None
        elif line.startswith("@@"):
            # Reset class context at new hunk, we'll find it as we go
            current_class = None
        elif current_file and current_file.startswith("tests/"):
            # Track class definitions in both added and context lines
            if line.startswith(("+", " ")) and "class " in line:
                stripped = line[1:].strip() if line.startswith("+") else line.strip()
                if stripped.startswith("class "):
                    current_class = stripped.split("(")[0].replace("class ", "").replace(":", "").strip()
            # Look for new test methods
            elif line.startswith("+"):
                stripped = line[1:].strip()
                if stripped.startswith("def test_"):
                    method = stripped.split("(")[0].replace("def ", "")
                    module = current_file.replace("/", ".").replace(".py", "")
                    if current_class:
                        ids.append(f"{module}.{current_class}.{method}")
                    else:
                        # If no class context, look backwards in the diff for class definition
                        for j in range(i - 1, -1, -1):
                            prev = lines[j]
                            if prev.startswith("@@"):
                                break
                            if "class " in prev:
                                cls_line = prev[1:].strip() if prev.startswith(("+", " ", "-")) else prev.strip()
                                if cls_line.startswith("class "):
                                    found_class = cls_line.split("(")[0].replace("class ", "").replace(":", "").strip()
                                    ids.append(f"{module}.{found_class}.{method}")
                                    break
    return ids


def parse_unittest_output(output: str) -> dict[str, str]:
    """Parse unittest -v output into {test_id: status} where status is ok/FAIL/ERROR/skipped."""
    results = {}
    for line in output.splitlines():
        line = line.strip()
        # unittest -v format: "test_method (module.Class) ... ok"
        if " ... " in line:
            parts = line.split(" ... ", 1)
            if len(parts) == 2:
                test_part, status = parts
                # Extract test id from "test_method (module.Class)"
                if "(" in test_part and ")" in test_part:
                    method = test_part.split("(")[0].strip()
                    module_class = test_part.split("(")[1].split(")")[0].strip()
                    test_id = f"{module_class}.{method}"
                    # status can be "ok", "FAIL", "ERROR", "skipped 'reason'"
                    status_word = status.split()[0]
                    results[test_id] = status_word
    return results


def prove_tests(repo: Path, base_oid: str, head_oid: str, runner=None) -> bool:
    """Prove new tests fail on base code. PASS when each changed code file has >=1 test fail on base."""
    run = runner or _default_runner

    # Check Chromium availability
    python = repo / ".venv" / "bin" / "python"
    chromium_check = run([str(python), "-c",
                          "from playwright.sync_api import sync_playwright; sync_playwright().start().chromium.executable_path"],
                         repo)
    if chromium_check.returncode != 0:
        print("prove-tests: UNPROVEN Chromium not installed in .venv")
        return False

    # Find changed files
    diff_out = _git(repo, "diff", "--name-only", f"{base_oid}...{head_oid}", "--", "tests/", runner=runner)
    test_files = [f for f in diff_out.splitlines() if f.startswith("tests/") and f.endswith(".py")]

    code_out = _git(repo, "diff", "--name-only", f"{base_oid}...{head_oid}", "--", "workshop_validator/", runner=runner)
    code_files = [f for f in code_out.splitlines() if f.endswith(".py")]

    if not code_files:
        print("prove-tests: SKIP no code change")
        return True

    # Get the full diff to parse new test ids
    full_diff = _git(repo, "diff", f"{base_oid}...{head_oid}", "--", "tests/", runner=runner)
    test_ids = parse_new_test_ids(full_diff)

    if not test_ids:
        print("prove-tests: SKIP no new tests")
        return True

    # Create temp worktree
    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "prove"
        try:
            _git(repo, "worktree", "add", "--detach", str(tree), head_oid, runner=runner)

            # Restore base version of each code file and log it
            swapped = []
            for code_file in code_files:
                file_path = tree / code_file
                # Check if file exists in base
                base_content = _git(tree, "show", f"{base_oid}:{code_file}", check=False, runner=runner)
                if base_content:
                    # File exists in base, restore it
                    file_path.write_text(base_content, encoding="utf-8")
                    swapped.append(code_file)
                else:
                    # File is new in head, remove it
                    if file_path.exists():
                        file_path.unlink()
                        swapped.append(f"{code_file} (removed, new in head)")

            # Run all tests together with -v to get per-test status
            # Set RUN_BROWSER_SMOKE=1 for browser tests
            old_env = os.environ.get("RUN_BROWSER_SMOKE")
            try:
                os.environ["RUN_BROWSER_SMOKE"] = "1"
                result = run([str(python), "-m", "unittest", "-v"] + test_ids, tree)
            finally:
                if old_env is None:
                    os.environ.pop("RUN_BROWSER_SMOKE", None)
                else:
                    os.environ["RUN_BROWSER_SMOKE"] = old_env

            # Parse output to categorize each test
            stdout_str = result.stdout or ""
            stderr_str = result.stderr or ""
            test_results = parse_unittest_output(stdout_str + stderr_str)

            failed_on_base = []
            passed_on_base = []
            unproven = []

            for test_id in test_ids:
                status = test_results.get(test_id, "MISSING")
                if status in ("FAIL", "ERROR"):
                    failed_on_base.append(test_id)
                elif status == "ok":
                    passed_on_base.append(test_id)
                elif status == "skipped":
                    unproven.append(f"{test_id} (skipped)")
                else:
                    unproven.append(f"{test_id} (status: {status})")

            # Check: need at least one failing test per changed code file
            # Map test files to code files they likely test
            untested_code = []
            for code_file in code_files:
                # Simple heuristic: test_foo.py tests foo.py
                code_name = Path(code_file).stem
                # Look for any failed test that might test this file
                has_failing_test = any(code_name in test_id for test_id in failed_on_base)
                if not has_failing_test:
                    untested_code.append(code_file)

            if unproven:
                print(f"prove-tests: UNPROVEN {len(unproven)} test(s): {', '.join(unproven[:3])}")
                return False

            if untested_code:
                print(f"prove-tests: FAIL no failing test for {', '.join(untested_code)} "
                      f"({len(failed_on_base)} failed / {len(passed_on_base)} passed)")
                return False

            print(f"prove-tests: PASS {len(failed_on_base)} failed / {len(passed_on_base)} passed / "
                  f"{len(unproven)} unproven on base; swapped {len(swapped)} file(s)")
            return True
        finally:
            _git(repo, "worktree", "remove", "--force", str(tree), check=False, runner=runner)


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
    pt = sub.add_parser("prove-tests")
    pt.add_argument("pr", type=int)
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
    if args.cmd == "prove-tests":
        info = _gh_json(repo, "pr", "view", str(args.pr), "--json", "headRefOid,baseRefOid")
        head_oid = info.get("headRefOid") if info else None
        base_oid = info.get("baseRefOid") if info else None
        if not head_oid or not base_oid:
            base_oid = _merge_base(repo, "origin/main")
            head_oid = _git(repo, "rev-parse", "HEAD").strip()
        return 0 if prove_tests(repo, base_oid, head_oid) else 1
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
