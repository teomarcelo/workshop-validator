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


_SHARED_GUARDS_JS = r"""
// Shared guards used by both text and aria-label extraction (each guard exactly once)
const CONTROL_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'OPTION', 'OUTPUT']);
const CONTROL_ROLES = new Set(['textbox', 'combobox', 'searchbox', 'spinbutton', 'listbox', 'option', 'slider']);
const HIDDEN_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
const SKIPPED_INPUT_TYPES = new Set(['hidden', 'submit', 'button', 'reset', 'checkbox', 'radio', 'image', 'file']);

function getRoleTokens(el) {
    const roleAttr = el.getAttribute('role');
    if (!roleAttr) return [];
    const tokens = roleAttr.toLowerCase().split(/\s+/);
    return tokens;
}

function isControl(el) {
    if (CONTROL_TAGS.has(el.tagName)) return true;
    if (el.isContentEditable) return true;
    if (el.ownerDocument && el.ownerDocument.designMode === 'on') return true;
    try {
        const style = window.getComputedStyle(el);
        const userModify = style.webkitUserModify || style.getPropertyValue('-webkit-user-modify');
        if (userModify && userModify !== 'read-only') return true;
    } catch (e) {}
    const tokens = getRoleTokens(el);
    for (const token of tokens) {
        if (CONTROL_ROLES.has(token)) return true;
    }
    return false;
}

function hasControlAncestor(el) {
    let current = el.parentElement;
    if (!current) {
        const root = el.getRootNode();
        if (root && root.host) {
            current = root.host;
        }
    }
    // Follow assignedSlot for light DOM elements slotted into shadow DOM
    if (!current && el.assignedSlot) {
        current = el.assignedSlot;
    }
    while (current) {
        if (isControl(current)) return true;
        const parent = current.parentElement;
        if (parent) {
            current = parent;
        } else {
            const root = current.getRootNode();
            if (root && root.host) {
                current = root.host;
            } else if (current.assignedSlot) {
                current = current.assignedSlot;
            } else {
                break;
            }
        }
    }
    return false;
}

function isHidden(el) {
    if (HIDDEN_TAGS.has(el.tagName)) return true;
    if (el.hasAttribute('hidden')) return true;
    const ariaHidden = el.getAttribute('aria-hidden');
    if (ariaHidden && ariaHidden.toLowerCase() === 'true') return true;
    try {
        if (!el.checkVisibility({visibilityProperty: true})) return true;
    } catch (e) {
        try {
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return true;
        } catch (e2) {}
    }
    return false;
}

function traverse(el, seen) {
    const children = [];
    // Skip SCRIPT, STYLE, TEMPLATE - they never contribute visible text
    const tag = el.tagName;
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TEMPLATE') {
        return children;
    }
    for (const child of el.childNodes) {
        if (child.nodeType === 1 && !seen.has(child)) {
            children.push(child);
        }
    }
    if (el.shadowRoot) {
        for (const child of el.shadowRoot.childNodes) {
            if (child.nodeType === 1 && !seen.has(child)) {
                children.push(child);
            }
        }
    }
    return children;
}

function collectValuesFromPage(doc) {
    const valueSet = new Set();
    const seen = new Set();
    const MAX_VALUES = 300;
    const MIN_LENGTH = 3;
    let capped = false;

    function addValue(val) {
        if (capped) return;
        if (val && typeof val === 'string') {
            const normalized = val.replace(/\s+/g, ' ').trim().toLowerCase();
            if (normalized && normalized.length >= MIN_LENGTH) {
                valueSet.add(normalized);
                if (valueSet.size > MAX_VALUES) {
                    capped = true;
                    valueSet.clear();
                }
            }
        }
    }

    function collectFromElement(elem) {
        if (capped) return;
        if (seen.has(elem)) return;
        seen.add(elem);

        const tag = elem.tagName;
        if (tag === 'INPUT') {
            const type = (elem.getAttribute('type') || 'text').toLowerCase();
            if (!SKIPPED_INPUT_TYPES.has(type)) {
                addValue(elem.value);
            }
        }
        if (tag === 'TEXTAREA') {
            addValue(elem.value);
        }
        if (tag === 'SELECT') {
            for (const option of elem.options) {
                if (option.selected) {
                    addValue(option.textContent);
                }
            }
        }
        const tokens = getRoleTokens(elem);
        // For role=option, only collect if aria-selected="true"
        if (tokens.includes('option')) {
            const ariaSelected = elem.getAttribute('aria-selected');
            if (ariaSelected && ariaSelected.toLowerCase() === 'true') {
                addValue(elem.textContent);
            }
        } else if (isControl(elem) && elem.ownerDocument.designMode !== 'on' && tag !== 'SELECT' && tag !== 'OPTION') {
            addValue(elem.textContent);
        }
        const inputRoles = new Set(['textbox', 'searchbox', 'combobox', 'spinbutton', 'slider']);
        for (const token of tokens) {
            if (inputRoles.has(token)) {
                addValue(elem.textContent);
                addValue(elem.getAttribute('aria-valuetext'));
                break;
            }
        }

        // Use shared traverse to walk light children and shadow roots
        for (const child of traverse(elem, seen)) {
            collectFromElement(child);
        }
    }

    const root = doc.body || doc.documentElement;
    if (root) {
        collectFromElement(root);
    }
    return {values: Array.from(valueSet), capped: capped};
}
"""

_COLLECT_VALUES_JS = _SHARED_GUARDS_JS + r"""
() => {
    return collectValuesFromPage(document);
}
"""

