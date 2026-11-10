"""One private failure snapshot per non-PASS step (owner-approved 2026-11-05).

Written into the run dir as snapshots/<index>-<step_id>.json and .png, so
triage never needs a diagnostic-only iteration. Only redacted page structure
is kept: the URL path (no host, no query), the title, the redacted
accessibility tree, visible button, link, tab and dialog names, open dialogs
and side panels with their headings, and the step note. Input contents are
never read, and inputs are masked in the screenshot. One snapshot is capped
at BUDGET_S seconds; when hit, what was collected is written with
TRUNCATED_NOTE.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

SNAPSHOT_STATUSES = frozenset({"UI_MISMATCH", "FUNCTIONAL_FAIL", "ENV_BLOCKED", "NEEDS_REVIEW"})
SNAPSHOT_FOLDER = "snapshots"
SNAPSHOT_HREF = re.compile(r"snapshots/\d{3,}-[A-Za-z0-9_.-]+\.json")
TREE_LIMIT = 8000
NAME_LIMIT = 120
NAMES_PER_ROLE = 60
CANDIDATES_PER_ROLE = 80
ROOT_LIMIT = 4
PANEL_LIMIT = 8
HEADINGS_PER_PANEL = 8
BUDGET_S = 8.0
TRUNCATED_NOTE = "snapshot truncated (time)"
CONTROL_ROLES = ("button", "link", "tab", "dialog")
PANEL_ROLES = ("dialog", "alertdialog", "complementary")
REGION_ROLES = ("dialog", "alertdialog", "complementary", "region", "tabpanel", "grid", "table")
REGION_LIMIT = 12
NAMED_TREE_ROLES = frozenset({
    "heading", "region", "dialog", "tab", "tabpanel", "button", "link",
    "row", "cell", "gridcell", "columnheader", "rowheader",
    "listitem", "group", "table", "grid"
})
NAMED_TREE_INPUT_ROLES = frozenset({"textbox", "combobox", "searchbox", "spinbutton"})
NAMED_TREE_NAME_LIMIT = 80
_INPUT_MASK = (
    "input, textarea, select, [contenteditable=''], [contenteditable='true'], "
    "[contenteditable='plaintext-only']"
)
TITLE_MIN_S = 1.0

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
# Dotless addresses (user@localhost) are emails too.
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*")
_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_HOST = re.compile(r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}\b")
_TOKEN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


class _Budget:
    """One snapshot's overall time cap. Every read checks it; no read waits past it."""

    def __init__(self, clock: Callable[[], float], seconds: float):
        self.clock = clock
        self.deadline = clock() + seconds
        self.hit = False

    def left(self) -> float:
        return self.deadline - self.clock()

    def over(self) -> bool:
        if not self.hit and self.left() <= 0:
            self.hit = True
        return self.hit

    def timeout_ms(self, cap_ms: int) -> int:
        return max(1, min(cap_ms, int(self.left() * 1000)))


def redact_text(value: object, limit: int = NAME_LIMIT) -> str:
    """Remove URLs, emails, hostnames and long tokens; collapse whitespace; bound the length."""
    text = " ".join(str(value or "").split())
    text = _URL.sub("<url>", text)
    text = _EMAIL.sub("<email>", text)
    text = _IPV4.sub("<ip>", text)
    text = _HOST.sub("<host>", text)
    text = _TOKEN.sub("<redacted>", text)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _parse_aria_line(line: str) -> tuple[str, str, str] | None:
    """Parse aria_snapshot line into (indent, role, name). None if not an element line.

    Handles Playwright's YAML quoting: when a name contains ': ', Playwright quotes
    the entire line like: - 'row "Field Label: Name"'
    """
    stripped = line.lstrip()
    if not stripped.startswith("- "):
        return None
    if stripped.startswith("- /"):  # property line
        return None
    if stripped.startswith("- text:"):  # text node
        return None

    indent = line[: len(line) - len(stripped)]
    rest = stripped[2:]

    # Handle YAML-quoted line: - 'role "name"' or - "role \"name\""
    if rest.startswith("'"):
        try:
            # Find closing single quote
            end_quote = rest.find("'", 1)
            if end_quote > 0:
                # Extract the content between quotes
                rest = rest[1:end_quote]
        except Exception:
            pass
    elif rest.startswith('"'):
        # For double-quoted outer wrapper, handle escaped inner quotes
        try:
            i = 1
            escaped_content = []
            while i < len(rest):
                if rest[i] == '\\' and i + 1 < len(rest):
                    # Escaped character
                    if rest[i + 1] == '"':
                        escaped_content.append('"')
                        i += 2
                        continue
                    else:
                        escaped_content.append(rest[i])
                        i += 1
                        continue
                if rest[i] == '"':
                    # End of outer quote
                    rest = ''.join(escaped_content)
                    break
                escaped_content.append(rest[i])
                i += 1
        except Exception:
            pass

    # Extract role (first word, before first space)
    space_idx = rest.find(' ')
    if space_idx == -1:
        role = rest.rstrip(":")
        remainder = ""
    else:
        role = rest[:space_idx].rstrip(":")
        remainder = rest[space_idx + 1:]

    # Parse quoted name if present
    name = ""
    if remainder.startswith('"'):
        try:
            # Find the closing quote, handling escapes
            i = 1
            while i < len(remainder):
                if remainder[i] == '\\' and i + 1 < len(remainder):
                    i += 2
                    continue
                if remainder[i] == '"':
                    name = remainder[1:i]
                    break
                i += 1
        except Exception:
            pass

    return indent, role, name


