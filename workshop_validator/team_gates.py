"""Deterministic merge gates for the producer team. Each prints one summary line.

    python -m workshop_validator.team_gates all [--base origin/main] [--private]
    python -m workshop_validator.team_gates em-dash|decision-rows|register|clock12
    python -m workshop_validator.team_gates prove-tests <pr>   # new tests fail on base, per code file
    python -m workshop_validator.team_gates prove --base <ref> --tests <mod>... [--mutants <json>] [--timeout <s>]
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
import shlex
import stat
import subprocess
import sys
import tempfile
import time

from . import team_kit
from .team_state import _write_private

EM_DASH = "\u2014"
TEXT_SUFFIXES = (".md", ".mdc")
ROW_PATTERN = re.compile(r"^\|\s*(" + team_kit.DECISION_ID_RE + r")\s*\|")
CLOCK24 = re.compile(r"\b([01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?\b(?!\s*(?:AM|PM|am|pm))")
# New saves and format 2 latest copies: any case, and exactly one verdict-like line.
VERDICT = re.compile(r"^VERDICT:\s*(APPROVE|CLEAR|VETO|BLOCK|REQUEST_CHANGES)\b", re.MULTILINE | re.IGNORECASE)
# Legacy copies (no FORMAT line, no history): main's rule, case-sensitive, last match wins.
LEGACY_VERDICT = re.compile(r"^VERDICT:\s*(APPROVE|CLEAR|VETO|BLOCK|REQUEST_CHANGES)\b", re.MULTILINE)
PASSING = {"APPROVE", "CLEAR"}
ALWAYS_REVIEWERS = ("code-reviewer", "safety-scanner")
RULE_PATHS = ("workshop_validator/", ".claude/", ".cursor/", "CLAUDE.md")
OID_PATTERN = re.compile(r"^[0-9a-f]{40}$")
NOREPLY_PATTERN = re.compile(r"[^@\s]+@users\.noreply\.github\.com\Z")
EMAIL_PATTERN = re.compile(r"[^\s@]+@[^\s@]+")
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{40,}")


def _default_runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")


def _redact_stderr(stderr: str) -> str:
    """Redact email addresses and tokens from stderr; strip control chars; return first 5 lines."""
    # Strip control and format characters (Cc and Cf) except newline and tab
    import unicodedata
    stripped = "".join(c for c in stderr
                      if unicodedata.category(c) not in ("Cc", "Cf") or c in "\n\t")
    redacted = EMAIL_PATTERN.sub("<email>", stripped)
    redacted = TOKEN_PATTERN.sub("<redacted>", redacted)
    # Also redact runs of 40+ chars from [A-Za-z0-9+/=._-]
    redacted = re.sub(r"[A-Za-z0-9+/=._-]{40,}", "<redacted>", redacted)
    lines = redacted.splitlines()[:5]
    return "\n".join(lines) if lines else ""


def _printable_name(name: str) -> str:
    """Folder name for printing, through `_redact_stderr`.

    Tabs become spaces. Other control and format characters are stripped,
    except `\\n`, which is kept. U+2028 and U+2029 are not stripped there;
    `splitlines` treats them as line breaks, so they come out as `\\n`.
    Emails, tokens and runs of 40+ token characters are redacted, and only
    the first 5 lines are kept. In practice a name never holds a line break,
    because `parse_worktrees` splits the porcelain output on those first.
    """
    return _redact_stderr(name.replace("\t", " "))


def _git(repo: Path, *args: str, check=True, runner=None) -> str:
    """Run git through `runner(cmd, cwd)` (tests inject a fake); raise on failure when check."""
    result = (runner or _default_runner)(["git", *args], Path(repo))
    if check and result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args],
                                            result.stdout, result.stderr)
    return result.stdout or ""


def is_valid_oid(oid: str | None) -> bool:
    """Validate that oid is exactly 40 hex characters."""
    return bool(oid and OID_PATTERN.match(str(oid).strip().lower()))


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


HISTORY_TS = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00")
HISTORY_VERDICT = re.compile(r"[A-Za-z_]{1,40}")
# A verdict-like line, quoted or decorated too; a format 2 latest copy holds exactly one.
VERDICT_LIKE_LINE = re.compile(r"^\W*VERDICT\s*:", re.MULTILINE | re.IGNORECASE)
SUPERSEDE_HINT = re.compile(r"([A-Z_]{1,40}) at (\S+)")


def is_format_2(content: str) -> bool:
    """True when one of the first five lines of a latest copy is exactly FORMAT: 2."""
    return "FORMAT: 2" in [line.strip() for line in content.splitlines()[:5]]


def legacy_verdict(content: str) -> str:
    """Main's legacy rule: the last case-sensitive VERDICT line, else MISSING."""
    found = LEGACY_VERDICT.findall(content)
    return found[-1] if found else "MISSING"


def _read_review_history(history_path: Path) -> list[dict]:
    """Read every JSON Lines history record, in file (append) order, for all heads.

    Fails closed: raises ValueError (or OSError) for a symlink, a non-regular file,
    invalid UTF-8, no records at all, a line that is not a JSON object, or a record
    without a 40-hex `head`, a microsecond UTC `ts` and a `verdict` word, or with a
    `supersedes` or `reason` that is neither null nor a string. A legacy seed whose old
    copy had no 40-hex head has a null `head` and a string `legacy_head`; it never
    matches a current head.
    """
    fd = os.open(history_path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("history path is not a regular file")
        raw = handle.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"history contains invalid UTF-8: {e}")
    records = []
    for line_no, line in enumerate(text.split("\n"), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, RecursionError):
            raise ValueError(f"history line {line_no} is not valid JSON")
        if not isinstance(record, dict):
            raise ValueError(f"history line {line_no} is not a JSON object")
        head, ts, verdict = record.get("head"), record.get("ts"), record.get("verdict")
        supersedes, reason = record.get("supersedes"), record.get("reason")
        legacy_seed = head is None and isinstance(record.get("legacy_head"), str)
        if not (legacy_seed or (isinstance(head, str) and OID_PATTERN.fullmatch(head))):
            raise ValueError(f"history line {line_no} has no valid head")
        if not (isinstance(ts, str) and HISTORY_TS.fullmatch(ts)):
            raise ValueError(f"history line {line_no} has no valid ts")
        if not (isinstance(verdict, str) and HISTORY_VERDICT.fullmatch(verdict)):
            raise ValueError(f"history line {line_no} has no valid verdict")
        if not (supersedes is None or isinstance(supersedes, str)):
            raise ValueError(f"history line {line_no} has an invalid supersedes")
        if not (reason is None or isinstance(reason, str)):
            raise ValueError(f"history line {line_no} has an invalid reason")
        records.append({"head": head, "ts": ts, "verdict": verdict.upper(),
                        "supersedes": supersedes, "reason": reason})
    if not records:
        raise ValueError("history file is empty")
    return records


