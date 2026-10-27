"""Fail-closed tracked-file boundary gate for public synthetic CI.

This is defense in depth, not a replacement for secret scanning.
No file contents or private-looking filenames are ever printed.
"""
from pathlib import Path, PurePosixPath
import argparse
import os
import re
import subprocess
import sys

BLOCKED_SUFFIXES = frozenset({
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".har",
    ".trace", ".zip", ".sqlite", ".db", ".pem", ".key",
    ".p12", ".pfx", ".env", ".log", ".mp4", ".webm",
})
BLOCKED_DIRS = frozenset({
    "screenshots", "traces", "recordings", "artifacts",
    "run-output", "run-results", "private-input", "private-output",
})
ALLOWED_SUFFIXES = frozenset({
    ".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".json", ".mdc", ".plist", ".js",
})
# Producer-team memory is private (register, standups, plan, ledger, reviews).
PRIVATE_TEAM_NAMES = frozenset({
    "decisions.md", "now.json", "runs.jsonl", "producer-live", "dashboard.html",
    "idle-since", "nudged-at", ".register-snapshot",
})
PRIVATE_TEAM_DIRS = frozenset({
    "standups", "outbox", "guide-inbox", "inbox", "research", "reviews", "progress",
})
HOME_PATH_PATTERN = re.compile(  # public-safety: pattern
    r"(?:/Users/[^/\s]+/|/home/[^/\s]+/|C:\\Users\\)", re.IGNORECASE  # public-safety: pattern
)  # public-safety: pattern
GUIDE_NAME_PATTERN = re.compile(r"[A-Z]{3}\d{3}_FULL_")  # public-safety: pattern
OID_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def inspect_tracked(root: Path, names: list[str]) -> tuple[str, ...]:
    """Return safe reason codes; never return filenames or file content."""
    issues = []
    for name in names:
        path = PurePosixPath(name)
        if (not name or "\\" in name or path.is_absolute() or
                ".." in path.parts or chr(0) in name):
            issues.append("invalid_path")
            continue
        parts = tuple(part.lower() for part in path.parts)
        filename = parts[-1]
        if (any(part in BLOCKED_DIRS for part in parts[:-1]) or
                filename == ".env" or filename.startswith(".env.") or
                path.suffix.lower() in BLOCKED_SUFFIXES):
            issues.append("private_artifact_path")
            continue
        if (filename in PRIVATE_TEAM_NAMES or
                any(part in PRIVATE_TEAM_DIRS for part in parts[:-1]) or
                (parts[0] == "briefs" and filename != "template.md")):
            issues.append("private_team_file")
            continue
        if path.suffix.lower() not in ALLOWED_SUFFIXES and filename != ".gitignore":
            issues.append("unexpected_file_type")
            continue
        full_path = root.joinpath(*path.parts)
        if full_path.is_symlink():
            issues.append("symlink_not_allowed")
            continue
        try:
            with full_path.open("rb") as source:
                data = source.read(1024 * 1024 + 1)
        except OSError:
            issues.append("unreadable_file")
            continue
        if len(data) > 1024 * 1024 or bytes([0]) in data:
            issues.append("binary_or_oversized")
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            issues.append("non_utf8_file")
            continue
        # Check content line-by-line, skipping pattern definitions
        is_public_safety = (name == "workshop_validator/public_safety.py")
        regex_line_pattern = re.compile(r'^\s*[A-Z_]+\s*=\s*re\.compile\(')
        in_multiline_regex = False
        for line in text.splitlines():
            # Track multi-line regex definitions in public_safety.py
            if is_public_safety and regex_line_pattern.match(line):
                in_multiline_regex = True
            # Honor marker only in public_safety.py on regex assignment lines or continuations
            if (is_public_safety and "# public-safety: pattern" in line and
                    in_multiline_regex):
                # Check if this line ends the regex (has closing paren with marker)
                if line.strip() == ")  # public-safety: pattern":
                    in_multiline_regex = False
                continue
            # Reset multi-line tracking if we're not in a marked continuation
            if in_multiline_regex and "# public-safety: pattern" not in line:
                in_multiline_regex = False
            if HOME_PATH_PATTERN.search(line):
                issues.append("home_path_in_content")
                break
            if GUIDE_NAME_PATTERN.search(line):
                issues.append("guide_name_in_content")
                break
    return tuple(issues)


ALLOWED_BOT_EMAILS = frozenset({
    "noreply@github.com",
    "github-actions[bot]@users.noreply.github.com",
})


def _count_bad_emails(git_log_output: str) -> int:
    """Count non-GitHub-noreply emails in git log output.

    Input format: git log --format=%ae%x00%ce output (NUL-separated author and committer
    emails per commit, newline-separated commits). Empty emails are counted as bad.

    Returns count of bad (non-noreply or empty) emails.
    """
    bad_count = 0
    # Split on newline to get per-commit records
    email_pairs = git_log_output.split('\n')
    for pair in email_pairs:
        if pair:
            # Split on NUL to get author and committer emails
            emails = pair.split('\x00')
            for email in emails:
                # Empty email is bad
                if not email:
                    bad_count += 1
                    continue
                # Allow GitHub noreply emails (user-specific)
                if email.endswith("@users.noreply.github.com") or email in ALLOWED_BOT_EMAILS:
                    continue
                bad_count += 1
    return bad_count


