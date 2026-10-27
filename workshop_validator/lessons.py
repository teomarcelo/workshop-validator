"""Record mistakes, findings, and observations for later workshops.

Lessons quote guide text, so they are private. They live under the private
root (``~/.workshop-validator``), never in the repository. Each lesson is
stored once and counted, so repeated runs do not grow the file.

Two kinds are written by the agent team, not by the runner:
``root-cause`` (why a step failed, what fixed it, and the rule that keeps it
from coming back) and ``team`` (a mistake in the team's own process: a RED
test, an auditor veto, a repeated failure, a stalled job). Both are kept
ahead of plain step rows when the store is capped. A root cause or team
lesson seen again is RECURRING: the earlier fix did not hold.
"""

from datetime import date
from pathlib import Path
import argparse
import json
import os
import re
import sys
import tempfile

STORE_NAME = "lessons.json"
TEXT_NAME = "lessons.md"
MAX_ENTRIES = 500
MAX_BYTES = 256 * 1024

_SECRET = re.compile(
    r"(?i)(\bpassword\b\s*[:=]?\s*\S+"
    r"|[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|\b[A-Za-z0-9-]+\.(?:my\.)?(?:salesforce|force|lightning\.force|salesforce-setup)\.com\S*"
    r"|\borgfarm[A-Za-z0-9-]*)"
)
_PAGE = re.compile(r"\s*The page showed:.*$", re.S)
_OLD_LINE = re.compile(r"^- \*\*(\w+)\.\*\* (.*) — ([A-Z_]+)\. (.*)$")
_CASCADE = ("the run froze", "was not run because", "an earlier step", "not run because")
_KEEP_NOT_RUN = ("loading", "still said", "password", "no check")
CAUSE, TEAM = "root-cause", "team"
PINNED = {CAUSE, TEAM}


def store_path(private_root: Path) -> Path:
    return Path(private_root) / STORE_NAME


def lessons_path(private_root: Path) -> Path:
    return Path(private_root) / TEXT_NAME


def sanitize(text: str) -> str:
    cleaned = " ".join((text or "").split())
    return _SECRET.sub("[removed]", cleaned)


def classify(note: str, status: str) -> str | None:
    """Return the lesson kind, or None when the row teaches nothing."""
    low = (note or "").lower()
    if "unexpected error" in low or "traceback" in low:
        return "mistake"
    if status == "PASS":
        return "observation" if "already" in low else None
    if status == "NOT_RUN":
        if any(word in low for word in _CASCADE):
            return None
        return "observation" if any(word in low for word in _KEEP_NOT_RUN) else None
    if status in {"ENV_BLOCKED", "AUTH_BLOCKED"}:
        return "observation"
    if status in {"UI_MISMATCH", "FUNCTIONAL_FAIL", "NEEDS_REVIEW"}:
        return "finding"
    return None


def _key(title: str, status: str, detail: str) -> str:
    root = _PAGE.sub("", detail).lower()
    return "|".join((" ".join(title.lower().split()), status, " ".join(root.split())))


def load_store(private_root: Path) -> list[dict]:
    path = store_path(private_root)
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [item for item in data.get("entries", []) if isinstance(item, dict)]


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def render_text(entries: list[dict]) -> str:
    lines = [
        "# Workshop lessons",
        "",
        "Private. Each lesson is listed once with how often it was seen.",
        "",
    ]
    pinned = [e for e in entries if e["kind"] in PINNED]
    if pinned:
        lines += ["## Root causes and team rules", ""]
        for item in sorted(pinned, key=lambda e: (-int(e["seen"]), e["title"])):
            lines.append(f"- {format_lesson(item)}")
        lines += ["", "## Step lessons", ""]
    for item in sorted(entries, key=lambda e: (-int(e["seen"]), e["title"])):
        if item["kind"] in PINNED:
            continue
        lines.append(
            f"- **{item['kind'].capitalize()}.** {item['title']} — {item['status']}. "
            f"{item['detail']} (seen {item['seen']}×, last {item['last']})"
        )
    return "\n".join(lines) + "\n"


def format_lesson(item: dict) -> str:
    """One line per lesson; root cause and team lessons show fix and rule."""
    if item["kind"] not in PINNED:
        return f"{item['kind']}: {item['title']} — {item['status']}. {item['detail']} ({item['seen']}×)"
    label = "Root cause" if item["kind"] == CAUSE else "Team"
    parts = [f"**{label}.** {item['title']} — {item['detail']}"]
    if item.get("fix"):
        parts.append(f"Fix: {item['fix']}")
    if item.get("never"):
        parts.append(f"Never: {item['never']}")
    if item.get("pr"):
        parts.append(f"PR: {item['pr']}")
    recurring = " RECURRING: the earlier fix did not hold." if int(item["seen"]) > 1 else ""
    return " ".join(parts) + f" (seen {item['seen']}×, last {item['last']}).{recurring}"


def _cap(entries: list[dict]) -> list[dict]:
    ranked = sorted(entries, key=lambda e: (e["kind"] in PINNED, int(e["seen"]), e["last"]), reverse=True)
    ranked = ranked[:MAX_ENTRIES]
    while ranked and len(render_text(ranked).encode("utf-8")) > MAX_BYTES:
        ranked.pop()
    return ranked