def _check_blocking_verdicts(records: list[dict]) -> str | None:
    """Return blocking verdict with full timestamp, or None if not blocked.

    A blocking verdict (VETO, BLOCK, REQUEST_CHANGES, or anything not in PASSING)
    is cleared only by a strictly later record (later position in file, strictly
    later ts) for the same role and head, whose supersedes equals that record's
    exact ts and whose reason is non-empty. One supersede clears one record;
    two records with the same ts need two supersedes.
    """
    # Track which specific record positions have been superseded (one supersede clears one record)
    superseded_positions = set()
    for i, record in enumerate(records):
        sup_ts = record.get("supersedes")
        sup_reason = record.get("reason")
        if sup_ts and sup_reason and sup_reason.strip():
            # Find the earliest not-yet-superseded record with matching timestamp and earlier position
            for j, earlier in enumerate(records[:i]):
                if earlier["ts"] == sup_ts and j not in superseded_positions:
                    # Check strictly later timestamp
                    if record["ts"] > sup_ts:
                        superseded_positions.add(j)
                        break  # One supersede clears one record

    # Check for any non-passing verdict that is not superseded
    for i, record in enumerate(records):
        verdict = record["verdict"]
        if verdict not in PASSING:
            if i not in superseded_positions:
                # Return full timestamp for supersede command
                return f"{verdict} at {record['ts']}"
    return None


def _history_verdict(content: str | None, history_path: Path, head_oid: str) -> str | None:
    """Verdict for a role with a format 2 latest copy or any history file.

    The verdict comes only from history records for the current head, never from
    the latest copy's body. The latest copy must still be for this exact head and
    hold exactly one VERDICT line equal to the newest current-head record's verdict.
    With content None (latest copy missing) only a read error or a block is returned,
    else None.
    """
    if not os.path.lexists(history_path):
        return "BLOCKED: FORMAT: 2 requires history file"
    try:
        records = _read_review_history(history_path)
    except ValueError as e:
        return f"BLOCKED: {e}"
    except OSError as e:
        # strerror only: the exception text would print the private reviews path.
        return f"BLOCKED: cannot read history: {e.strerror or type(e).__name__}"
    current = [record for record in records if record["head"] == head_oid]
    blocking = _check_blocking_verdicts(current)
    if blocking or content is None:
        return blocking
    if not current:
        return "BLOCKED: history has no record for the current head"
    first = next((line.strip() for line in content.splitlines() if line.strip()), "")
    if first != f"HEAD: {head_oid}":
        return "STALE"
    found = VERDICT.findall(content)
    if len(VERDICT_LIKE_LINE.findall(content)) != 1 or len(found) != 1:
        return "BLOCKED: latest copy must hold exactly one VERDICT line"
    newest = current[-1]["verdict"]
    if found[0].upper() != newest:
        return f"BLOCKED: latest copy says {found[0].upper()} but history says {newest}"
    return newest


def review_verdicts(reviews: Path, pr: int, head_oid: str | None = None) -> dict[str, str]:
    """Load review verdicts. If head_oid is given, mark stale reviews as STALE.

    A role with a FORMAT: 2 latest copy, or with any history file, is judged only by
    its history, which must be readable and valid (see `_read_review_history`),
    whatever the FORMAT line says. A legacy role (no FORMAT line and no history)
    behaves as before. History files whose latest copy is missing are scanned too.
    """
    verdicts = {}
    seen_roles = set()
    has_head = bool(head_oid and head_oid.strip())

    for path in sorted(reviews.glob(f"pr-{pr}-*.md")):
        role = path.stem[len(f"pr-{pr}-"):]
        if role.endswith(".history"):
            continue
        seen_roles.add(role)
        history_path = reviews / f"pr-{pr}-{role}.history.md"
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            verdicts[role] = f"BLOCKED: invalid UTF-8 in latest copy: {e}"
            continue
        except OSError as e:
            verdicts[role] = f"BLOCKED: cannot read latest copy: {e.strerror or type(e).__name__}"
            continue
        if has_head and (is_format_2(content) or os.path.lexists(history_path)):
            verdicts[role] = _history_verdict(content, history_path, head_oid)
            continue
        verdict = legacy_verdict(content)
        # Fail closed: empty/missing head_oid or stale review marks PASSING verdicts as STALE
        if verdict in PASSING and (not has_head or is_review_stale(content, head_oid)):
            verdict = "STALE"
        verdicts[role] = verdict

    # History-only roles (latest copy missing): a current-head block, or any read error, blocks.
    if has_head:
        for history_path in sorted(reviews.glob(f"pr-{pr}-*.history.md")):
            role = history_path.name[len(f"pr-{pr}-"):-len(".history.md")]
            if role in seen_roles:
                continue
            seen_roles.add(role)
            blocking = _history_verdict(None, history_path, head_oid)
            if blocking:
                verdicts[role] = blocking

    return verdicts


SECURITY_PREFIXES = (".github/", ".claude/agents/", ".claude/workflows/")
SECURITY_FILES = {".claude/settings.json", ".claude/settings.local.json",
                  "workshop_validator/team_hooks.py", "workshop_validator/team_gates.py",
                  "workshop_validator/team_kit.py", "workshop_validator/watchdog.py",
                  "workshop_validator/public_safety.py", "workshop_validator/team_state.py"}
PRIVACY_FILES = {"workshop_validator/private_runner.py", "workshop_validator/report.py",
                 "workshop_validator/step_board.py", "workshop_validator/automate.py"}


