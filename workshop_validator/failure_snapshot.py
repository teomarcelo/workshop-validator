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
_INPUT_MASK = "input, textarea, select, [contenteditable=''], [contenteditable='true']"

_URL = re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
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
    text = _HOST.sub("<host>", text)
    text = _TOKEN.sub("<redacted>", text)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


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
    if budget.over():
        return "skipped (time)"
    try:
        return redact_text(page.title())
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
    reads = [lambda: item.get_attribute("aria-label"), lambda: item.get_attribute("title")]
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


def _controls_and_panels(roots: list, budget: _Budget) -> tuple[dict[str, list[str]], list[dict]]:
    controls: dict[str, list[str]] = {role: [] for role in CONTROL_ROLES}
    panels: list[dict] = []
    for root in roots[:ROOT_LIMIT]:
        for role in CONTROL_ROLES:
            try:
                loc = root.get_by_role(role)
            except Exception:
                continue
            # A dialog's inner text is its whole body; name it by label or heading only.
            for item in _visible(loc, CANDIDATES_PER_ROLE, budget):
                if len(controls[role]) >= NAMES_PER_ROLE or budget.over():
                    break
                name = _name(item, read_text=role != "dialog", budget=budget)
                if not name and role == "dialog":
                    heads = _headings(item, budget)
                    name = heads[0] if heads else ""
                if name and name not in controls[role]:
                    controls[role].append(name)
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
    return controls, panels


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
) -> str:
    """Write the snapshot; return its path relative to RUN_DIR. Raises on a write error."""
    budget = _Budget(clock or time.monotonic, budget_s)
    folder = _private_dir(Path(run_dir) / SNAPSHOT_FOLDER)
    stem = snapshot_stem(index, step_id)
    png_href = ""
    png_error = ""
    title = _title(page, budget)
    tree = _tree(page, redact_tree, budget)
    controls, panels = _controls_and_panels(roots, budget)
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
        "screenshot": png_href or f"unavailable ({png_error or 'empty'})",
        # Set only when a read was skipped because the cap was reached.
        "truncated": TRUNCATED_NOTE if budget.hit else "",
    }, leaks)
    text = json.dumps(payload, indent=2)
    if leaks is not None and leaks(text):
        raise ValueError("snapshot still contained a secret")
    _write_private(folder / f"{stem}.json", text.encode("utf-8"))
    return f"{SNAPSHOT_FOLDER}/{stem}.json"
