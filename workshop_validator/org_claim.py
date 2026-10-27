"""Claim a new verification org from the event portal (W-042, W-043).

The owner gives only the event code. The portal address and the code live in
a private file, ``~/.workshop-validator/org-claim.json``, readable only by
the owner. Its keys are ``portal_url`` and ``event_code``. Neither value is
ever printed or written into the repository.

Modes:

* ``--dry-run`` (default): open the portal, follow the Add Event link, confirm
  the event-code field and its submit button exist, then stop. Nothing is
  submitted.
* ``--claim``: submit the event code, open the claimed org's details, read
  the username and password by label, and append one org to
  ``verification-orgs.json``. Refused unless the last run's org decision
  said move on and no unused org is left. ``--force`` (owner only) skips that
  check.
* ``--guides``: download the exercise-guide PDFs linked on the portal into
  ``team/guide-inbox/``.

Output is one line. Page text, credentials, the portal address and the code
are never printed. Every page read goes through a flattened copy of the DOM
(open shadow roots included) and the small parser below, so the logic is
tested on synthetic HTML without a browser.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
import argparse
import contextlib
import fcntl
import json
import os
import re
import sys
import tempfile
import time

from .automate import FAILED, ORGS_NAME, SUCCESS, load_gate, load_orgs

CONFIG_NAME = "org-claim.json"
INBOX = Path("team") / "guide-inbox"
ATTEMPT = Path("team") / "claim-attempt"
LEFT_PORTAL = "left the https portal"
FORCE_REFUSED = "--force needs the owner at a terminal"
ATTEMPT_REFUSED = "a claim was already tried for this run"

OK, NEEDS_REVIEW_EXIT, REFUSED, INCONCLUSIVE = 0, 1, 2, 3

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}
_SKIP = {"script", "style", "template", "noscript"}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_LABELS = {"username": ("username", "user name"), "password": ("password",)}


class ClaimError(Exception):
    """A refusal or failure with a message that is safe to print."""


# ---------------------------------------------------------------- HTML model


@dataclass
class Node:
    tag: str
    attrs: dict
    children: list
    parent: "Node | None" = None

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag not in _SKIP:
                parts.append(child.text())
        return " ".join(" ".join(parts).split())

    def walk(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.walk()


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, [])
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, [], self.current)
        self.current.children.append(node)
        if tag not in _VOID:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(Node(tag, {k: (v or "") for k, v in attrs}, [], self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:
            self.current = node.parent

    def handle_data(self, data):
        if data.strip():
            self.current.children.append(data)


def parse(html: str) -> Node:
    builder = _Builder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _tokens(root: Node) -> list[str]:
    """Visible text and input values in document order."""
    out: list[str] = []

    def visit(node: Node):
        for child in node.children:
            if isinstance(child, str):
                out.append(" ".join(child.split()))
            elif child.tag in _SKIP:
                continue
            elif child.tag in {"input", "textarea"}:
                kind = child.attrs.get("type", "text").lower()
                if kind not in {"hidden", "submit", "button", "checkbox", "radio"} and child.attrs.get("value"):
                    out.append(child.attrs["value"].strip())
                visit(child)
            else:
                visit(child)

    visit(root)
    return [token for token in out if token]


# ---------------------------------------------------------------- page logic


def find_add_event(html: str) -> dict | None:
    """The single Add Event link or button: its kind, text and href."""
    hits = []
    for node in parse(html).walk():
        if node.tag not in {"a", "button"}:
            continue
        if _norm(node.text() or node.attrs.get("aria-label", "")) == "add event":
            hits.append({"kind": "link" if node.tag == "a" else "button",
                         "text": node.text() or node.attrs.get("aria-label", ""),
                         "href": node.attrs.get("href", "")})
    unique = {(h["kind"], h["text"], h["href"]) for h in hits}
    return hits[0] if len(unique) == 1 else None


def has_password_field(html: str) -> bool:
    return any(n.tag == "input" and n.attrs.get("type", "").lower() == "password" for n in parse(html).walk())


def sign_in_required(html: str) -> bool:
    """A login form, or sign-in words on a page that has no Add Event link."""
    if has_password_field(html):
        return True
    root = parse(html)
    words = _norm(root.text())
    return find_add_event(html) is None and bool(re.search(r"\b(sign in|log in|login)\b", words))


def _field_names(node: Node, labels: dict[str, str], previous: str) -> str:
    names = [labels.get(node.attrs.get("id", ""), ""), node.attrs.get("aria-label", ""),
             node.attrs.get("placeholder", ""), node.attrs.get("name", ""), node.attrs.get("id", ""),
             previous]
    parent = node.parent
    while parent is not None:
        if parent.tag == "label":
            names.append(parent.text())
            break
        parent = parent.parent
    return " | ".join(_norm(name.replace("_", " ").replace("-", " ")) for name in names)


def _selector(node: Node) -> str:
    for key in ("id", "name"):
        value = node.attrs.get(key)
        if value:
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            return f'{node.tag}[{key}="{escaped}"]'
    return ""


def find_event_form(html: str) -> dict | None:
    """The event-code field and its submit button, or None when either is missing or unclear."""
    root = parse(html)
    labels = {n.attrs.get("for", ""): n.text() for n in root.walk() if n.tag == "label" and n.attrs.get("for")}
    fields = []
    previous = ""
    for node in root.walk():
        if node.tag in {"input", "textarea"}:
            kind = node.attrs.get("type", "text").lower()
            if kind in {"text", "search", ""} or node.tag == "textarea":
                if "event code" in _field_names(node, labels, previous):
                    fields.append(node)
        elif node.children and isinstance(node.children[0], str):
            previous = node.children[0]
    if len(fields) != 1 or not _selector(fields[0]):
        return None
    field = fields[0]
    # The enclosing form, or else the nearest ancestor that holds a button.
    scope = field.parent
    while scope is not None:
        buttons = _submit_buttons(scope)
        if scope.tag == "form" or buttons:
            break
        scope = scope.parent
    if scope is None or len(buttons) != 1:
        return None
    return {"field": _selector(field), "button": buttons[0]}


def _submit_buttons(scope: Node) -> list[str]:
    names = []
    for node in scope.walk():
        kind = node.attrs.get("type", "").lower()
        # Component sites often use type="button" with a click handler, and keep it
        # disabled until the field has a value. Nameless (icon) buttons are skipped.
        if node.tag == "button" and kind in {"", "submit", "button"}:
            names.append(node.text() or node.attrs.get("aria-label", ""))
        elif node.tag == "input" and kind == "submit":
            names.append(node.attrs.get("value", ""))
    return [n for n in names if n and _norm(n) not in {"cancel", "close", "back", "x"}]


def _label_values(tokens: list[str], label: str) -> set[str]:
    words = _LABELS[label]
    other = {w for key, values in _LABELS.items() if key != label for w in values}
    values: set[str] = set()
    for index, token in enumerate(tokens):
        lowered = token.lower().strip()
        inline = re.match(r"^(%s)\s*:\s*(\S+)$" % "|".join(map(re.escape, words)), lowered)
        if inline:
            values.add(token.split(":", 1)[1].strip())
        elif lowered.rstrip(":").strip() in words and index + 1 < len(tokens):
            nxt = tokens[index + 1].strip()
            if nxt.lower().rstrip(":").strip() not in other | set(words):
                values.add(nxt)
    return values


def extract_credentials(html: str) -> tuple[str, str] | None:
    """Username and password read by label, or None when anything is unsure."""
    tokens = _tokens(parse(html))
    users, passwords = _label_values(tokens, "username"), _label_values(tokens, "password")
    if len(users) != 1 or len(passwords) != 1:
        return None
    username, password = users.pop(), passwords.pop()
    if not _EMAIL.match(username):
        return None
    if not (8 <= len(password) <= 128) or any(c.isspace() for c in password):
        return None
    if set(password) <= {"*", "•", "●", "."}:
        return None
    return username, password


def find_detail_link(html: str) -> dict | None:
    """The one link that opens the claimed org's details."""
    hits = []
    for node in parse(html).walk():
        if node.tag not in {"a", "button"}:
            continue
        words = _norm(node.text() or node.attrs.get("aria-label", ""))
        if re.search(r"\b(details?|credentials?|view org|login info)\b", words):
            hits.append({"kind": "link" if node.tag == "a" else "button",
                         "text": node.text() or node.attrs.get("aria-label", ""),
                         "href": node.attrs.get("href", "")})
    return hits[0] if len(hits) == 1 else None