def required_reviewers(changed: list[str]) -> list[str]:
    roles = list(ALWAYS_REVIEWERS)
    if any(name.startswith(RULE_PATHS) for name in changed):
        roles.append("rule-auditor")
    if any(name.startswith(SECURITY_PREFIXES) or name in SECURITY_FILES for name in changed):
        roles.append("security-reviewer")
    if any(name in PRIVACY_FILES for name in changed):
        roles.append("privacy-reviewer")
    return roles


def _review_problem(role: str, verdict: str, pr: int | None, head_oid: str | None) -> str:
    """One review problem; a "VERDICT at <ts>" block also gets a shell-quoted supersede command."""
    hint = SUPERSEDE_HINT.fullmatch(verdict)
    if not (hint and pr and head_oid):
        return f"review {role}: {verdict}"
    cmd = " ".join(["team_kit review", "--pr", shlex.quote(str(pr)), "--role", shlex.quote(role),
                    "--head", shlex.quote(head_oid), "--supersedes", shlex.quote(hint.group(2)),
                    '--reason "..."'])
    return f"review {role}: {verdict}; clear with: {cmd}"


def merge_blockers(checks: list[dict], verdicts: dict[str, str], required: list[str], pr: int | None = None, head_oid: str | None = None) -> list[str]:
    """Return list of blocking problems. For verdicts with timestamps, show supersede command."""
    problems = []
    if not checks:
        problems.append("no CI checks reported")
    for check in checks:
        if check.get("bucket") != "pass" and check.get("bucket") != "skipping":
            problems.append(f"CI {check.get('name')}: {check.get('bucket') or check.get('state')}")
    roles = list(required) + [role for role in verdicts if role not in required]
    for role in roles:
        verdict = verdicts.get(role, "MISSING")
        if verdict not in PASSING:
            problems.append(_review_problem(role, verdict, pr, head_oid))
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
            # Create merge commit with noreply email to pass email check
            merge = run(["git", "-c", "user.email=merged-tree-gates@users.noreply.github.com",
                        "-c", "user.name=merged-tree-gates",
                        "merge", "--no-edit", "--no-ff", oid], tree)
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
    if not head_oid or not is_valid_oid(head_oid):
        print(f"merge-when-green: REFUSED PR #{pr} has no valid headRefOid to pin the merge to")
        return 2
    if not head.startswith("automation/"):
        print("merge-when-green: REFUSED head branch is not automation/*")
        return 2
    # Check that user.email is a noreply address
    user_email_result = run(["git", "config", "user.email"], repo)
    if user_email_result.returncode:
        print("merge-when-green: FAIL cannot read git config user.email")
        return 1
    user_email = user_email_result.stdout.strip()
    # Full-match regex: non-empty local part, no spaces, @users.noreply.github.com
    if not NOREPLY_PATTERN.fullmatch(user_email):
        print("merge-when-green: FAIL user.email must be a noreply address")
        return 1
    checks = _gh_json(repo, "pr", "checks", str(pr), "--json", "name,state,bucket",
                      runner=runner) or []
    changed = [f["path"] for f in info.get("files", [])]
    problems = merge_blockers(checks, review_verdicts(team / "reviews", pr, head_oid),
                              required_reviewers(changed), pr=pr, head_oid=head_oid)
    if problems:
        print(f"merge-when-green: WAIT {'; '.join(problems[:5])}")
        return 1
    # Fetch the base branch too, so the merged-tree gates run against current main.
    base_name = info.get("baseRefName")
    fetched = run(["git", "fetch", "origin", base_name, head], repo)
    if fetched.returncode:
        print(f"merge-when-green: FAIL git fetch origin {base_name} {head} exited {fetched.returncode}")
        return 1
    if not merged_tree_gates(repo, head_oid, runner=runner):
        return 1
    # New or changed tests must fail on base code: base = merge-base(PR base branch, head).
    if not prove_tests(repo, f"origin/{base_name}", head_oid, runner=runner):
        return 1
    if dry_run:
        print(f"merge-when-green: READY PR #{pr} (dry run, not merged)")
        return 0
    merged = run(["gh", "pr", "merge", str(pr), "--squash",
                  "--match-head-commit", head_oid,
                  "--author-email", user_email], repo)
    if merged.returncode:
        # Read PR state to check if it was merged despite non-zero exit
        pr_view = _gh_json(repo, "pr", "view", str(pr), "--json", "state,mergeCommit,headRefOid", runner=runner) or {}
        # Continue only when BOTH state is MERGED AND headRefOid matches reviewed head
        if pr_view.get("state") == "MERGED" and pr_view.get("headRefOid") == head_oid:
            # Continue - PR was merged successfully with correct head
            pass
        else:
            print(f"merge-when-green: FAIL gh pr merge exited {merged.returncode}; PR not merged or head changed")
            if merged.stderr:
                redacted = _redact_stderr(merged.stderr)
                if redacted:
                    for line in redacted.splitlines():
                        print(f"  {line}")
            return 1
    else:
        pr_view = _gh_json(repo, "pr", "view", str(pr), "--json", "mergeCommit,headRefOid", runner=runner) or {}

    sha = str((pr_view.get("mergeCommit") or {}).get("oid") or "").strip().lower()
    # Validate SHA before using it (fail closed). The PR is already merged here.
    if not is_valid_oid(sha):
        print(f"merge-when-green: FAIL PR #{pr} is already merged but the merge commit SHA "
              "cannot be read; check main CI by hand with `gh run list --branch main`")
        return 1

    # Check that the merge commit author email is a noreply address (gh --jq prints plain text)
    author_result = run(["gh", "api", f"repos/{{owner}}/{{repo}}/commits/{sha}",
                        "--jq", ".commit.author.email"], repo)
    author_email = author_result.stdout.strip() if not author_result.returncode else ""
    if not author_email:
        author_problem = "cannot read merge commit author"
    elif not NOREPLY_PATTERN.fullmatch(author_email):
        author_problem = "merge commit has non-noreply author"
    else:
        author_problem = ""
    if author_problem:
        # Still wait for main CI and revert when red (fail closed, still clean up)
        result = wait_main_ci(repo, sha, runner=runner)
        if result == "failure":
            revert_code = revert_by_pr(repo, pr, sha, runner=runner)
            # revert_by_pr prints its own summary line; report the author problem as the FAIL reason
            if revert_code != 0:
                print(f"merge-when-green: FAIL {author_problem} (revert also failed)")
            else:
                print(f"merge-when-green: FAIL {author_problem}")
        elif result == "timeout":
            print(f"merge-when-green: FAIL {author_problem}; main CI not finished; "
                  "check `gh run list --branch main`")
        else:
            print(f"merge-when-green: FAIL {author_problem}")
        return 1

    result = wait_main_ci(repo, sha, runner=runner)
    if result == "success":
        # Delete remote branch after successful merge (only for automation/*)
        if head.startswith("automation/"):
            delete_result = run(["git", "push", f"--force-with-lease=refs/heads/{head}:{head_oid}",
                                "origin", "--delete", head], repo)
            if delete_result.returncode:
                print(f"merge-when-green: MERGED PR #{pr}, main CI green, branch kept")
                return 0
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
    removed, removed_reasons = [], {}
    kept_reasons = {}
    main_tree = repo.resolve()
    # Cache of oids we've already fetched this run (fetch each at most once)
    fetched_oids = set()
    
    def _ensure_oid_available(oid: str) -> bool:
        """Check if oid exists locally, fetch if missing and not yet fetched. Return True when available."""
        if not oid or not is_valid_oid(oid):
            return False
        # Check if oid exists locally
        run = runner or _default_runner
        result = run(["git", "cat-file", "-e", f"{oid}^{{commit}}"], repo)
        if result.returncode == 0:
            return True  # Already local
        # Not local - fetch if we haven't already fetched it this run
        if oid in fetched_oids:
            return False  # Already tried fetching, still not available
        fetched_oids.add(oid)
        fetch_result = _git(repo, "fetch", "origin", oid, check=False, runner=runner)
        # Verify the fetch worked
        verify = run(["git", "cat-file", "-e", f"{oid}^{{commit}}"], repo)
        return verify.returncode == 0
    
    # Get all merged PRs for pr-exact checking (an exact oid match is sound proof)
    all_merged_prs = _gh_json(repo, "pr", "list", "--state", "merged", "--base", "main",
                              "--limit", "200", "--json", "headRefOid", runner=runner) or []

    for item in parse_worktrees(_git(repo, "worktree", "list", "--porcelain", runner=runner)):
        path = Path(item.get("worktree", ""))
        branch = str(item.get("branch", "")).removeprefix("refs/heads/")
        lock_reason = item.get("locked", "")
        if path.resolve() == main_tree or not branch:
            continue
        dirty = _git(path, "status", "--porcelain", check=False, runner=runner).strip() if path.is_dir() else ""
        commit_time = team_kit._branch_commit_time(repo, branch)
        idle = worktree_idle_time(path, commit_time)

        # Query this branch's own PRs for pr-ancestor checking only
        if branch.startswith("worktree-agent-"):
            this_branch_prs = []  # Worktree-agent-* branches don't have named PRs
        else:
            this_branch_prs = _gh_json(repo, "pr", "list", "--state", "merged", "--base", "main",
                                       "--head", branch, "--json", "number,headRefOid", runner=runner) or []

        branch_oid = _git(repo, "rev-parse", branch, check=False, runner=runner).strip() if branch else ""
        safe_to_delete = False
        keep_reason = None
        remove_reason = None

        if lock_reason:
            if isinstance(lock_reason, str):
                pid_match = re.search(r'\bpid (\d+)\b', lock_reason)
                pid = None
                if pid_match:
                    try:
                        pid = int(pid_match.group(1))
                        if pid > 4194304:
                            pid = None
                    except (ValueError, OverflowError):
                        pid = None

                if pid is not None:
                    try:
                        os.kill(pid, 0)
                        keep_reason = "locked"
                    except ProcessLookupError:
                        keep_reason = "stale-lock"
                        print(f"  stale-lock {_printable_name(path.name)} (pid {pid} not running)")
                    except PermissionError:
                        keep_reason = "locked"
                    except (OSError, ValueError, OverflowError):
                        keep_reason = "locked"
                else:
                    keep_reason = "locked"
            else:
                keep_reason = "locked"

        if keep_reason:
            kept_reasons[keep_reason] = kept_reasons.get(keep_reason, 0) + 1
            if dry_run:
                print(f"  keep {_printable_name(path.name)}: {keep_reason}")
            continue

        if dirty:
            keep_reason = "dirty"
        elif idle < idle_hours:
            keep_reason = "recent"
        elif branch_oid and is_valid_oid(branch_oid):
            # pr-exact: Check against ALL merged PRs (sound proof - that commit was reviewed and merged)
            for pr in all_merged_prs:
                pr_oid = pr.get("headRefOid")
                if pr_oid and is_valid_oid(pr_oid) and pr_oid == branch_oid:
                    safe_to_delete = True
                    remove_reason = "pr-exact"
                    break

            # pr-ancestor: Only for THIS branch's own PRs (security constraint)
            if not safe_to_delete and this_branch_prs:
                for pr in this_branch_prs:
                    pr_oid = pr.get("headRefOid")
                    if pr_oid and is_valid_oid(pr_oid) and _ensure_oid_available(pr_oid):
                        # Check if branch_oid is ancestor of pr_oid
                        run = runner or _default_runner
                        result = run(["git", "merge-base", "--is-ancestor", branch_oid, pr_oid], repo)
                        if result.returncode == 0:
                            safe_to_delete = True
                            remove_reason = "pr-ancestor"
                            break

            # merged-pr-ancestor: For worktree-agent-* branches, check if tip is ancestor of any merged PR head
            if not safe_to_delete and branch.startswith("worktree-agent-"):
                for pr in all_merged_prs:
                    pr_oid = pr.get("headRefOid")
                    if pr_oid and is_valid_oid(pr_oid) and _ensure_oid_available(pr_oid):
                        # Check if branch_oid is ancestor of pr_oid
                        run = runner or _default_runner
                        result = run(["git", "merge-base", "--is-ancestor", branch_oid, pr_oid], repo)
                        if result.returncode == 0:
                            safe_to_delete = True
                            remove_reason = "merged-pr-ancestor"
                            break

            # tree-in-main: Check tree match (squash-merges won't be ancestors)
            if not safe_to_delete:
                run = runner or _default_runner
                # Get tree hash of branch
                branch_tree = _git(repo, "rev-parse", f"{branch_oid}^{{tree}}", check=False, runner=runner).strip()
                # Check if this tree exists in origin/main history
                if branch_tree and is_valid_oid(branch_tree):
                    result = run(["git", "log", "origin/main", "--pretty=%T"], repo)
                    if result.returncode == 0 and branch_tree in result.stdout:
                        safe_to_delete = True
                        remove_reason = "tree-in-main"

            if not safe_to_delete:
                keep_reason = "no-proof"

        if keep_reason:
            kept_reasons[keep_reason] = kept_reasons.get(keep_reason, 0) + 1
            if dry_run:
                print(f"  keep {_printable_name(path.name)}: {keep_reason}")
            continue

        if not dry_run:
            run = runner or _default_runner
            remove_result = run(["git", "worktree", "remove", str(path)], repo)
            if remove_result.returncode == 0:
                _git(repo, "branch", "-D", branch, check=False, runner=runner)
                removed.append(path.name)
                if remove_reason:
                    removed_reasons[remove_reason] = removed_reasons.get(remove_reason, 0) + 1
            else:
                kept_reasons["remove-failed"] = kept_reasons.get("remove-failed", 0) + 1
                redacted = _redact_stderr(remove_result.stderr) if remove_result.stderr else ""
                stderr_first = next((line for line in redacted.splitlines() if line.strip()),
                                    "unknown error")
                print(f"  remove-failed {_printable_name(path.name)}: {stderr_first}")
        else:
            # In dry-run, print worktrees that would be removed
            if remove_reason:
                print(f"  remove {_printable_name(path.name)}: {remove_reason}")
            removed.append(path.name)
            if remove_reason:
                removed_reasons[remove_reason] = removed_reasons.get(remove_reason, 0) + 1
    _git(repo, "worktree", "prune", check=False, runner=runner)
    verb = "would remove" if dry_run else "removed"
    reason_str = ", ".join(f"{n} {r}" for r, n in sorted(kept_reasons.items())) if kept_reasons else "none"
    total_kept = sum(kept_reasons.values())
    print(f"worktree-cleanup: {verb} {len(removed)}, kept {total_kept} ({reason_str}), {team_kit.free_gb(repo):.0f} GB free")
    return 0