def check_commit_emails(root: Path, base_ref: str = "origin/main", head_ref: str = "HEAD") -> tuple[bool, str]:
    """Check that all commits between base_ref and head_ref have allowed emails."""
    try:
        # Check if base ref exists
        check_base = subprocess.run(
            ["git", "rev-parse", "--verify", base_ref],
            cwd=root, capture_output=True, text=True
        )
        if check_base.returncode != 0:
            return False, f"base ref {base_ref} not found; fetch it first"

        # Get commit range with NUL-separated author and committer emails
        result = subprocess.run(
            ["git", "log", f"{base_ref}..{head_ref}", "--format=%ae%x00%ce"],
            cwd=root, capture_output=True, text=True, check=True
        )

        bad_count = _count_bad_emails(result.stdout)

        if bad_count > 0:
            return False, f"Found {bad_count} non-GitHub noreply email(s)"

        # Count total emails for success message
        email_count = sum(len(pair.split('\x00')) for pair in result.stdout.split('\n') if pair)
        return True, f"All {email_count} commit emails are GitHub noreply or allowed bots"
    except subprocess.CalledProcessError:
        return False, "Could not check commit emails"


def get_main_checkout(root: Path) -> Path | None:
    """Get the main checkout path (first entry from git worktree list, or root if no worktrees)."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=root, capture_output=True, text=True, check=True
        )
        # First worktree entry is the main checkout
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                return Path(line.split(" ", 1)[1])
        # If no worktrees found, the root itself is the main checkout
        return root
    except subprocess.CalledProcessError:
        # git worktree list fails in non-git repos, or the root is the main checkout
        return root


def check_main_clean(root: Path, check_dirty: bool = True) -> tuple[bool, str]:
    """Check that main checkout is on branch main, and optionally check for dirty files.

    Args:
        root: Repository root path
        check_dirty: If True, also check for untracked or modified files (default: True)

    Returns (True, msg) if not in a git repo (skip check for test environments).
    """
    try:
        # Check if this is a git repo at all
        git_check = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=root, capture_output=True, text=True
        )
        if git_check.returncode != 0:
            # Not a git repo, skip check (test environments)
            return True, "Not a git repo, check skipped"

        # Find the main checkout (first worktree entry)
        main_checkout = get_main_checkout(root)
        if main_checkout is None:
            return False, "Could not find main checkout"

        # Check if on branch main or detached
        head_result = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=main_checkout, capture_output=True, text=True
        )
        if head_result.returncode != 0:
            # HEAD is detached, get the SHA
            sha_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=main_checkout, capture_output=True, text=True, check=True
            )
            sha = sha_result.stdout.strip()
            return False, f"Main checkout HEAD is detached at {sha}"

        # Check if the branch is main
        branch = head_result.stdout.strip()
        if branch != "refs/heads/main":
            branch_name = branch.replace("refs/heads/", "")
            return False, f"Main checkout is on branch {branch_name}, not main"

        # Check for modified or untracked files (only if requested)
        if check_dirty:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=main_checkout, capture_output=True, text=True, check=True
            )
            if result.stdout.strip():
                return False, "Main checkout has untracked or modified files"
        return True, "Main checkout is clean"
    except FileNotFoundError:
        return False, "git binary not found"
    except subprocess.CalledProcessError:
        return False, "Could not check main checkout status"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Public safety gate for tracked files and commits.")
    parser.add_argument("--main-clean", action="store_true",
                        help="Also check that main checkout is on main branch and has no untracked or modified files")
    parser.add_argument("--check-emails", action="store_true", default=True,
                        help="Check commit author/committer emails (default: true)")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parents[1]
    try:
        # Check tracked files
        tracked_listing = subprocess.run(
            ["git", "ls-files", "--cached", "-z"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        ).stdout
        tracked_names = [part.decode("utf-8") for part in tracked_listing.split(bytes([0])) if part]
        if not tracked_names:
            raise ValueError("No tracked files")

        # Check untracked, non-ignored files
        untracked_listing = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        ).stdout
        untracked_names = [part.decode("utf-8") for part in untracked_listing.split(bytes([0])) if part]

        all_names = tracked_names + untracked_names
        problems = inspect_tracked(root, all_names)

        if problems:
            print(f"Public safety gate rejected {len(problems)} file(s); review locally", file=sys.stderr)
            return 1

        # Check commit emails
        if args.check_emails:
            # Use PR_HEAD from environment (set in CI on pull_request events) to check
            # the PR's real commits instead of GitHub's synthetic merge commit
            pr_head = os.environ.get("PR_HEAD", "").strip()
            if pr_head:
                # Validate PR_HEAD is a valid 40-hex git oid
                if not OID_PATTERN.match(pr_head):
                    print("Public safety gate: PR_HEAD must be 40 hex characters", file=sys.stderr)
                    return 1
                head_ref = pr_head
            else:
                head_ref = "HEAD"
            email_ok, email_msg = check_commit_emails(root, head_ref=head_ref)
            if not email_ok:
                print(f"Public safety gate: {email_msg}", file=sys.stderr)
                return 1

        # Check main clean if requested
        if args.main_clean:
            clean_ok, clean_msg = check_main_clean(root)
            if not clean_ok:
                print(f"Public safety gate: {clean_msg}", file=sys.stderr)
                return 1

        print(f"Public safety gate passed: {len(all_names)} text files ({len(tracked_names)} tracked, {len(untracked_names)} untracked)")
        return 0

    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeDecodeError):
        print("Public safety gate could not inspect files; failing closed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
