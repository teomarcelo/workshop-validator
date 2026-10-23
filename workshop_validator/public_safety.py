"""Fail-closed tracked-file boundary gate for public synthetic CI.

This is defense in depth, not a replacement for secret scanning.
No file contents or private-looking filenames are ever printed.
"""
from pathlib import Path, PurePosixPath
import re
import subprocess

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
    ".cfg", ".json", ".mdc", ".plist",
})
HOME_PATH_PATTERN = re.compile(  # public-safety: pattern
    r"(?:/Users/[^/\s]+/|/home/[^/\s]+/|C:\\Users\\)", re.IGNORECASE  # public-safety: pattern
)  # public-safety: pattern
GUIDE_NAME_PATTERN = re.compile(r"[A-Z]{3}\d{3}_FULL_")  # public-safety: pattern


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


def main() -> int:
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
    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeDecodeError):
        print("Public safety gate could not inspect files; failing closed")
        return 1
    if problems:
        print(f"Public safety gate rejected {len(problems)} file(s); review locally")
        return 1
    print(f"Public safety gate passed: {len(all_names)} text files ({len(tracked_names)} tracked, {len(untracked_names)} untracked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