# ---- prove-tests -----------------------------------------------------------
#
# prove-tests checks that the new or changed tests of a branch actually test its
# code change. Base is merge-base(<base ref>, head). In a temp worktree at head:
#   1. every new or changed test must be ok on head;
#   2. for each changed workshop_validator/*.py file, that ONE file is swapped to
#      its base version (removed when new in head, restored when deleted) while
#      the others stay at head, and at least one new test must fail or error.
# Statuses come from a tiny runner module that writes per-test JSON. Skips,
# expected failures, unexpected successes and missing results are UNPROVEN.
# The runner starts with `python -I`, imports json and unittest first, then
# appends the worktree to the END of sys.path. A PR that adds or changes a root
# file named like a stdlib module is UNPROVEN. Result keys must equal the ids.
# SKIP only when no code file changed; code without a new or changed test is UNPROVEN.

_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")
_CLASS = re.compile(r"^class\s+([A-Za-z_]\w*)")
_TEST_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+(test\w*)\s*\(")
PROOF_STATUSES = ("fail", "error")
RUNNER_NAME = "_prove_tests_runner.py"
STDLIB_SHADOW_NAMES = frozenset({"json.py", "unittest.py", "os.py", "sys.py", "types.py",
                                 "re.py", "io.py", "pathlib.py", "subprocess.py"})
RUNNER_SOURCE = '''"""prove-tests runner: run each test id, write {id: status} JSON. Exit 0 when written.

Started with `python -I`: stdlib json and unittest load before the worktree joins
the END of sys.path, so a root file cannot shadow them.
"""
import json
import unittest
import os
import sys

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.append(HERE)
os.environ["RUN_BROWSER_SMOKE"] = "1"
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ["PYTHONPATH"] = HERE


class JsonResult(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.status = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.status.setdefault(test.id(), "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.status[test.id()] = "fail"

    def addError(self, test, err):
        super().addError(test, err)
        self.status[test.id()] = "error"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.status[test.id()] = "skip"

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.status[test.id()] = "expectedFailure"

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.status[test.id()] = "unexpectedSuccess"

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            failed = issubclass(err[0], test.failureException)
            self.status[test.id()] = "fail" if failed else "error"


def main(argv):
    out, ids = argv[1], argv[2:]
    loader = unittest.TestLoader()
    statuses = {}
    for tid in ids:
        result = JsonResult()
        try:
            loader.loadTestsFromName(tid).run(result)
        except Exception:
            statuses[tid] = "error"
            continue
        if tid in result.status:
            statuses[tid] = result.status[tid]
        elif result.errors or result.failures:
            statuses[tid] = "error"  # import error or class/module fixture error
        else:
            statuses[tid] = "missing"
    package = sys.modules.get("workshop_validator")
    where = os.path.realpath(getattr(package, "__file__", None) or HERE)
    if not where.startswith(HERE + os.sep) and where != HERE:
        print(f"prove-tests runner: workshop_validator imported from {where}, not the worktree")
        return 3
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(statuses, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''
CHROMIUM_CHECK = ("from playwright.sync_api import sync_playwright\n"
                  "p = sync_playwright().start()\n"
                  "try:\n"
                  "    b = p.chromium.launch(headless=True)\n"
                  "    b.close()\n"
                  "finally:\n"
                  "    p.stop()\n")


def parse_new_test_ids(diff: str) -> list[str]:
    """Ids (tests.module.Class.test_x) of test methods a diff adds or changes under tests/.

    The class comes from the class line in the hunk, else from the `@@ ... @@ class X`
    hunk header. A `def test_` line is a test even when it also contains "class ".
    A test is changed only by an added `def test_` line or an added line inside its
    body. Removed lines never mark a test, so a removal-only diff yields no ids.
    """
    ids: list[str] = []
    module = cls = method = None
    method_indent, changed, in_header, in_string = 0, False, False, False

    def flush():
        if module and cls and method and changed:
            tid = f"{module}.{cls}.{method}"
            if tid not in ids:
                ids.append(tid)

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            module = cls = method = None
            in_header, in_string = True, False
            continue
        if in_header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
            module = (path[:-3].replace("/", ".")
                      if path.startswith("tests/") and path.endswith(".py") else None)
            continue
        hunk = _HUNK.match(line)
        if hunk:
            flush()
            in_header, in_string, method, changed = False, False, None, False
            found = _CLASS.match(hunk.group(1))
            cls = found.group(1) if found else None
            continue
        if in_header or not module or not line or line[0] not in " +-":
            continue
        tag, text = line[0], line[1:]
        if tag == "-":
            continue  # removed lines are not in head; they mark no test
        quotes = text.count('"""') + text.count("'''")
        if in_string:
            if tag == "+" and method:
                changed = True
            in_string = quotes % 2 == 0
            continue
        stripped = text.strip()
        indent = len(text) - len(text.lstrip())
        if stripped and not stripped.startswith("#"):
            test_def = _TEST_DEF.match(text)
            if test_def:
                flush()
                method, method_indent, changed = test_def.group(2), indent, tag == "+"
                if indent == 0:
                    cls = method = None
            elif method and indent > method_indent:
                changed = changed or tag == "+"
            else:
                flush()
                method = None
                if indent == 0:
                    found = _CLASS.match(text)
                    cls = found.group(1) if found else None
        elif stripped and method and tag == "+":
            changed = True
        if quotes % 2 == 1:
            in_string = True
    flush()
    return ids


def _run_test_ids(run, python: Path, tree: Path, out_json: Path, ids: list[str]):
    """({id: status}, None) from the runner module, or (None, reason)."""
    if out_json.exists():
        out_json.unlink()
    result = run([str(python), "-I", str(tree / RUNNER_NAME), str(out_json), *ids], tree)
    if result.returncode:
        return None, f"test runner exited {result.returncode}"
    try:
        data = json.loads(out_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "test runner wrote no results"
    if not isinstance(data, dict) or set(data) != set(ids):
        return None, "test runner results do not match the requested tests"
    return {tid: str(data[tid]) for tid in ids}, None


def stdlib_shadow_files(name_only: str) -> list[str]:
    """Root-level paths from `git diff --name-only` named like a guarded stdlib module."""
    return [f for f in name_only.splitlines()
            if f and "/" not in f and f.casefold() in STDLIB_SHADOW_NAMES]


def _counts(statuses: dict[str, str]) -> str:
    tally: dict[str, int] = {}
    for status in statuses.values():
        tally[status] = tally.get(status, 0) + 1
    return ", ".join(f"{n} {s}" for s, n in sorted(tally.items()))


def prove_tests(repo: Path, base_ref: str, head_oid: str, runner=None) -> bool:
    """Prove the new or changed tests fail on base code, one changed code file at a time.

    Prints one line: PASS, FAIL (new tests fail on head), UNPROVEN (no proof, named
    per file) or SKIP (no workshop_validator/*.py file changed).
    """
    run = runner or _default_runner
    merge_base = run(["git", "merge-base", base_ref, head_oid], Path(repo))
    base = (merge_base.stdout or "").strip()
    if merge_base.returncode or not base:
        print(f"prove-tests: UNPROVEN no merge-base of {base_ref} and {head_oid[:10]}")
        return False

    shadows = stdlib_shadow_files(_git(repo, "diff", "--name-only", "--no-renames",
                                       "--diff-filter=d", base, head_oid, runner=runner))
    if shadows:
        print("prove-tests: UNPROVEN root file named like a stdlib module added or changed: "
              + ", ".join(shadows))
        return False

    code_out = _git(repo, "diff", "--name-only", "--no-renames", base, head_oid, "--",
                    "workshop_validator/", runner=runner)
    code_files = [f for f in code_out.splitlines() if f.endswith(".py")]
    if not code_files:
        print("prove-tests: SKIP no code change")
        return True

    test_ids = parse_new_test_ids(_git(repo, "diff", "--no-renames", base, head_oid, "--",
                                       "tests/", runner=runner))
    if not test_ids:
        print(f"prove-tests: UNPROVEN code changed ({', '.join(code_files)}) "
              "but no new or changed test found")
        return False

    python = repo / ".venv" / "bin" / "python"
    if run([str(python), "-c", CHROMIUM_CHECK], Path(repo)).returncode:
        print("prove-tests: UNPROVEN headless Chromium did not launch from .venv")
        return False

    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "prove"
        out_json = Path(tmp) / "results.json"
        try:
            try:
                _git(repo, "worktree", "add", "--detach", str(tree), head_oid, runner=runner)
            except subprocess.CalledProcessError as exc:
                print(f"prove-tests: UNPROVEN git worktree add exited {exc.returncode}")
                return False
            tree.mkdir(parents=True, exist_ok=True)
            (tree / RUNNER_NAME).write_text(RUNNER_SOURCE, encoding="utf-8")

            head, reason = _run_test_ids(run, python, tree, out_json, test_ids)
            if reason:
                print(f"prove-tests: UNPROVEN on head: {reason}")
                return False
            not_ok = {t: s for t, s in head.items() if s != "ok"}
            if not_ok:
                verdict = "FAIL" if all(s in PROOF_STATUSES for s in not_ok.values()) else "UNPROVEN"
                listed = ", ".join(f"{t} ({s})" for t, s in list(not_ok.items())[:3])
                print(f"prove-tests: {verdict} new tests not ok on head: {listed}")
                return False

            proved, unproven = [], []
            for code_file in code_files:
                path = tree / code_file
                head_bytes = path.read_bytes() if path.is_file() else None
                shown = run(["git", "show", f"{base}:{code_file}"], tree)
                try:
                    if shown.returncode == 0:  # exists on base, even when empty
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(shown.stdout or "", encoding="utf-8")
                    elif path.exists():  # new in head
                        path.unlink()
                    statuses, reason = _run_test_ids(run, python, tree, out_json, test_ids)
                finally:
                    if head_bytes is None:
                        if path.exists():
                            path.unlink()
                    else:
                        path.write_bytes(head_bytes)
                if reason:
                    unproven.append(f"{code_file} ({reason})")
                    continue
                failed = [t for t, s in statuses.items() if s in PROOF_STATUSES]
                if failed:
                    proved.append(f"{code_file} {len(failed)}/{len(test_ids)} fail on base")
                else:
                    unproven.append(f"{code_file} 0 fail on base ({_counts(statuses)})")

            if unproven:
                print(f"prove-tests: UNPROVEN per file: {'; '.join(unproven + proved)}")
                return False
            print(f"prove-tests: PASS {len(test_ids)} new or changed test(s) ok on head; "
                  f"per file: {'; '.join(proved)}")
            return True
        finally:
            _git(repo, "worktree", "remove", "--force", str(tree), check=False, runner=runner)
            _git(repo, "worktree", "prune", check=False, runner=runner)


# ---- prove -----------------------------------------------------------------

PROVE_TIMEOUT = 600
PROVE_MAX_NAMES = 10
TEST_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
PROVE_RUNNER_SOURCE = '''"""prove runner: run the named tests in one tree, write JSON results. Exit 0 when written.

argv: out_json tree name...  Writes {"tests": {id: status}, "collection": [name...]}.
A name that cannot be loaded (import or other error at collection) goes in "collection".
"""
import json
import os
import sys
import unittest

out, tree, names = sys.argv[1], os.path.realpath(sys.argv[2]), sys.argv[3:]
sys.path.insert(0, tree)


class Result(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.status = {}

    def addSuccess(self, test):
        super().addSuccess(test)
        self.status.setdefault(test.id(), "ok")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.status[test.id()] = "fail"

    def addError(self, test, err):
        super().addError(test, err)
        self.status[test.id()] = "error"

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.status[test.id()] = "skip"

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.status[test.id()] = "expectedFailure"

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.status[test.id()] = "unexpectedSuccess"

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.status[test.id()] = "fail" if issubclass(err[0], test.failureException) else "error"


def flat(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flat(item)
        else:
            yield item


tests, collection = {}, []
for name in names:
    loader = unittest.TestLoader()
    try:
        suite = loader.loadTestsFromName(name)
    except Exception:
        collection.append(name)
        continue
    items = list(flat(suite))
    if loader.errors or any(type(t).__name__ == "_FailedTest" for t in items):
        collection.append(name)
        continue
    parts, where = name.split("."), ""
    for size in range(len(parts), 0, -1):
        found = getattr(sys.modules.get(".".join(parts[:size])), "__file__", None)
        if found:
            where = os.path.realpath(found)
            break
    if not where.startswith(tree + os.sep):
        collection.append(name)
        continue
    result = Result()
    suite.run(result)
    for test in items:
        tests[test.id()] = result.status.get(test.id(), "error")
    for test, _ in result.errors:
        if type(test).__name__ == "_ErrorHolder":  # class or module fixture error
            tests.setdefault(test.id(), "error")
with open(out, "w", encoding="utf-8") as fh:
    json.dump({"tests": tests, "collection": collection}, fh)
'''


class ProveError(Exception):
    """A prove phase cannot go on; the message is the reason printed after PROVE: FAIL."""


def _prove_fail(reason: str) -> bool:
    print(f"PROVE: FAIL {reason}")
    return False


def _prove_git(repo: Path, *args: str, timeout: float) -> str:
    """git with a hard limit; raises CalledProcessError or TimeoutExpired."""
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                            errors="replace", timeout=timeout)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, ["git", *args],
                                            result.stdout, result.stderr)
    return result.stdout or ""


