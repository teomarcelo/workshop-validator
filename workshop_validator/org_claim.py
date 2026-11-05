"""Claim a new verification org from the event portal (W-042, W-097).

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
  ``verification-orgs.json``. W-097: allowed whenever the owner's code is set
  in org-claim.json. Only one claim runs at a time (a non-blocking lock held
  for the whole claim). W-104: one event code can be redeemed again and
  again; each redeem gives a new username. A code that fails is submitted at
  most 3 times in a row (a try counts only at the submit click); a CLAIMED
  result resets that code's count. Only a username that was not on the page
  before submit and is not in the org file is saved. W-098, W-099: the org
  gate then points at the new org and its floor, so the retired orgs below it
  are never used again (a freeze stays).

  W-102: at most 10 submits are spent in total during testing, across all
  codes. The marker ``team/claim-attempt`` keeps SHA-256 digests only, never
  the code. Just before each submit click the digest goes into ``pending``;
  a CLAIMED result moves one entry from ``pending`` to ``claimed``; any other
  end (NEEDS_REVIEW, an error, a crash, even after the org was saved) leaves
  it in ``pending``. Nothing clears ``pending`` automatically. When
  ``len(claimed) + len(pending)`` reaches 10, the next claim is refused with
  exit 2 before the browser opens. No flag or environment variable lifts the
  cap. The owner lifts it by hand, after verifying the orgs, by removing
  digests from ``claimed`` or ``pending`` in that private marker.

  A deleted or emptied marker does not reset the count. The org count before
  any claim is kept as ``org_baseline`` in the marker and in a second private
  file, ``team/claim-baseline``. When the marker is missing or empty but the
  org file holds more orgs than that baseline, the claim is refused ("the
  claim marker is missing; the owner must verify (W-102)"). After verifying,
  the owner restores the marker or deletes ``team/claim-baseline`` by hand.
* ``--repoint``: when the last claim ended CLAIMED but the gate step failed,
  point the gate (and floor) at the last org in the org file. Refused unless
  the marker says CLAIMED and the gate floor is lower.
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
import hashlib
import json
import os
import re
import sys
import tempfile
import time

from .automate import ORGS_NAME, gate_floor, load_gate, load_orgs, update_gate

CONFIG_NAME = "org-claim.json"
INBOX = Path("team") / "guide-inbox"
ATTEMPT = Path("team") / "claim-attempt"
RUN_LOCK = Path("team") / ".claim-run.lock"
BASELINE = Path("team") / "claim-baseline"
MAX_ATTEMPTS = 3
LEFT_PORTAL = "left the https portal"
MARKER_REFUSED = "the claim marker could not be read"
MAX_CLAIMED = 10
TOTAL_CAP_REFUSED = f"{MAX_CLAIMED} new orgs already claimed (W-102); the owner must verify before another"
MISSING_REFUSED = "the claim marker is missing; the owner must verify (W-102)"
RUNNING_REFUSED = "another claim is running"
CAP_REFUSED = f"this event code was already tried {MAX_ATTEMPTS} times"

OK, NEEDS_REVIEW_EXIT, REFUSED, INCONCLUSIVE = 0, 1, 2, 3

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "source", "track", "wbr"}
_SKIP = {"script", "style", "template", "noscript"}
_EMAIL = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_EMAIL_IN = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_LABELS = {"username": ("username", "user name", "salesforce org username"),
           "password": ("password", "salesforce org password")}


class ClaimError(Exception):
    """A refusal or failure with a message that is safe to print."""


class ClaimRefused(ClaimError):
    """The claim was refused before anything was submitted (exit 2)."""


class GateNotUpdated(Exception):
    """The org was claimed and saved, but the gate step failed (exit 3). The message is safe."""


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


def usernames_on_page(html: str) -> set[str]:
    """Every username-like value on a page, lowercased: labelled values and any email shape."""
    tokens = _tokens(parse(html))
    seen = {value.strip().lower() for value in _label_values(tokens, "username")}
    for token in tokens:
        seen.update(match.lower() for match in _EMAIL_IN.findall(token))
    return seen


def _block_password(root: Node, username: str) -> str | None:
    """The one password inside the blocks that hold this username and no other."""
    found: set[str] = set()
    for node in (root, *root.walk()):
        tokens = _tokens(node)
        if _label_values(tokens, "username") == {username}:
            passwords = _label_values(tokens, "password")
            if len(passwords) == 1:
                found |= passwords
    return found.pop() if len(found) == 1 else None


def extract_credentials(html: str, exclude=frozenset()) -> tuple[str, str] | None:
    """Username and password read by label, or None when anything is unsure.

    exclude holds usernames seen before submit or already in the org file. Exactly
    one username on the page may be new; its password is read from the same block
    (the containers that hold that username and no other), never page-wide.
    """
    root = parse(html)
    tokens = _tokens(root)
    users, passwords = _label_values(tokens, "username"), _label_values(tokens, "password")
    skip = {str(name).strip().lower() for name in exclude}
    new = {user for user in users if user.strip().lower() not in skip}
    # One username that is new, and never more password values than username blocks.
    if len(new) != 1 or not passwords or len(passwords) > len(users):
        return None
    username = new.pop()
    password = _block_password(root, username)
    if password is None:
        return None
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
    """True only for an https address on the portal's own host and port (none means 443)."""
    here, portal = _https_origin(url), _https_origin(portal_url)
    return here is not None and here == portal


