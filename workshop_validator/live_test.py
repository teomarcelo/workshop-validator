"""Live Test waits for an Agentforce preview.

A spinner, the word Loading, "Nothing here yet", or an empty reply is not a
failure. FAIL is recorded only after those signals are gone and the expected
result is still missing. A page that is still loading at the time limit is
not a failure.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable


LIVE_TEST_POLL_SECONDS = 2
LIVE_TEST_TIMEOUT_SECONDS = 90

_VALIDATION_ERROR = re.compile(
    r"Validation failed for action\(s\) .+? invalid attribute value for ['\"‘’]source['\"‘’]\."
    r"(?: Please specify a source when using an OOTB target\.)?",
    re.I,
)
_BLOCK_ERRORS = (
    _VALIDATION_ERROR,
    re.compile(r"We found errors in the Agent Script\.[^.]*\.", re.I),
    re.compile(
        r"Your agent has an error\.?(?: Preview is unavailable until you fix the error and save your agent\.)?",
        re.I,
    ),
    re.compile(r"Preview is unavailable until you fix the error and save your agent\.", re.I),
)
_AGENT_ERROR = re.compile(r"Sorry, I encountered an error\.")
_CHROME_EXACT = {
    "live test",
    "trace",
    "variables",
    "summary",
    "context variables",
    "agent preview details",
    "send a message",
    "let's chat!",
    "let's chat",
    "preview",
    "agentforce",
}
_MARK_PREVIEW_JS = """
(el) => {
  const parentOf = (node) => {
    if (!node) return null;
    if (node.parentElement) return node.parentElement;
    const root = node.getRootNode();
    return root && root.host ? root.host : null;
  };
  const shadowText = (node) => {
    let out = "";
    const visit = (n) => {
      if (!n) return;
      if (n.nodeType === 3) {
        out += n.textContent || "";
        return;
      }
      if (n.nodeType !== 1 && n.nodeType !== 11) return;
      const kids = n.childNodes || [];
      for (let i = 0; i < kids.length; i++) visit(kids[i]);
      if (n.shadowRoot) visit(n.shadowRoot);
    };
    visit(node);
    return out;
  };
  const clear = (node) => {
    if (!node) return;
    if (node.nodeType === 1) {
      node.removeAttribute("data-walk-preview");
      node.removeAttribute("data-walk-column");
    }
    const kids = node.children || [];
    for (let i = 0; i < kids.length; i++) clear(kids[i]);
    if (node.shadowRoot) clear(node.shadowRoot);
  };
  clear(document.documentElement);
  let column = null;
  let preview = el;
  let node = el;
  for (let i = 0; i < 40 && node; i++) {
    const next = parentOf(node);
    if (!next || next === document.body || next === document.documentElement) break;
    const blob = shadowText(next);
    if (blob.includes("Ask for help or describe what you'd like to build")) break;
    preview = next;
    if (!column && blob.includes("Live Test")) column = next;
    node = next;
  }
  if (!column) column = preview;
  preview.setAttribute("data-walk-preview", "1");
  column.setAttribute("data-walk-column", "1");
  return true;
}
"""


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def extract_real_error(*chunks: str, include_agent: bool = False) -> str | None:
    """Return a stable validation error. Agent error text comes only from a reply."""
    flat = _flat("\n".join(chunk for chunk in chunks if chunk))
    if not flat:
        return None
    found = []
    for pattern in _BLOCK_ERRORS:
        match = pattern.search(flat)
        if match:
            found.append(_flat(match.group(0)))
    if include_agent and _AGENT_ERROR.search(flat):
        found.append("Sorry, I encountered an error.")
    unique = []
    for item in found:
        if any(item != other and item in other for other in found):
            continue
        if item not in unique:
            unique.append(item)
    for item in unique:
        if item.lower().startswith("validation failed"):
            return item
    return " ".join(unique) or None


def _has_lets_chat(text: str) -> bool:
    return "let's chat" in (text or "").lower().replace("\u2019", "'")


def _is_chrome_line(line: str) -> bool:
    low = line.strip().lower().replace("\u2019", "'")
    if not low or low in _CHROME_EXACT:
        return True
    if "agentforce development agent" in low or "ask for help or describe" in low:
        return True
    if low.startswith("agentforce avatar responded"):
        return True
    prefixes = (
        "describe your task",
        "send a message in the preview",
        "once there's activity",
        "once there\u2019s activity",
        "session:",
        "session #",
        "nothing here yet",
        "ask for help",
        "saved at",
        "ask agentforce",
    )
    return any(low.startswith(prefix) for prefix in prefixes)


def chat_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if _is_chrome_line(line):
            continue
        lines.append(line)
    return lines


def empty_reply_from_aria(snapshot: str) -> bool:
    """True when an avatar in the transcript has no text before the composer."""
    if not snapshot:
        return False
    items = []
    for raw in snapshot.splitlines():
        line = raw.strip()
        if not line.startswith("-"):
            continue
        line = line.lstrip("-").strip()
        lower = line.lower()
        if lower.startswith("text:"):
            value = line.split(":", 1)[1].strip().strip("\"'")
            items.append("text" if value else "empty")
        elif lower.startswith(("paragraph:", "listitem:")):
            value = line.split(":", 1)[1].strip().strip("\"'")
            if value and not _is_chrome_line(value):
                items.append("text")
        elif lower.startswith("textbox") or "describe your task" in lower:
            items.append("composer")
            break
        elif lower.startswith(("img", "image", "graphic")) or "avatar" in lower:
            items.append("avatar")
        elif lower.startswith(("button", "tab", "link", "heading", "separator", "combobox", "checkbox")):
            items.append("chrome")
    content = [kind for kind in items if kind != "chrome"]
    pending = False
    for kind in content:
        if kind == "avatar":
            if pending:
                return True
            pending = True
        elif kind == "text":
            pending = False
        elif kind in ("empty", "composer"):
            if pending:
                return True
    return False


def _is_typing_placeholder(text: str) -> bool:
    stripped = (text or "").strip()
    return not stripped or set(stripped) <= set(".…• ")


def _reply_lines(text: str) -> list[str]:
    lines = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        low = line.lower()
        if not line or low.startswith("agentforce avatar responded"):
            continue
        if line == "Hello! I'm a simple agent here to say hi.":
            continue
        if _is_typing_placeholder(line):
            continue
        lines.append(line)
    return lines


def _reply_text(obs: dict, reply_after: str | None) -> str | None:
    if not reply_after:
        return None
    needle = reply_after.strip()
    bubbles = obs.get("bubbles") or []
    if bubbles:
        indices = [i for i, bubble in enumerate(bubbles) if (bubble.get("text") or "").strip() == needle]
        if not indices:
            return None
        following = bubbles[indices[-1] + 1:]
        if any(bubble.get("pending") for bubble in following):
            return None
        reply = "\n".join(
            line for bubble in following for line in _reply_lines(bubble.get("text") or "")
        ).strip()
        if not reply or extract_real_error(reply):
            return None
        return reply
    messages = [line for line in (obs.get("messages") or []) if line]
    indices = [i for i, line in enumerate(messages) if line == needle]
    if not indices:
        return None
    last = indices[-1]
    if last + 1 < len(messages):
        reply = "\n".join(_reply_lines("\n".join(messages[last + 1:]))).strip()
    elif len(indices) >= 2:
        reply = "\n".join(_reply_lines(messages[last])).strip()
    else:
        reply = ""
    if not reply or extract_real_error(reply):
        return None
    return reply


def bubbles_from_aria(snapshot: str) -> list[dict]:
    """Chat bubbles inside Live Test. A status with no text is the typing indicator."""
    if not snapshot or "Chat History" not in snapshot:
        return []
    bubbles = []
    in_history = False
    history_indent = 0
    current = None

    def close():
        nonlocal current
        if current is None:
            return
        if current["text"]:
            current["pending"] = False
        elif current["who"] == "agent" or current["pending"]:
            current["pending"] = True
        bubbles.append(current)
        current = None

    for raw in snapshot.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        body = line[1:].strip() if line.startswith("-") else line
        if body.startswith('region "Chat History"') or body.startswith("region 'Chat History'"):
            in_history = True
            history_indent = indent
            continue
        if not in_history:
            continue
        if line.startswith("-") and indent <= history_indent:
            close()
            in_history = False
            continue
        if body.startswith("listitem"):
            close()
            current = {"who": "", "text": "", "pending": False}
            continue
        if current is None:
            continue
        low = body.lower()
        if low.startswith("img"):
            if "my avatar" in low:
                current["who"] = "user"
            elif "agent" in low:
                current["who"] = "agent"
        elif low.startswith("paragraph:") or low.startswith("text:"):
            value = body.split(":", 1)[1].strip().strip("\"'")
            if value and not value.lower().startswith("agentforce avatar responded") and not _is_typing_placeholder(value):
                current["text"] = (current["text"] + "\n" + value).strip()
                current["pending"] = False
        elif low == "status" or low.startswith("status "):
            current["pending"] = True
    close()
    return bubbles


def _trace_is_empty(obs: dict) -> bool:
    return bool(obs.get("nothing_here_yet") or obs.get("details_waiting"))


def _loading_reason(obs: dict) -> str:
    reasons = []
    if not obs.get("panel_found"):
        reasons.append("preview panel not ready")
    if obs.get("spinner"):
        reasons.append("spinner")
    if obs.get("loading_assistant"):
        reasons.append("Loading assistant")
    elif obs.get("agent_loading"):
        reasons.append("agent is loading")
    elif obs.get("loading_word"):
        reasons.append("Loading")
    if obs.get("nothing_here_yet"):
        reasons.append("Nothing here yet")
    if obs.get("empty_reply") and _trace_is_empty(obs):
        reasons.append("empty reply bubble while Trace is empty")
    if obs.get("details_waiting"):
        reasons.append("preview details still empty")
    return ", ".join(reasons) or "loading"


def _still_loading(obs: dict) -> bool:
    """True while Live Test is still loading. An empty reply is not a finished result."""
    if not obs.get("panel_found"):
        return True
    if (
        obs.get("spinner")
        or obs.get("loading_word")
        or obs.get("loading_assistant")
        or obs.get("agent_loading")
        or obs.get("empty_reply")
        or obs.get("nothing_here_yet")
        or obs.get("details_waiting")
        or (obs.get("lets_chat") and obs.get("spinner"))
    ):
        return True
    return False


def judge_live_test(obs: dict, *, expect=(), reply_after: str | None = None) -> dict:
    """Classify one poll. Callers record FAIL only for error or mismatch, never for loading."""
    if isinstance(expect, str):
        expect = (expect,)
    error = obs.get("error_text") or None
    if error:
        return {"kind": "error", "error_text": error, "reply": None, "detail": error}
    reply = _reply_text(obs, reply_after)
    if reply:
        return {"kind": "pass", "error_text": None, "reply": reply, "detail": reply}
    text = obs.get("text") or ""
    if not reply_after:
        for item in expect:
            if item and item in text:
                return {"kind": "pass", "error_text": None, "reply": item, "detail": item}
    if reply_after and reply_after not in text:
        return {"kind": "loading", "error_text": None, "reply": None, "detail": "user message not on screen yet"}
    if _still_loading(obs) or (reply_after and obs.get("details_waiting")):
        return {"kind": "loading", "error_text": None, "reply": None, "detail": _loading_reason(obs)}
    missing = reply_after or (expect[0] if expect else "expected text")
    return {
        "kind": "mismatch",
        "error_text": None,
        "reply": None,
        "detail": f"settled without {missing}",
    }


def fold_wait(previous_error, judgment: dict):
    """Return status, the error to compare next time, and whether the wait is finished.

    A missing reply is not finished here. The caller keeps checking until the time
    bound, and the first empty observation is never a failure. A repeated real
    error is finished, because that text is not a loading signal.
    """
    kind = judgment.get("kind")
    if kind == "pass":
        return "PASS", previous_error, True
    if kind == "error":
        error = judgment.get("error_text") or ""
        if previous_error is not None and error == previous_error:
            return "FAIL", error, True
        return None, error, False
    return None, None, False


def _visible_spinner(root) -> bool:
    loc = root.locator(
        "lightning-spinner, .slds-spinner, [class*='spinner'], [role='progressbar'], [aria-label='Loading' i]"
    )
    count = min(loc.count(), 30)
    for index in range(count):
        el = loc.nth(index)
        try:
            if not el.is_visible():
                continue
            box = el.bounding_box()
        except Exception:
            continue
        if not box:
            continue
        if 8 <= box["width"] <= 120 and 8 <= box["height"] <= 120:
            return True
    try:
        if root.get_attribute("aria-busy") == "true":
            return True
    except Exception:
        pass
    return False


def _visible_chunks(locator, limit: int = 6) -> str:
    chunks = []
    count = min(locator.count(), limit)
    for index in range(count):
        el = locator.nth(index)
        try:
            if el.is_visible():
                chunks.append(el.inner_text(timeout=2000))
        except Exception:
            continue
    return "\n".join(chunks)


def preview_composer(page):
    box = page.get_by_role("textbox", name="Describe your task or ask a question")
    if box.count() == 0:
        box = page.get_by_placeholder("Describe your task or ask a question")
    return box.first


def _mark_preview(page) -> bool:
    composer = preview_composer(page)
    if composer.count() == 0:
        return False
    try:
        if not composer.is_visible():
            return False
        composer.evaluate(_MARK_PREVIEW_JS)
    except Exception:
        return False
    return page.locator("[data-walk-column='1']").count() > 0


def _composer_draft(page) -> str:
    try:
        composer = preview_composer(page)
        if composer.count() == 0 or not composer.is_visible():
            return ""
        return composer.input_value(timeout=1000) or ""
    except Exception:
        return ""


def read_live_test(page) -> dict:
    """Read the Live Test panel. The coworker chat on the right is outside this panel."""
    alerts = _visible_chunks(page.locator("[role='alert'], .slds-theme_error, .slds-notify_alert"))
    panel_found = _mark_preview(page)
    if not panel_found:
        error = extract_real_error(alerts, _visible_chunks(page.get_by_text("Your agent has an error")))
        return {
            "panel_found": False,
            "text": "",
            "messages": [],
            "spinner": True,
            "loading_word": False,
            "empty_reply": False,
            "nothing_here_yet": False,
            "lets_chat": False,
            "details_waiting": False,
            "error_text": error,
            "draft": "",
            "snapshot": "",
        }
    column = page.locator("[data-walk-column='1']").first
    preview = page.locator("[data-walk-preview='1']").first
    column_text = column.inner_text(timeout=5000)
    preview_text = preview.inner_text(timeout=5000)
    try:
        snapshot = column.aria_snapshot(timeout=5000)
    except Exception:
        snapshot = ""
    bubbles = bubbles_from_aria(snapshot)
    if bubbles:
        messages = [bubble["text"] for bubble in bubbles if bubble.get("text")]
        empty_reply = any(bubble.get("pending") for bubble in bubbles)
    else:
        messages = chat_lines(column_text)
        empty_reply = empty_reply_from_aria(snapshot)
    error = extract_real_error(alerts, preview_text)
    if any(line == "Sorry, I encountered an error." for line in messages):
        error = extract_real_error(error or "", "Sorry, I encountered an error.", include_agent=True)
    blob = preview_text + "\n" + column_text
    return {
        "panel_found": True,
        "text": column_text,
        "messages": messages,
        "bubbles": bubbles,
        "spinner": _visible_spinner(preview),
        "loading_word": bool(re.search(r"\bLoading\b", blob, re.I)),
        "loading_assistant": "Loading assistant" in blob,
        "agent_loading": bool(re.search(r"agent is loading|loading (the |your )?agent", blob, re.I)),
        "empty_reply": empty_reply,
        "nothing_here_yet": "Nothing here yet" in blob,
        "lets_chat": _has_lets_chat(column_text),
        "details_waiting": "Send a message in the Preview panel" in blob,
        "error_text": error,
        "draft": _composer_draft(page),
        "snapshot": snapshot,
    }


def wait_for_live_test(
    page,
    *,
    expect=(),
    reply_after: str | None = None,
    timeout_s: float = LIVE_TEST_TIMEOUT_SECONDS,
    interval_s: float = LIVE_TEST_POLL_SECONDS,
    reader: Callable | None = None,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict:
    """Poll Live Test about every 2 seconds for about 90 seconds.

    The first empty observation is not a failure. FAIL only when loading signals
    are gone and the expected result is still missing. A page that is still
    loading returns status None and settled False.
    """
    if isinstance(expect, str):
        expect = (expect,)
    read = reader or read_live_test
    now = clock or time.monotonic

    def pause(seconds: float) -> None:
        if sleeper is not None:
            sleeper(seconds)
            return
        page.wait_for_timeout(int(max(seconds, 0) * 1000))

    start = now()
    previous_error = None
    last = None
    poll = 0
    while True:
        obs = read(page)
        judgment = judge_live_test(obs, expect=expect, reply_after=reply_after)
        status, previous_error, done = fold_wait(previous_error, judgment)
        elapsed = now() - start
        last = {
            "status": status if done else None,
            "settled": bool(done),
            "reason": judgment.get("detail") or "",
            "reply": judgment.get("reply"),
            "error_text": judgment.get("error_text"),
            "text": obs.get("text") or "",
            "messages": obs.get("messages") or [],
            "kind": judgment.get("kind"),
            "elapsed_s": elapsed,
            "loading": _loading_reason(obs) if _still_loading(obs) else "",
        }
        poll += 1
        if done:
            if status == "FAIL" and judgment.get("kind") == "error":
                last["reason"] = judgment.get("error_text") or last["reason"]
            elif status == "FAIL":
                last["reason"] = judgment.get("detail") or last["reason"]
            last["elapsed_s"] = now() - start
            return last
        if elapsed >= timeout_s and poll >= 2:
            if judgment.get("kind") == "loading" or _still_loading(obs):
                last["status"] = None
                last["settled"] = False
                last["reason"] = "still loading: " + _loading_reason(obs)
                last["elapsed_s"] = now() - start
                return last
            last["status"] = "FAIL"
            last["settled"] = True
            last["reason"] = judgment.get("error_text") or judgment.get("detail") or "expected result still missing"
            last["elapsed_s"] = now() - start
            return last
        if poll > 50:
            last["status"] = None
            last["settled"] = False
            last["reason"] = "still loading: " + (_loading_reason(obs) if _still_loading(obs) else "timed out")
            return last
        pause(interval_s if poll < 2 else min(interval_s, max(timeout_s - elapsed, 0)))


def click_preview_send(page) -> None:
    """Click the Send control inside the Live Test composer, not the coworker Send button."""
    if not _mark_preview(page):
        raise RuntimeError("preview composer is not ready")
    composer = preview_composer(page)
    box = composer.bounding_box()
    if not box:
        raise RuntimeError("preview composer has no box")
    buttons = page.locator("[data-walk-column='1']").first.locator("button, [role='button']")
    best = None
    best_x = -1
    count = buttons.count()
    for index in range(count):
        btn = buttons.nth(index)
        try:
            bb = btn.bounding_box()
        except Exception:
            continue
        if not bb:
            continue
        cx = bb["x"] + bb["width"] / 2
        cy = bb["y"] + bb["height"] / 2
        inside = (box["x"] - 6) <= cx <= (box["x"] + box["width"] + 8) and (box["y"] - 6) <= cy <= (box["y"] + box["height"] + 8)
        if inside and cx >= best_x:
            best = btn
            best_x = cx
    if best is None:
        named = page.locator("[data-walk-column='1']").get_by_role("button", name="Send")
        if named.count() == 0:
            raise RuntimeError("Send button inside the preview composer was not found")
        best = named.last
    for _ in range(10):
        try:
            if best.is_enabled():
                break
        except Exception:
            break
        page.wait_for_timeout(300)
    best.click(timeout=8000)
