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


_SAFE_TEXT_JS = r"""
(element) => {
    // Returns visible text from text nodes only, never from controls or editable contexts.
    // Returns "" if element is or contains a control, or is inside an editable ancestor.
    // Value-match: returns "" if the collected name contains any current control value.
    const CONTROL_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'OPTION', 'OUTPUT']);
    const CONTROL_ROLES = new Set(['textbox', 'combobox', 'searchbox', 'spinbutton', 'listbox', 'option', 'slider']);
    const HIDDEN_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
    const SKIPPED_INPUT_TYPES = new Set(['hidden', 'submit', 'button', 'reset', 'checkbox', 'radio', 'image', 'file']);

    function isControl(el) {
        if (CONTROL_TAGS.has(el.tagName)) return true;

        // Check isContentEditable (covers inherited and explicit contenteditable)
        if (el.isContentEditable) return true;

        // Check designMode
        if (el.ownerDocument && el.ownerDocument.designMode === 'on') return true;

        // Check computed -webkit-user-modify (not read-only means editable)
        try {
            const style = window.getComputedStyle(el);
            const userModify = style.webkitUserModify || style.getPropertyValue('-webkit-user-modify');
            if (userModify && userModify !== 'read-only') return true;
        } catch (e) {
            // getComputedStyle can fail on detached elements
        }

        // Check role attribute (case-insensitive, any token in the space-separated list)
        const roleAttr = el.getAttribute('role');
        if (roleAttr) {
            const tokens = roleAttr.toLowerCase().split(/\s+/);
            for (const token of tokens) {
                if (CONTROL_ROLES.has(token)) return true;
            }
        }

        return false;
    }

    function isHidden(el) {
        if (HIDDEN_TAGS.has(el.tagName)) return true;
        if (el.hasAttribute('hidden')) return true;
        if (el.getAttribute('aria-hidden') === 'true') return true;

        // Use checkVisibility for visibility: collapse and other cases
        try {
            if (!el.checkVisibility({visibilityProperty: true})) return true;
        } catch (e) {
            // checkVisibility not supported or element detached, fall back to getComputedStyle
            try {
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return true;
            } catch (e2) {
                // getComputedStyle can fail on detached elements
            }
        }

        return false;
    }

    // Check if element itself is contentEditable or in designMode document
    if (element.isContentEditable) return '';
    if (element.ownerDocument && element.ownerDocument.designMode === 'on') return '';

    // Check if element is inside an editable ancestor, crossing shadow roots
    function hasControlAncestor(el) {
        let current = el.parentElement;
        // If no parent in light DOM, check if element is in a shadow root
        if (!current) {
            const root = el.getRootNode();
            if (root && root.host) {
                current = root.host;
            }
        }
        while (current) {
            if (isControl(current)) return true;
            // Try to climb up in the same tree
            const parent = current.parentElement;
            if (parent) {
                current = parent;
            } else {
                // No parent in this tree, try to cross shadow boundary
                const root = current.getRootNode();
                if (root && root.host) {
                    current = root.host;
                } else {
                    // Reached the top
                    break;
                }
            }
        }
        return false;
    }

    if (hasControlAncestor(element)) return '';

    // Traverse light children AND open shadow root children with a seen-set
    function traverse(el, seen) {
        if (seen.has(el)) return [];
        seen.add(el);

        const children = [];

        // Light children
        for (const child of el.childNodes) {
            if (child.nodeType === 1) {  // ELEMENT_NODE
                children.push(child);
            }
        }

        // Open shadow root children
        if (el.shadowRoot) {
            for (const child of el.shadowRoot.childNodes) {
                if (child.nodeType === 1) {  // ELEMENT_NODE
                    children.push(child);
                }
            }
        }

        return children;
    }

    function containsControl(el, seen) {
        if (isControl(el)) return true;
        for (const child of traverse(el, seen)) {
            if (containsControl(child, seen)) return true;
        }
        return false;
    }

    function collectText(el, seen) {
        if (isHidden(el)) return '';
        let text = '';

        // Collect text nodes and recurse into elements in DOM order (light children)
        for (const node of el.childNodes) {
            if (node.nodeType === 3) {  // TEXT_NODE
                text += node.textContent;
            } else if (node.nodeType === 1) {  // ELEMENT_NODE
                if (!isControl(node)) {
                    text += collectText(node, seen);
                }
            }
        }

        // Collect from shadow root in DOM order
        if (el.shadowRoot) {
            for (const node of el.shadowRoot.childNodes) {
                if (node.nodeType === 3) {  // TEXT_NODE
                    text += node.textContent;
                } else if (node.nodeType === 1) {  // ELEMENT_NODE
                    if (!isControl(node)) {
                        text += collectText(node, seen);
                    }
                }
            }
        }

        return text;
    }

    // Collect all values for value-match
    function collectValues(el, seen) {
        const values = [];

        function addValue(val) {
            if (val && typeof val === 'string') {
                // Lowercase and collapse whitespace
                const normalized = val.replace(/\s+/g, ' ').trim().toLowerCase();
                if (normalized) values.push(normalized);
            }
        }

        function collectFromElement(elem) {
            if (seen.has(elem)) return;
            seen.add(elem);

            const tag = elem.tagName;

            // Input values (excluding certain types)
            if (tag === 'INPUT') {
                const type = (elem.getAttribute('type') || 'text').toLowerCase();
                if (!SKIPPED_INPUT_TYPES.has(type)) {
                    addValue(elem.value);
                }
            }

            // Textarea values
            if (tag === 'TEXTAREA') {
                addValue(elem.value);
            }

            // ContentEditable text
            if (elem.isContentEditable && elem.ownerDocument.designMode !== 'on') {
                addValue(elem.textContent);
            }

            // Selected option text from selects
            if (tag === 'SELECT') {
                for (const option of elem.options) {
                    if (option.selected) {
                        addValue(option.textContent);
                    }
                }
            }

            // Role-based input controls: text and aria-valuetext
            const roleAttr = elem.getAttribute('role');
            if (roleAttr) {
                const tokens = roleAttr.toLowerCase().split(/\s+/);
                const inputRoles = new Set(['textbox', 'searchbox', 'combobox', 'spinbutton']);
                for (const token of tokens) {
                    if (inputRoles.has(token)) {
                        addValue(elem.textContent);
                        addValue(elem.getAttribute('aria-valuetext'));
                        break;
                    }
                }
            }

            // Recurse into children (light children)
            for (const child of elem.childNodes) {
                if (child.nodeType === 1) {
                    collectFromElement(child);
                }
            }

            // Recurse into shadow root
            if (elem.shadowRoot) {
                for (const child of elem.shadowRoot.childNodes) {
                    if (child.nodeType === 1) {
                        collectFromElement(child);
                    }
                }
            }
        }

        collectFromElement(el);
        return values;
    }

    const seen = new Set();
    if (containsControl(element, seen)) return '';

    // Collect text (join text nodes exactly as in the page, then collapse whitespace)
    let text = collectText(element, new Set());
    text = text.replace(/\s+/g, ' ').trim();

    // Over 400 characters: drop whole
    if (text.length > 400) return '';

    // Value-match: if name contains any value, return ""
    const nameLower = text.toLowerCase();
    const values = collectValues(element, new Set());
    for (const value of values) {
        if (nameLower.includes(value)) {
            return '';
        }
    }

    return text;
}
"""