def snapshot_stem(index: int, step_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(step_id or "step"))[:80] or "step"
    return f"{int(index):03d}-{safe_id}"


def _url_path(page) -> str:
    try:
        path = urlparse(str(getattr(page, "url", "") or "")).path
    except Exception:
        return ""
    return redact_text(path, 300)


def _title(page, budget: _Budget) -> str:
    """Read <title> with a timeout bounded by the budget (page.title() takes none)."""
    if budget.over():
        return "skipped (time)"
    if budget.left() < TITLE_MIN_S:
        budget.hit = True
        return "skipped (time)"
    try:
        loc = page.locator("head > title")
        if int(loc.count()) == 0:
            return ""
        return redact_text(loc.first.text_content(timeout=budget.timeout_ms(500)))
    except Exception:
        return "unavailable"


def _tree(page, redact_tree: Callable[..., str], budget: _Budget) -> str:
    if budget.over():
        return "skipped (time)"
    try:
        tree = page.locator("body").aria_snapshot(timeout=budget.timeout_ms(1500))
        return redact_tree(tree, limit=TREE_LIMIT)
    except Exception:
        return "unavailable"


def _named_tree(page, budget: _Budget) -> str:
    """Create aria snapshot with names kept for structural roles, redacted and capped."""
    if budget.over():
        return "skipped (time)"
    try:
        tree = page.locator("body").aria_snapshot(timeout=budget.timeout_ms(1500))
        lines = []
        for line in tree.splitlines():
            parsed = _parse_aria_line(line)
            if parsed is None:
                # Keep non-element lines (text nodes, properties) as-is
                lines.append(line)
                continue

            indent, role, name = parsed

            # Keep names for structural and control roles
            if role in NAMED_TREE_ROLES:
                # Keep the name, redacted and capped
                if name:
                    name_out = redact_text(name, NAMED_TREE_NAME_LIMIT)
                    lines.append(f'{indent}- {role} "{name_out}"')
                else:
                    lines.append(f'{indent}- {role}')
            # Input roles keep their label (name) but never their value
            elif role in NAMED_TREE_INPUT_ROLES:
                # Keep only the name (label), never the value
                if name:
                    name_out = redact_text(name, NAMED_TREE_NAME_LIMIT)
                    lines.append(f'{indent}- {role} "{name_out}"')
                else:
                    lines.append(f'{indent}- {role}')
            else:
                # Other roles: keep structure without name
                lines.append(f'{indent}- {role}')

        result = "\n".join(lines)
        if len(result) > TREE_LIMIT:
            result = result[:TREE_LIMIT - 3] + "..."
        return result
    except Exception:
        return "unavailable"


def _visible(loc, cap: int, budget: _Budget):
    """Yield visible items one at a time, so each is named before the next is read."""
    if budget.over():
        return
    try:
        count = min(int(loc.count()), cap)
    except Exception:
        return
    for i in range(count):
        if budget.over():
            return
        item = loc.nth(i)
        try:
            visible = item.is_visible()
        except Exception:
            continue
        if visible:
            yield item