def _load_mutants(repo: Path, mutants_file: str, timeout: float) -> list[dict]:
    """Read and check every mutant path, before anything is written anywhere."""
    try:
        mutants = json.loads(Path(mutants_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ProveError("cannot read mutants file") from None
    if not isinstance(mutants, list) or not mutants:
        raise ProveError("mutants file is not a non-empty list")
    for mutant in mutants:
        if not (isinstance(mutant, dict) and all(isinstance(mutant.get(k), str)
                                                 for k in ("file", "find", "replace"))):
            raise ProveError("mutant needs string file, find and replace")
        name = mutant["file"]
        parts = Path(name).parts
        if not name or Path(name).is_absolute() or name.startswith(("/", "\\", "-")) \
                or ".." in parts or "\\" in name:
            raise ProveError("bad mutant path")
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "--", name], cwd=repo,
                                 capture_output=True, text=True, errors="replace", timeout=timeout)
        if tracked.returncode:
            raise ProveError("bad mutant path")
    return mutants


def _prove_run(tree: Path, runner_file: Path, out_json: Path, tests: list[str],
               timeout: float) -> tuple[dict, list]:
    """({id: status}, [names that failed at collection]) for the named tests in tree."""
    if out_json.exists():
        out_json.unlink()
    env = {**os.environ, "RUN_BROWSER_SMOKE": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run([sys.executable, "-I", "-B", str(runner_file), str(out_json),
                             str(tree), *tests], cwd=tree, capture_output=True, text=True,
                            errors="replace", env=env, timeout=timeout)
    if result.returncode:
        raise ProveError(f"test runner exited {result.returncode}")
    try:
        data = json.loads(out_json.read_text(encoding="utf-8"))
        statuses, collection = dict(data["tests"]), list(data["collection"])
    except (OSError, ValueError, KeyError, TypeError):
        raise ProveError("test runner wrote no results") from None
    return {str(k): str(v) for k, v in statuses.items()}, [str(n) for n in collection]


def _add_tree(repo: Path, tree: Path, oid: str, timeout: float) -> None:
    _prove_git(repo, "worktree", "add", "--detach", str(tree), oid, timeout=timeout)


def _drop_tree(repo: Path, tree: Path) -> None:
    for args in (("worktree", "remove", "--force", str(tree)), ("worktree", "prune")):
        try:
            subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


def _prove_phases(repo: Path, base_ref: str, tests: list[str], mutants_file: str | None,
                  timeout: float) -> bool:
    if base_ref.startswith("-") or not base_ref.strip():
        return _prove_fail("bad --base")
    if not tests or any(not TEST_NAME.match(name) for name in tests):
        return _prove_fail("bad --tests name")
    if _prove_git(repo, "status", "--porcelain", timeout=timeout).strip():
        return _prove_fail("uncommitted changes")
    head = _prove_git(repo, "rev-parse", "--verify", "HEAD^{commit}", timeout=timeout).strip()
    base = _prove_git(repo, "rev-parse", "--verify", "--end-of-options", base_ref + "^{commit}",
                      timeout=timeout).strip()
    mutants = _load_mutants(repo, mutants_file, timeout) if mutants_file else []
    print(f"prove: head {head}")
    print(f"prove: base {base}")

    with tempfile.TemporaryDirectory(prefix="prove-") as tmp_root:
        os.chmod(tmp_root, 0o700)
        tmp = Path(tmp_root)
        runner_file, out_json = tmp / "runner.py", tmp / "results.json"
        runner_file.write_text(PROVE_RUNNER_SOURCE, encoding="utf-8")

        # (a) head: every named test passes, none skipped
        tree = tmp / "head"
        try:
            _add_tree(repo, tree, head, timeout)
            statuses, collection = _prove_run(tree, runner_file, out_json, tests, timeout)
        finally:
            _drop_tree(repo, tree)
        if collection:
            return _prove_fail(f"tests did not load on head: {', '.join(collection[:PROVE_MAX_NAMES])}")
        if not statuses:
            return _prove_fail("no tests ran on head")
        skipped = sorted(t for t, s in statuses.items() if s == "skip")
        if skipped:
            return _prove_fail(f"{len(skipped)} test(s) skipped on head: "
                               f"{', '.join(skipped[:PROVE_MAX_NAMES])}")
        bad = sorted(t for t, s in statuses.items() if s != "ok")
        if bad:
            return _prove_fail(f"{len(bad)} test(s) failed on head: {', '.join(bad[:PROVE_MAX_NAMES])}")
        print(f"prove: {len(statuses)} test(s) pass on head")

        # (b) base code with the head's test modules: at least one test fails
        tree = tmp / "base"
        try:
            _add_tree(repo, tree, base, timeout)
            for name in tests:
                module = name
                while module:
                    rel = module.replace(".", "/") + ".py"
                    shown = subprocess.run(["git", "show", f"{head}:{rel}"], cwd=repo,
                                           capture_output=True, timeout=timeout)
                    if not shown.returncode:
                        target = tree / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(shown.stdout)
                        break
                    module = module.rpartition(".")[0]
            statuses, collection = _prove_run(tree, runner_file, out_json, tests, timeout)
        finally:
            _drop_tree(repo, tree)
        failing = sorted(t for t, s in statuses.items() if s in ("fail", "error")) + collection
        if not failing:
            return _prove_fail("tests pass on base")
        print(f"prove: {len(failing)} test(s) fail on base: {', '.join(failing[:PROVE_MAX_NAMES])}")

        # (c) mutants on the head commit: each must be killed by a real test failure
        for number, mutant in enumerate(mutants, 1):
            tree = tmp / f"mutant{number}"
            try:
                _add_tree(repo, tree, head, timeout)
                target = tree / mutant["file"]
                if not target.resolve().is_relative_to(tree.resolve()) or not target.is_file():
                    return _prove_fail("bad mutant path")
                text = target.read_text(encoding="utf-8")
                if not mutant["find"] or mutant["find"] not in text:
                    return _prove_fail(f"mutant did not apply: {number}")
                target.write_text(text.replace(mutant["find"], mutant["replace"], 1), encoding="utf-8")
                statuses, collection = _prove_run(tree, runner_file, out_json, tests, timeout)
            finally:
                _drop_tree(repo, tree)
            killed = sorted(t for t, s in statuses.items() if s in ("fail", "error"))
            if killed:
                print(f"prove: mutant {number} killed by {', '.join(killed[:PROVE_MAX_NAMES])}")
            elif collection:
                return _prove_fail(f"mutant broke import: {number}")
            else:
                return _prove_fail(f"mutant survived: {number}")
        if mutants:
            print(f"prove: {len(mutants)} mutant(s) killed")
    print("PROVE: PASS")
    return True


def prove_command(repo: Path, base_ref: str, tests: list[str], mutants_file: str | None = None,
                  timeout: float = PROVE_TIMEOUT) -> bool:
    """Prove the named tests: pass on HEAD, fail on base, and kill every mutant.

    Every phase runs from the one HEAD commit, with the running interpreter.
    The last line is always `PROVE: PASS` or `PROVE: FAIL <reason>`; it never raises.
    """
    try:
        return _prove_phases(Path(repo), base_ref, list(tests), mutants_file, timeout)
    except ProveError as error:
        return _prove_fail(str(error))
    except subprocess.TimeoutExpired:
        return _prove_fail("timeout")
    except subprocess.CalledProcessError as error:
        verb = error.cmd[1] if isinstance(error.cmd, list) and len(error.cmd) > 1 else "git"
        detail = _redact_stderr(error.stderr or "").splitlines()
        return _prove_fail(f"git {verb} failed" + (f": {detail[0]}" if detail else ""))
    except (OSError, UnicodeError):
        return _prove_fail("os error")


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
    prove_parser = sub.add_parser("prove")
    prove_parser.add_argument("--base", required=True, help="Base ref to compare against")
    prove_parser.add_argument("--tests", required=True, nargs="+", help="Test modules to run")
    prove_parser.add_argument("--mutants", help="JSON file with mutants")
    prove_parser.add_argument("--timeout", type=float, default=PROVE_TIMEOUT,
                              help="Seconds allowed for each git or test run")
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
        info = _gh_json(repo, "pr", "view", str(args.pr), "--json", "headRefOid,baseRefName") or {}
        head_oid = str(info.get("headRefOid") or "").strip()
        base_name = str(info.get("baseRefName") or "").strip()
        if not head_oid or not base_name:
            print(f"prove-tests: UNPROVEN cannot read head and base of PR #{args.pr}")
            return 1
        return 0 if prove_tests(repo, f"origin/{base_name}", head_oid) else 1
    if args.cmd == "prove":
        return 0 if prove_command(repo, args.base, args.tests, args.mutants, args.timeout) else 1
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