_ARIA_LABEL_JS = r"""
(element) => {
    // For aria-label: apply same checks as text collection (control/ancestor/value-match)
    // Reuse isControl, hasControlAncestor, and collectValues from _SAFE_TEXT_JS
    const CONTROL_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'OPTION', 'OUTPUT']);
    const CONTROL_ROLES = new Set(['textbox', 'combobox', 'searchbox', 'spinbutton', 'listbox', 'option', 'slider']);
    const SKIPPED_INPUT_TYPES = new Set(['hidden', 'submit', 'button', 'reset', 'checkbox', 'radio', 'image', 'file']);

    function isControl(el) {
        if (CONTROL_TAGS.has(el.tagName)) return true;
        if (el.isContentEditable) return true;
        if (el.ownerDocument && el.ownerDocument.designMode === 'on') return true;
        try {
            const style = window.getComputedStyle(el);
            const userModify = style.webkitUserModify || style.getPropertyValue('-webkit-user-modify');
            if (userModify && userModify !== 'read-only') return true;
        } catch (e) {}
        const roleAttr = el.getAttribute('role');
        if (roleAttr) {
            const tokens = roleAttr.toLowerCase().split(/\s+/);
            for (const token of tokens) {
                if (CONTROL_ROLES.has(token)) return true;
            }
        }
        return false;
    }

    function hasControlAncestor(el) {
        let current = el.parentElement;
        // If no parent in light DOM, check if element is in a shadow root
        if (!current) {
            const root = el.getRootNode();
            if (root && root.host) {
                current = root.host;
            }
        }
        while (current) {
            if (isControl(current)) return true;
            // Try to climb up in the same tree
            const parent = current.parentElement;
            if (parent) {
                current = parent;
            } else {
                // No parent in this tree, try to cross shadow boundary
                const root = current.getRootNode();
                if (root && root.host) {
                    current = root.host;
                } else {
                    // Reached the top
                    break;
                }
            }
        }
        return false;
    }

    function collectValues(el, seen) {
        const values = [];
        function addValue(val) {
            if (val && typeof val === 'string') {
                const normalized = val.replace(/\s+/g, ' ').trim().toLowerCase();
                if (normalized) values.push(normalized);
            }
        }
        function collectFromElement(elem) {
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
            if (elem.isContentEditable && elem.ownerDocument.designMode !== 'on') {
                addValue(elem.textContent);
            }
            if (tag === 'SELECT') {
                for (const option of elem.options) {
                    if (option.selected) {
                        addValue(option.textContent);
                    }
                }
            }
            const roleAttr = elem.getAttribute('role');
            if (roleAttr) {
                const tokens = roleAttr.toLowerCase().split(/\s+/);
                const inputRoles = new Set(['textbox', 'searchbox', 'combobox', 'spinbutton']);
                for (const token of tokens) {
                    if (inputRoles.has(token)) {
                        addValue(elem.textContent);
                        addValue(elem.getAttribute('aria-valuetext'));
                        break;
                    }
                }
            }
            for (const child of elem.childNodes) {
                if (child.nodeType === 1) {
                    collectFromElement(child);
                }
            }
            if (elem.shadowRoot) {
                for (const child of elem.shadowRoot.childNodes) {
                    if (child.nodeType === 1) {
                        collectFromElement(child);
                    }
                }
            }
        }
        collectFromElement(el);
        return values;
    }

    // Check if element itself is contentEditable or in designMode document
    if (element.isContentEditable) return '';
    if (element.ownerDocument && element.ownerDocument.designMode === 'on') return '';

    // Check if element is inside a control ancestor (crossing shadow roots)
    if (hasControlAncestor(element)) return '';

    // Get aria-label text
    const ariaLabel = (element.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!ariaLabel) return '';
    if (ariaLabel.length > 400) return '';

    // Value-match on aria-label
    const ariaLabelLower = ariaLabel.toLowerCase();
    const values = collectValues(element, new Set());
    for (const value of values) {
        if (ariaLabelLower.includes(value)) {
            return '';
        }
    }

    return ariaLabel;
}
"""


def _name(item, *, read_text: bool, budget: _Budget) -> str:
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

    # Try aria-label first (with control/ancestor checks and value-match)
    try:
        value = str(item.evaluate(_ARIA_LABEL_JS, timeout=budget.timeout_ms(500)) or "")
        if value:
            return redact_text(value, NAME_LIMIT)
    except Exception:
        pass

    if budget.over():
        return ""

    # Try safe text from visible text nodes (includes value-match and full checks)
    if read_text:
        try:
            value = str(item.evaluate(_SAFE_TEXT_JS, timeout=budget.timeout_ms(500)) or "")
            if value:
                # Python redacts first, then trims (no pre-trim in Python)
                return redact_text(value, NAME_LIMIT)
        except Exception:
            pass

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