def _name(item, *, read_text: bool, budget: _Budget) -> str:
    """aria-label, then title, then short inner text. Never an input's value."""
    # Every read gets a budget-bound timeout; a detached element would otherwise
    # wait the page default (about 20 s).
    reads = [
        lambda: item.get_attribute("aria-label", timeout=budget.timeout_ms(500)),
        lambda: item.get_attribute("title", timeout=budget.timeout_ms(500)),
    ]
    if read_text:
        reads.append(lambda: item.inner_text(timeout=budget.timeout_ms(500)))
    for read in reads:
        if budget.over():
            return ""
        try:
            value = " ".join(str(read() or "").split())
        except Exception:
            continue
        if value and len(value) <= 200:
            return redact_text(value)
    return ""


def _headings(panel, budget: _Budget) -> list[str]:
    names = []
    try:
        loc = panel.get_by_role("heading")
    except Exception:
        return names
    for item in _visible(loc, HEADINGS_PER_PANEL * 2, budget):
        name = _name(item, read_text=True, budget=budget)
        if name and name not in names:
            names.append(name)
        if len(names) >= HEADINGS_PER_PANEL:
            break
    return names


def _region_headings(panel, budget: _Budget) -> list[dict]:
    """Get headings with text, level, and tag for a region."""
    headings = []
    try:
        loc = panel.get_by_role("heading")
    except Exception:
        return headings
    for item in _visible(loc, HEADINGS_PER_PANEL * 2, budget):
        try:
            text = _name(item, read_text=True, budget=budget)
            if not text:
                continue
            # Get level from aria-level attribute
            level_str = item.get_attribute("aria-level", timeout=budget.timeout_ms(200))
            level = int(level_str) if level_str and level_str.isdigit() else 0
            # Get tag name
            tag = item.evaluate("el => el.tagName.toLowerCase()", timeout=budget.timeout_ms(200))
            headings.append({"text": text, "level": level, "tag": str(tag or "")})
            if len(headings) >= HEADINGS_PER_PANEL:
                break
        except Exception:
            continue
    return headings


def _regions(roots: list, budget: _Budget) -> list[dict]:
    """Collect visible regions with their structure."""
    regions = []
    for frame_idx, root in enumerate(roots[:ROOT_LIMIT]):
        for role in REGION_ROLES:
            if len(regions) >= REGION_LIMIT or budget.over():
                break
            try:
                loc = root.get_by_role(role)
            except Exception:
                continue
            for item in _visible(loc, REGION_LIMIT, budget):
                if len(regions) >= REGION_LIMIT or budget.over():
                    break
                try:
                    name = _name(item, read_text=False, budget=budget)
                    tag = item.evaluate("el => el.tagName.toLowerCase()", timeout=budget.timeout_ms(200))
                    role_attr = item.get_attribute("role", timeout=budget.timeout_ms(200)) or ""
                    headings = _region_headings(item, budget)
                    regions.append({
                        "role": role,
                        "name": name,
                        "tag": str(tag or ""),
                        "role_attr": role_attr,
                        "frame": frame_idx,
                        "parent": -1,  # Parent tracking would need nested traversal
                        "headings": headings,
                    })
                except Exception:
                    continue
    return regions


def _controls_and_panels(roots: list, budget: _Budget) -> tuple[dict[str, list[str]], list[dict], list[str]]:
    controls: dict[str, list[str]] = {role: [] for role in CONTROL_ROLES}
    panels: list[dict] = []
    truncated_roles: list[str] = []
    for root in roots[:ROOT_LIMIT]:
        for role in CONTROL_ROLES:
            try:
                loc = root.get_by_role(role)
            except Exception:
                continue
            # A dialog's inner text is its whole body; name it by label or heading only.
            candidates_seen = 0
            for item in _visible(loc, CANDIDATES_PER_ROLE, budget):
                candidates_seen += 1
                if len(controls[role]) >= NAMES_PER_ROLE or budget.over():
                    break
                name = _name(item, read_text=role != "dialog", budget=budget)
                if not name and role == "dialog":
                    heads = _headings(item, budget)
                    name = heads[0] if heads else ""
                if name and name not in controls[role]:
                    controls[role].append(name)
            # Track if we hit the cap
            if len(controls[role]) >= NAMES_PER_ROLE and role not in truncated_roles:
                truncated_roles.append(role)
        for role in PANEL_ROLES:
            try:
                loc = root.get_by_role(role)
            except Exception:
                continue
            for item in _visible(loc, PANEL_LIMIT, budget):
                if len(panels) >= PANEL_LIMIT or budget.over():
                    break
                panels.append({
                    "role": role,
                    "name": _name(item, read_text=False, budget=budget),
                    "headings": _headings(item, budget),
                })
    return controls, panels, truncated_roles