def on_portal(url: str, portal_url: str) -> bool:
    """True only for an https address on the portal's own host."""
    try:
        here, portal = urlparse(str(url or "")), urlparse(str(portal_url or ""))
        return (here.scheme == "https" and portal.scheme == "https" and bool(here.hostname)
                and here.hostname == portal.hostname and here.port == portal.port)
    except ValueError:
        return False


def find_guide_links(html: str, base: str) -> list[str]:
    seen: list[str] = []
    for node in parse(html).walk():
        href = node.attrs.get("href", "") if node.tag == "a" else ""
        if href and urlparse(href).path.lower().endswith(".pdf"):
            url = urljoin(base, href)
            if urlparse(url).scheme in {"http", "https"} and url not in seen:
                seen.append(url)
    return seen


# ---------------------------------------------------------------- org file


def count_orgs(root: Path) -> int:
    """Number of orgs, counted through automate's loader. No value is read out."""
    try:
        return len(load_orgs(Path(root)))
    except SystemExit:
        return 0


def claim_allowed(root: Path) -> tuple[bool, str]:
    """W-043: claim only when the org rule says move on and no unused org is left."""
    gate = load_gate(Path(root))
    last = gate.get("last_outcome")
    if last not in {SUCCESS, FAILED}:
        return False, "the last run did not end in a success or a real failure"
    total = count_orgs(root)
    if int(gate.get("org") or 0) < total:
        return False, "an unused org remains"
    return True, ""