def _https_origin(url: str) -> tuple[str, int] | None:
    """(host, port) of an https address, host lowercased, default port 443. None otherwise."""
    # Reject URLs with backslash or @ that can be parsed differently by browser vs urlparse
    raw = str(url or "")
    if "\\" in raw or "@" in raw:
        return None
    try:
        parts = urlparse(raw)
        if parts.scheme.lower() != "https" or not parts.hostname:
            return None
        # Reject explicit port 0 or any non-443 explicit port
        if parts.port is not None and parts.port != 443:
            return None
        return parts.hostname.lower(), parts.port or 443
    except ValueError:  # a malformed port
        return None


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
    """W-097: claim only when the owner's code is set in org-claim.json."""
    try:
        config = load_config(root)
        if not config.get("event_code"):
            return False, "no event_code in org-claim.json"
    except ClaimError as exc:
        return False, str(exc)
    return True, ""


def _attempt_path(root: Path) -> Path:
    return Path(root) / ATTEMPT


def last_run_id(root: Path):
    return load_gate(Path(root)).get("last_run")


def code_digest(code: str) -> str:
    """SHA-256 of the event code. The marker keeps this, never the code."""
    return hashlib.sha256(str(code).encode("utf-8")).hexdigest()


@contextlib.contextmanager
def claim_run_lock(root: Path):
    """A non-blocking exclusive flock held for a whole claim. Refuses when another claim holds it."""
    path = Path(root) / RUN_LOCK
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ClaimRefused(RUNNING_REFUSED) from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _read_marker(root: Path) -> dict | None:
    """The claim marker, {} when there is none, None when it cannot be read."""
    path = _attempt_path(root)
    if not path.exists():
        return {}
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return marker if isinstance(marker, dict) else None


def _digest_list(value) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(d, str) for d in value):
        return None
    return list(value)


def _claimed_list(marker: dict) -> list[str] | None:
    """One digest per CLAIMED result (repeats allowed, W-104). None when the list is malformed.

    A marker with no "claimed" key but result CLAIMED (an older format) counts as one."""
    if "claimed" not in marker:
        digest = marker.get("code_sha256")
        return [digest] if marker.get("result") == "CLAIMED" and isinstance(digest, str) else []
    return _digest_list(marker.get("claimed"))


def _pending_list(marker: dict) -> list[str] | None:
    """One digest per submit that has not ended CLAIMED. None when the list is malformed."""
    return _digest_list(marker["pending"]) if "pending" in marker else []