def _screenshot(page, roots: list, budget: _Budget) -> bytes:
    masks = []
    for root in roots[:ROOT_LIMIT]:
        try:
            masks.append(root.locator(_INPUT_MASK))
        except Exception:
            continue
    return page.screenshot(type="png", full_page=False, timeout=budget.timeout_ms(4000), mask=masks)


def _guarded(value, leaks: Callable[[str], bool] | None):
    """Replace any string the runner's secret check flags (username, password)."""
    if leaks is None:
        return value
    if isinstance(value, str):
        return "<omitted>" if leaks(value) else value
    if isinstance(value, list):
        return [_guarded(item, leaks) for item in value]
    if isinstance(value, dict):
        return {key: _guarded(item, leaks) for key, item in value.items()}
    return value


def _private_dir(folder: Path) -> Path:
    folder = Path(folder)
    if folder.is_symlink():
        raise OSError("snapshot folder is a symlink")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    folder.chmod(0o700)
    return folder


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o600)


def _distinct_roots(page, roots: list) -> list:
    """The page once, then its child frames. page.frames includes the main frame,
    which is the same document as the page, so it is dropped."""
    try:
        main = page.main_frame
    except Exception:
        main = None
    page_listed = any(root is page for root in roots)
    kept = []
    for root in roots:
        if page_listed and main is not None and root is not page and root == main:
            continue
        if any(root is seen for seen in kept):
            continue
        kept.append(root)
    return kept


def save_failure_snapshot(
    page,
    run_dir: Path,
    index: int,
    step_id: str,
    status: str,
    note: str,
    *,
    roots: list,
    redact_tree: Callable[..., str],
    leaks: Callable[[str], bool] | None = None,
    clock: Callable[[], float] | None = None,
    budget_s: float = BUDGET_S,
    option_shapes: list | None = None,
) -> str:
    """Write the snapshot; return its path relative to RUN_DIR. Raises on a write error."""
    budget = _Budget(clock or time.monotonic, budget_s)
    roots = _distinct_roots(page, roots)
    folder = _private_dir(Path(run_dir) / SNAPSHOT_FOLDER)
    stem = snapshot_stem(index, step_id)
    png_href = ""
    png_error = ""
    title = _title(page, budget)
    tree = _tree(page, redact_tree, budget)
    controls, panels, truncated_roles = _controls_and_panels(roots, budget)

    # New fields: collect only if budget allows
    regions_data = []
    named_tree_data = ""
    if not budget.over():
        regions_data = _regions(roots, budget)
    if not budget.over():
        named_tree_data = _named_tree(page, budget)

    try:
        if budget.over():
            raise TimeoutError("snapshot time used")
        image = _screenshot(page, roots, budget)
        if isinstance(image, (bytes, bytearray)) and image:
            _write_private(folder / f"{stem}.png", bytes(image))
            png_href = f"{SNAPSHOT_FOLDER}/{stem}.png"
    except Exception as exc:
        png_error = type(exc).__name__
    payload = _guarded({
        "step_id": str(step_id),
        "index": int(index),
        "status": str(status),
        "note": redact_text(note, 2000),
        "url_path": _url_path(page),
        "title": title,
        "tree": tree,
        "controls": controls,
        "panels": panels,
        "regions": regions_data,
        "named_tree": named_tree_data,
        "truncated_roles": truncated_roles,
        "screenshot": png_href or f"unavailable ({png_error or 'empty'})",
        # Set only when a read was skipped because the cap was reached.
        "truncated": TRUNCATED_NOTE if budget.hit else "",
        # A lookup or Resource Type miss: up to 3 options' tags, roles, classes and nesting,
        # each text node as its length only.
        "option_shapes": list(option_shapes or [])[:3],
    }, leaks)
    text = json.dumps(payload, indent=2)
    if leaks is not None and leaks(text):
        raise ValueError("snapshot still contained a secret")
    _write_private(folder / f"{stem}.json", text.encode("utf-8"))
    return f"{SNAPSHOT_FOLDER}/{stem}.json"