def _attempt_path(root: Path) -> Path:
    return Path(root) / ATTEMPT


def last_run_id(root: Path):
    return load_gate(Path(root)).get("last_run")


def attempt_allowed(root: Path) -> bool:
    """One claim per gate run: a second is refused unless the earlier one ended CLAIMED."""
    path = _attempt_path(root)
    if not path.exists():
        return True
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False  # unreadable marker: assume a claim was tried
    if not isinstance(marker, dict):
        return False
    return marker.get("last_run") != last_run_id(root) or marker.get("result") == "CLAIMED"


def mark_attempt(root: Path, last_run, result: str) -> None:
    _atomic_write(_attempt_path(root), json.dumps({"last_run": last_run, "result": result}) + "\n")


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:  # mkstemp creates the file 0600, so it is never readable by others
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
    path.chmod(0o600)


def append_org(root: Path, username: str, password: str) -> int:
    """Append one org in the loader's format, atomically, mode 0600. Returns its number."""
    path = Path(root) / ORGS_NAME
    with _org_lock(path):
        return _append_locked(path, username, password)


@contextlib.contextmanager
def _org_lock(path: Path):
    """An exclusive flock on a sibling lock file around read-append-replace."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path.parent / f".{path.name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _append_locked(path: Path, username: str, password: str) -> int:
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ClaimError("the org file has an unknown format")
    orgs = data.get("orgs")
    if not orgs and isinstance(data.get("usernames"), list):
        shared = data.pop("password", None)
        orgs = [{"username": name, "password": shared} for name in data.pop("usernames")]
    if orgs is None:
        orgs = []
    if not isinstance(orgs, list):
        raise ClaimError("the org file has an unknown format")
    key = username.strip().lower()
    if any(str(org.get("username") or "").strip().lower() == key for org in orgs if isinstance(org, dict)):
        raise ClaimError("the claimed org is already in the org file")
    orgs.append({"username": username, "password": password})
    data["orgs"] = orgs
    _atomic_write(path, json.dumps(data, indent=2) + "\n")
    return len(orgs)


def _safe_name(url: str, taken: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(urlparse(url).path).name).strip("-.") or "guide"
    if not stem.lower().endswith(".pdf"):
        stem += ".pdf"
    name, n = stem, 2
    while name in taken:
        name = f"{stem[:-4]}-{n}.pdf"
        n += 1
    taken.add(name)
    return name


def save_guides(root: Path, files: list[tuple[str, bytes]]) -> int:
    """Write downloaded PDFs into the guide inbox, 0600. Returns how many were saved."""
    inbox = Path(root) / INBOX
    inbox.mkdir(mode=0o700, parents=True, exist_ok=True)
    inbox.chmod(0o700)  # mkdir's mode is masked by umask and ignored when the folder exists
    taken = {p.name for p in inbox.iterdir()}
    saved = 0
    for url, body in files:
        if not body.startswith(b"%PDF"):
            continue
        name = _safe_name(url, taken)
        target = inbox / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
        saved += 1
    return saved


def load_config(root: Path) -> dict:
    path = Path(root) / CONFIG_NAME
    if not path.is_file():
        raise ClaimError(f"missing private config {CONFIG_NAME}")
    if path.stat().st_mode & 0o077:
        raise ClaimError(f"{CONFIG_NAME} must be mode 0600")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("portal_url"):
        raise ClaimError(f"{CONFIG_NAME} has no portal_url")
    if urlparse(str(data["portal_url"])).scheme != "https":
        raise ClaimError("portal_url must be https")
    return data


# ---------------------------------------------------------------- browser

_FLATTEN_JS = r"""
() => {
  const esc = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const attr = s => esc(s).replace(/"/g,'&quot;');
  const ser = node => {
    if (node.nodeType === 3) return esc(node.data);
    if (node.nodeType !== 1 && node.nodeType !== 11) return '';
    let inner = '';
    if (node.shadowRoot) for (const c of node.shadowRoot.childNodes) inner += ser(c);
    for (const c of node.childNodes) inner += ser(c);
    if (node.nodeType === 11) return inner;
    const tag = node.localName;
    let attrs = '';
    for (const a of node.attributes) if (a.name !== 'value') attrs += ` ${a.name}="${attr(a.value)}"`;
    if ((tag === 'input' || tag === 'textarea') && node.value) attrs += ` value="${attr(node.value)}"`;
    return `<${tag}${attrs}>${inner}</${tag}>`;
  };
  return ser(document.documentElement);
}
"""


def _flat(page) -> str:
    try:
        return page.evaluate(_FLATTEN_JS)
    except Exception:
        return ""


def _poll(page, check, timeout_s: float = 90.0, every_s: float = 2.0):
    """Wait for a page to settle: try check on a fresh DOM every ~2 s, up to ~90 s."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        html = _flat(page)
        value = check(html)
        if value or time.monotonic() >= deadline:
            return value, html
        page.wait_for_timeout(int(every_s * 1000))


def _settle(page) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass


def _require_portal(page, portal_url: str) -> None:
    """Stop unless the page, after redirects, is https on the portal's own host."""
    if not on_portal(page.url, portal_url):
        raise ClaimError(LEFT_PORTAL)


def _goto(page, url: str, portal_url: str) -> None:
    if not on_portal(url, portal_url):
        raise ClaimError(LEFT_PORTAL)
    page.goto(url, wait_until="domcontentloaded")
    _settle(page)
    _require_portal(page, portal_url)


def _follow(page, target: dict, portal_url: str) -> None:
    href = target.get("href", "")
    if href and not href.startswith(("#", "javascript:")):
        _goto(page, urljoin(page.url, href), portal_url)
        return
    page.get_by_role(target["kind"], name=target["text"], exact=True).first.click()
    _settle(page)
    _require_portal(page, portal_url)


def _open_form(page, portal_url: str) -> dict:
    _goto(page, portal_url, portal_url)
    link, html = _poll(page, lambda h: find_add_event(h) or (has_password_field(h) and {"signin": True}))
    _require_portal(page, portal_url)
    if not link or link.get("signin"):
        raise ClaimError("sign-in required" if (link or sign_in_required(html)) else "no Add Event link found")
    _follow(page, link, portal_url)
    form, _ = _poll(page, find_event_form)
    _require_portal(page, portal_url)
    if not form:
        raise ClaimError("the Add Event form has no single event-code field and submit button")
    return form


def _browser(run):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(20000)
            return run(context, page)
        finally:
            browser.close()


def dry_run(config: dict) -> str:
    def run(_context, page):
        _open_form(page, config["portal_url"])
        return "org-claim: DRY-RUN form ready"
    return _browser(run)


def claim(root: Path, config: dict) -> str:
    code = str(config.get("event_code") or "")
    if not code:
        raise ClaimError(f"{CONFIG_NAME} has no event_code")

    portal_url = config["portal_url"]
    last_run = last_run_id(root)

    def run(_context, page):
        form = _open_form(page, portal_url)
        _require_portal(page, portal_url)
        page.locator(form["field"]).first.fill(code)
        _require_portal(page, portal_url)
        mark_attempt(root, last_run, "started")  # before submitting: a crash still counts
        page.get_by_role("button", name=form["button"], exact=True).first.click()
        _settle(page)
        creds, html = _poll(page, lambda h: extract_credentials(h) or find_detail_link(h))
        _require_portal(page, portal_url)
        if isinstance(creds, dict):
            _follow(page, creds, portal_url)
            creds, _ = _poll(page, extract_credentials)
        if not isinstance(creds, tuple):
            return "org-claim: NEEDS_REVIEW could not read credentials"
        _require_portal(page, portal_url)
        number = append_org(root, *creds)
        mark_attempt(root, last_run, "CLAIMED")
        return f"org-claim: CLAIMED org {number}"
    return _browser(run)


def guides(root: Path, config: dict) -> str:
    portal_url = config["portal_url"]

    def run(context, page):
        _goto(page, portal_url, portal_url)
        links, html = _poll(page, lambda h: find_guide_links(h, page.url))
        _require_portal(page, portal_url)
        if not links and sign_in_required(html):
            raise ClaimError("sign-in required")
        files, skipped = [], 0
        for url in links:
            if not on_portal(url, portal_url):
                skipped += 1
                continue
            response = context.request.get(url)
            if not on_portal(getattr(response, "url", ""), portal_url):
                skipped += 1  # redirected off the portal: the body is not kept
                continue
            if response.ok:
                files.append((url, response.body()))
        line = f"org-claim: GUIDES {save_guides(root, files)}"
        return line + (f" (skipped {skipped} off-portal links)" if skipped else "")
    return _browser(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claim a verification org from the event portal.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default. Stop at the Add Event form.")
    mode.add_argument("--claim", action="store_true", help="Submit the event code and save the new org.")
    mode.add_argument("--guides", action="store_true", help="Download linked exercise-guide PDFs.")
    parser.add_argument("--force", action="store_true",
                        help="Owner only, at a terminal: claim even when the org rule does not say move on.")
    parser.add_argument("--private-root", default=str(Path.home() / ".workshop-validator"))
    args = parser.parse_args(argv)
    root = Path(args.private_root).expanduser()
    try:
        if args.claim:
            if args.force:
                if not sys.stdin.isatty():
                    print(f"org-claim: REFUSED {FORCE_REFUSED}")
                    return REFUSED
            else:
                allowed, reason = claim_allowed(root)
                if not allowed:
                    print(f"org-claim: REFUSED {reason}")
                    return REFUSED
            if not attempt_allowed(root):
                print(f"org-claim: REFUSED {ATTEMPT_REFUSED}")
                return REFUSED
        config = load_config(root)
        if args.claim:
            line = claim(root, config)
        elif args.guides:
            line = guides(root, config)
        else:
            line = dry_run(config)
    except ClaimError as exc:
        reason = str(exc)
        print(f"org-claim: {'SIGN_IN' if reason == 'sign-in required' else 'STOPPED'} {reason}")
        return INCONCLUSIVE
    except Exception as exc:  # never echo the message: it can hold the portal address
        print(f"org-claim: ERROR {type(exc).__name__}")
        return INCONCLUSIVE
    print(line)
    return NEEDS_REVIEW_EXIT if "NEEDS_REVIEW" in line else OK


if __name__ == "__main__":
    sys.exit(main())