def _count(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _read_baseline(root: Path) -> int | None:
    """The org count kept in team/claim-baseline, None when there is none. Raises when unreadable."""
    path = Path(root) / BASELINE
    if not path.exists():
        return None
    try:
        value = _count(json.loads(path.read_text(encoding="utf-8")).get("org_baseline"))
    except (OSError, ValueError, AttributeError):
        value = None
    if value is None:
        raise ClaimRefused(MARKER_REFUSED)
    return value


def reserve_attempt(root: Path, code: str) -> dict:
    """Check and record a new attempt before the browser opens. The caller holds claim_run_lock.

    Refused (ClaimRefused) when the marker is unreadable, when it is missing or empty
    but the org file grew past the baseline, when MAX_CLAIMED submits were spent in
    total (claimed plus pending, W-102), or when this code failed MAX_ATTEMPTS times
    since its last CLAIMED. A code that was CLAIMED can be claimed again (W-104). No
    try is counted here: mark_attempt ("started") counts it just before the submit
    click. Returns the marker written.
    """
    run, digest = last_run_id(root), code_digest(code)
    marker = _read_marker(root)
    if marker is None:
        raise ClaimRefused(MARKER_REFUSED)  # unreadable marker: assume a claim was tried
    claimed, pending = _claimed_list(marker), _pending_list(marker)
    if claimed is None or pending is None:
        raise ClaimRefused(MARKER_REFUSED)
    orgs, kept = count_orgs(root), _read_baseline(root)
    if not marker:  # missing or empty: the count is gone, so the org file is the backstop
        if kept is not None and orgs > kept:
            raise ClaimRefused(MISSING_REFUSED)
        baseline = orgs if kept is None else kept
    else:  # an older marker has no baseline: the orgs it did not claim
        baseline = _count(marker.get("org_baseline"))
        if baseline is None:
            baseline = max(orgs - len(claimed), 0)
    if len(claimed) + len(pending) >= MAX_CLAIMED:
        raise ClaimRefused(TOTAL_CAP_REFUSED)
    counts = marker.get("code_attempts") if isinstance(marker.get("code_attempts"), dict) else {}
    tried = counts.get(digest, 0)
    if not isinstance(tried, int) or isinstance(tried, bool) or tried >= MAX_ATTEMPTS:
        raise ClaimRefused(CAP_REFUSED)
    marker = {"last_run": run, "result": "reserved", "code_sha256": digest,
              "code_attempts": dict(counts), "claimed": claimed, "pending": pending,
              "org_baseline": baseline}
    if kept is None:  # kept outside the marker, so a deleted marker is still noticed
        _atomic_write(Path(root) / BASELINE, json.dumps({"org_baseline": baseline}) + "\n")
    _atomic_write(_attempt_path(root), json.dumps(marker) + "\n")
    return marker


def mark_attempt(root: Path, marker: dict, result: str) -> None:
    """Record a result. "started" counts one try for this code and puts its digest in
    pending (W-102); "CLAIMED" moves one entry from pending to claimed and resets this
    code's tries (W-104). Any other result leaves pending as it is. The marker dict is
    updated in place, so later results keep the counts."""
    digest = marker.get("code_sha256")
    if result == "started" and isinstance(digest, str):
        counts = dict(marker.get("code_attempts") or {})
        counts[digest] = int(counts.get(digest, 0)) + 1
        marker["code_attempts"] = counts
        marker["pending"] = [*(marker.get("pending") or ()), digest]
    elif result == "CLAIMED" and isinstance(digest, str):
        pending = list(marker.get("pending") or ())
        if digest in pending:
            pending.remove(digest)
        marker["pending"] = pending
        marker["claimed"] = [*(marker.get("claimed") or ()), digest]
        counts = dict(marker.get("code_attempts") or {})
        counts.pop(digest, None)
        marker["code_attempts"] = counts
    marker["result"] = result
    _atomic_write(_attempt_path(root), json.dumps(marker) + "\n")


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
    """Append one org in the loader's format, atomically, mode 0600. Returns its 1-based number."""
    path = Path(root) / ORGS_NAME
    with _org_lock(path):
        return _append_locked(path, username, password)


def point_gate_at(root: Path, number: int) -> None:
    """W-098, W-099: the next automate run uses org `number` (1-based). automate's gate["org"]
    is a 0-based index; gate["floor"] gets the same index, so no run goes back to a retired
    org. Written atomically under automate's gate lock; a freeze and other keys are kept."""
    def change(gate: dict) -> None:
        gate["org"] = gate["floor"] = max(number - 1, gate_floor(gate))
    update_gate(Path(root), change)


def repoint(root: Path) -> str:
    """Point the gate (and floor) at the last org in the org file after a CLAIMED claim whose
    gate step failed. Under the claim, org-file and gate locks. Refused unless the marker
    says CLAIMED and the gate floor is lower than the last org."""
    root = Path(root)
    with claim_run_lock(root):
        marker = _read_marker(root)
        if not marker or marker.get("result") != "CLAIMED":
            raise ClaimRefused("the last claim did not end CLAIMED")
        with _org_lock(root / ORGS_NAME):
            total = count_orgs(root)
            if total < 1:
                raise ClaimRefused("the org file has no org")

            def change(gate: dict) -> None:
                floor = gate.get("floor")
                if isinstance(floor, int) and not isinstance(floor, bool) and floor >= total - 1:
                    raise ClaimRefused("the gate floor is already at the last org")
                gate["org"] = gate["floor"] = total - 1
            update_gate(root, change)
    return f"org-claim: REPOINTED gate at org {total}"


def known_usernames(root: Path) -> set[str]:
    """Usernames already in the org file, lowercased. Never printed."""
    try:
        orgs = load_orgs(Path(root))
    except SystemExit:
        return set()
    except ValueError:
        raise ClaimError("the org file has an unknown format") from None
    return {str(org.get("username") or "").strip().lower() for org in orgs if isinstance(org, dict)} - {""}


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
    with claim_run_lock(root):  # held for the whole claim: two claims never overlap
        marker = reserve_attempt(root, code)
        return _claim_locked(root, config, code, marker)


def _claim_locked(root: Path, config: dict, code: str, marker: dict) -> str:
    portal_url = config["portal_url"]
    known = known_usernames(root)

    def run(_context, page):
        form = _open_form(page, portal_url)
        _require_portal(page, portal_url)
        page.locator(form["field"]).first.fill(code)
        _require_portal(page, portal_url)
        seen = known | usernames_on_page(_flat(page))  # earlier events on the page are never new
        _require_portal(page, portal_url)
        mark_attempt(root, marker, "started")  # the try counts here, right before submit
        page.get_by_role("button", name=form["button"], exact=True).first.click()
        _settle(page)

        def read(html):
            return extract_credentials(html, seen)
        creds, html = _poll(page, lambda h: read(h) or find_detail_link(h))
        _require_portal(page, portal_url)
        if isinstance(creds, dict):
            _follow(page, creds, portal_url)
            creds, _ = _poll(page, read)
        if not isinstance(creds, tuple):
            return "org-claim: NEEDS_REVIEW could not read credentials"
        _require_portal(page, portal_url)
        number = append_org(root, *creds)
        # CLAIMED before the gate step: the org counts toward the W-102 cap even if the gate fails.
        mark_attempt(root, marker, "CLAIMED")
        try:
            point_gate_at(root, number)
        except Exception:  # never echo it; --repoint fixes the gate later
            raise GateNotUpdated(f"org-claim: CLAIMED org {number}, gate not updated") from None
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
            response = context.request.get(url, max_redirects=0)
            # Count any non-OK response (including refused redirects) as skipped
            if not response.ok or 300 <= response.status < 400:
                skipped += 1
                continue
            if not on_portal(getattr(response, "url", ""), portal_url):
                skipped += 1  # redirected off the portal: the body is not kept
                continue
            files.append((url, response.body()))
        line = f"org-claim: GUIDES {save_guides(root, files)}"
        return line + (f" (skipped {skipped} off-portal links)" if skipped else "")
    return _browser(run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claim a verification org from the event portal (W-097).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Default. Stop at the Add Event form.")
    mode.add_argument("--claim", action="store_true", help="W-097: Submit the event code and save the new org.")
    mode.add_argument("--guides", action="store_true", help="Download linked exercise-guide PDFs.")
    mode.add_argument("--repoint", action="store_true",
                      help="After 'CLAIMED org N, gate not updated': point the gate at the last org.")
    parser.add_argument("--force", action="store_true",
                        help="Accepted as a no-op alias for --claim.")
    parser.add_argument("--private-root", default=str(Path.home() / ".workshop-validator"))
    args = parser.parse_args(argv)
    root = Path(args.private_root).expanduser()
    try:
        if args.repoint:
            print(repoint(root))
            return OK
        if args.claim:
            allowed, reason = claim_allowed(root)
            if not allowed:
                print(f"org-claim: REFUSED {reason}")
                return REFUSED
        config = load_config(root)
        if args.claim:
            line = claim(root, config)
        elif args.guides:
            line = guides(root, config)
        else:
            line = dry_run(config)
    except GateNotUpdated as exc:
        print(str(exc))
        return INCONCLUSIVE
    except ClaimRefused as exc:
        print(f"org-claim: REFUSED {exc}")
        return REFUSED
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