def _merge(entries: list[dict], rows, today: str) -> list[dict]:
    index = {_key(e["title"], e["status"], e["detail"]): e for e in entries}
    for text, status, note in rows:
        kind = classify(note, status)
        if kind is None:
            continue
        title = sanitize(text)[:300] or "Untitled step"
        detail = sanitize(_PAGE.sub("", note or ""))[:600] or "No note was recorded."
        key = _key(title, status, detail)
        if key in index:
            index[key]["seen"] = int(index[key]["seen"]) + 1
            index[key]["last"] = today
            continue
        index[key] = {"kind": kind, "title": title, "status": status, "detail": detail,
                      "seen": 1, "first": today, "last": today}
    return list(index.values())


def save_store(private_root: Path, entries: list[dict]) -> Path:
    entries = _cap(entries)
    _write_private(store_path(private_root), json.dumps({"entries": entries}, indent=1) + "\n")
    path = lessons_path(private_root)
    _write_private(path, render_text(entries))
    return path


def record_lessons(private_root: Path, rows, *, today: str | None = None) -> Path:
    """Merge one run's rows (step text, status, note) into the private store."""
    stamp = today or date.today().isoformat()
    return save_store(private_root, _merge(load_store(private_root), rows, stamp))


def record_cause(
    private_root: Path,
    *,
    title: str,
    cause: str,
    fix: str = "",
    never: str = "",
    pr: str = "",
    kind: str = CAUSE,
    today: str | None = None,
) -> dict:
    """Store why something failed, how it was fixed, and the rule to keep.

    The same title and cause again counts as a repeat (RECURRING), and the
    newest fix, rule and PR replace the old ones.
    """
    if kind not in PINNED:
        raise ValueError(f"kind must be one of {sorted(PINNED)}")
    title = sanitize(title)[:300]
    cause = sanitize(cause)[:600]
    if not title or not cause:
        raise ValueError("title and cause are required")
    stamp = today or date.today().isoformat()
    status = "CAUSE" if kind == CAUSE else "TEAM"
    entries = load_store(private_root)
    key = _key(title, status, cause)
    found = next((e for e in entries if _key(e["title"], e["status"], e["detail"]) == key), None)
    if found is None:
        found = {"kind": kind, "title": title, "status": status, "detail": cause,
                 "seen": 0, "first": stamp, "last": stamp}
        entries.append(found)
    found["seen"] = int(found["seen"]) + 1
    found["last"] = stamp
    for name, value in (("fix", fix), ("never", never), ("pr", pr)):
        if value:
            found[name] = sanitize(value)[:600]
    save_store(private_root, entries)
    return found


def read_lessons(private_root: Path, step_text: str | None = None, kind: str | None = None) -> list[dict]:
    """Return stored lessons, filtered by kind and by a step text match."""
    entries = load_store(private_root)
    if kind:
        entries = [e for e in entries if e["kind"] == kind]
    if not step_text:
        return entries
    want = " ".join(sanitize(step_text).lower().split())
    return [e for e in entries if want in e["title"].lower() or e["title"].lower() in want]


def team_rules(private_root: Path) -> list[str]:
    """Every 'never' rule from root causes and team lessons, most seen first."""
    entries = [e for e in load_store(private_root) if e["kind"] in PINNED and e.get("never")]
    entries.sort(key=lambda e: (-int(e["seen"]), e["title"]))
    return [f"{e['never']} ({e['title']}, seen {e['seen']}×)" for e in entries]


def compact_from(private_root: Path, source: Path) -> Path:
    """Fold an old append-only lessons file into the private store."""
    rows = []
    for line in Path(source).read_text(encoding="utf-8").splitlines():
        match = _OLD_LINE.match(line)
        if match:
            rows.append((match.group(2), match.group(3), match.group(4)))
    return record_lessons(private_root, rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show, record or compact private workshop lessons.")
    parser.add_argument("--private-root", default=str(Path.home() / ".workshop-validator"))
    parser.add_argument("--compact-from", help="Old append-only lessons file to fold in.")
    parser.add_argument("--step", help="Show only lessons for this step text.")
    parser.add_argument("--kind", choices=["mistake", "finding", "observation", CAUSE, TEAM],
                        help="Show only lessons of this kind.")
    parser.add_argument("--rules", action="store_true", help="Print every 'never' rule for agents.")
    parser.add_argument("--record", choices=[CAUSE, TEAM], help="Record a root cause or team lesson.")
    parser.add_argument("--title", help="Step text (root cause) or team area, e.g. 'check-fixer'.")
    parser.add_argument("--cause", help="Why it failed.")
    parser.add_argument("--fix", default="", help="What fixed it.")
    parser.add_argument("--never", default="", help="The rule that keeps it from coming back.")
    parser.add_argument("--pr", default="", help="PR link or commit of the fix.")
    args = parser.parse_args(argv)
    root = Path(args.private_root).expanduser()
    if args.compact_from:
        path = compact_from(root, Path(args.compact_from))
        print(f"{len(load_store(root))} lessons in {path}")
        return 0
    if args.record:
        if not args.title or not args.cause:
            parser.error("--record needs --title and --cause")
        item = record_cause(root, title=args.title, cause=args.cause, fix=args.fix,
                            never=args.never, pr=args.pr, kind=args.record)
        print(format_lesson(item))
        return 0
    if args.rules:
        for rule in team_rules(root):
            print(f"- {rule}")
        return 0
    for item in read_lessons(root, args.step, args.kind):
        print(format_lesson(item))
    return 0


if __name__ == "__main__":
    sys.exit(main())