_SAFE_TEXT_JS = _SHARED_GUARDS_JS + r"""
(element, valuesResult) => {
    function containsControl(el, seen) {
        if (isControl(el)) return true;
        for (const child of traverse(el, seen)) {
            seen.add(child);
            if (containsControl(child, seen)) return true;
        }
        return false;
    }

    function collectText(el, seen) {
        if (isHidden(el)) return '';
        let text = '';
        for (const node of el.childNodes) {
            if (node.nodeType === 3) {
                text += node.textContent;
            } else if (node.nodeType === 1) {
                if (!isControl(node)) {
                    text += collectText(node, seen);
                }
            }
        }
        if (el.shadowRoot) {
            for (const node of el.shadowRoot.childNodes) {
                if (node.nodeType === 3) {
                    text += node.textContent;
                } else if (node.nodeType === 1) {
                    if (!isControl(node)) {
                        text += collectText(node, seen);
                    }
                }
            }
        }
        return text;
    }

    if (isControl(element)) return '';
    if (hasControlAncestor(element)) return '';
    const seen = new Set();
    if (containsControl(element, seen)) return '';
    let text = collectText(element, new Set());
    text = text.replace(/\s+/g, ' ').trim();
    if (text.length > 400) return '';

    // Fail closed if value collection capped
    if (valuesResult.capped) return '';

    const nameLower = text.toLowerCase();
    for (const value of valuesResult.values) {
        if (nameLower.includes(value)) {
            return '';
        }
    }
    return text;
}
"""

_ARIA_LABEL_JS = _SHARED_GUARDS_JS + r"""
(element, valuesResult) => {
    if (isControl(element)) return '';
    if (hasControlAncestor(element)) return '';
    const ariaLabel = (element.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!ariaLabel) return '';
    if (ariaLabel.length > 400) return '';

    // Fail closed if value collection capped
    if (valuesResult.capped) return '';

    const ariaLabelLower = ariaLabel.toLowerCase();
    for (const value of valuesResult.values) {
        if (ariaLabelLower.includes(value)) {
            return '';
        }
    }
    return ariaLabel;
}
"""


def _name(item, *, read_text: bool, budget: _Budget, values_result=None) -> str:
    """aria-label or safe text from visible text nodes only.

    aria-label is read only AFTER control and ancestor checks (done in JS).
    Safe text never descends into controls or editable contexts.
    Returns "" if element is or contains a control, or is inside an editable ancestor.
    Value-match: returns "" if the collected name contains any current control value.
    Never reads title (apps mirror values into it).
    Over 400 characters: dropped whole. Python applies redact_text before trimming."""
    # Every read gets a budget-bound timeout; a detached element would otherwise
    # wait the page default (about 20 s).
    if budget.over():
        return ""

    # Collect values once per document if not provided
    if values_result is None:
        try:
            values_result = item.evaluate(_COLLECT_VALUES_JS, timeout=budget.timeout_ms(500))
            if values_result is None:
                values_result = {"values": [], "capped": False}
        except Exception:
            values_result = {"values": [], "capped": False}

    if budget.over():
        return ""

    # Try aria-label first (with control/ancestor checks and value-match)
    try:
        value = str(item.evaluate(_ARIA_LABEL_JS, values_result, timeout=budget.timeout_ms(500)) or "")
        if value:
            return redact_text(value, NAME_LIMIT)
    except Exception:
        pass

    if budget.over():
        return ""

    # Try safe text from visible text nodes (includes value-match and full checks)
    if read_text:
        try:
            value = str(item.evaluate(_SAFE_TEXT_JS, values_result, timeout=budget.timeout_ms(500)) or "")
            if value:
                # Python redacts first, then trims (no pre-trim in Python)
                return redact_text(value, NAME_LIMIT)
        except Exception:
            pass

    return ""


def _headings(panel, budget: _Budget, values_result=None) -> list[str]:
    names = []
    try:
        loc = panel.get_by_role("heading")
    except Exception:
        return names
    for item in _visible(loc, HEADINGS_PER_PANEL * 2, budget):
        name = _name(item, read_text=True, budget=budget, values_result=values_result)
        if name and name not in names:
            names.append(name)
        if len(names) >= HEADINGS_PER_PANEL:
            break
    return names


def _controls_and_panels(roots: list, budget: _Budget) -> tuple[dict[str, list[str]], list[dict]]:
    controls: dict[str, list[str]] = {role: [] for role in CONTROL_ROLES}
    panels: list[dict] = []
    for root in roots[:ROOT_LIMIT]:
        # Collect values once per root
        values_result = None
        try:
            values_result = root.evaluate(_COLLECT_VALUES_JS)
            if values_result is None:
                values_result = {"values": [], "capped": False}
        except Exception:
            values_result = {"values": [], "capped": False}

        for role in CONTROL_ROLES:
            try:
                loc = root.get_by_role(role)
            except Exception:
                continue
            # A dialog's inner text is its whole body; name it by label or heading only.
            for item in _visible(loc, CANDIDATES_PER_ROLE, budget):
                if len(controls[role]) >= NAMES_PER_ROLE or budget.over():
                    break
                name = _name(item, read_text=role != "dialog", budget=budget, values_result=values_result)
                if not name and role == "dialog":
                    heads = _headings(item, budget, values_result=values_result)
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
                    "name": _name(item, read_text=False, budget=budget, values_result=values_result),
                    "headings": _headings(item, budget, values_result=values_result),
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
        # A lookup or Resource Type miss: up to 3 options' tags, roles, classes and nesting,
        # each text node as its length only.
        "option_shapes": list(option_shapes or [])[:3],
    }, leaks)
    text = json.dumps(payload, indent=2)
    if leaks is not None and leaks(text):
        raise ValueError("snapshot still contained a secret")
    _write_private(folder / f"{stem}.json", text.encode("utf-8"))
    return f"{SNAPSHOT_FOLDER}/{stem}.json"
