"""Fail-closed tracked-file boundary gate for public synthetic CI.

This is defense in depth, not a replacement for secret scanning.
No file contents or private-looking filenames are ever printed.
"""
from pathlib import Path, PurePosixPath
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
    ".cfg", ".json",
})


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
            data.decode("utf-8")
        except UnicodeDecodeError:
            issues.append("non_utf8_file")
    return tuple(issues)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        listing = subprocess.run(
            ["git", "ls-files", "--cached", "-z"], cwd=root,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
        ).stdout
        names = [part.decode("utf-8") for part in listing.split(bytes([0])) if part]
        if not names:
            raise ValueError("No tracked files")
        problems = inspect_tracked(root, names)
    except (OSError, ValueError, subprocess.CalledProcessError, UnicodeDecodeError):
        print("Public safety gate could not inspect tracked files; failing closed")
        return 1
    if problems:
        print(f"Public safety gate rejected {len(problems)} tracked file(s); review locally")
        return 1
    print(f"Public safety gate passed: {len(names)} tracked text files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
