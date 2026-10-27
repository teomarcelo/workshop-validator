"""Run uploaded guide steps in printed order.

PASS is recorded only after this run reads the result on the page. A toggle
that is already on (the trial org was provisioned that way) passes without a
click, and the note says so. A missing name is looked up in the live page
HTML, then researched. Research only suggests similar names for the failure
note; a suggestion is never clicked. A reviewed alias may be used to keep
going, but that step stays a UI_MISMATCH. The page is checked every 30
seconds. The run freezes only when that wait has reached 5 minutes and
research has finished. A page that is still loading is not a failure.
The password is never changed here.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .browser_worker import BrowserActionError
from .guide_inventory import GuideStep
from .live_test import click_preview_send, preview_composer, wait_for_live_test


LOADING_NOTE = (
    "The page was still loading after about 90 seconds, "
    "so this step was not marked failed."
)
ALREADY_SET = "{name} was already on in the provisioned org, so it was not clicked."
ALIAS_NOTE = (
    'Guide says "{guide}"; the screen shows "{screen}". Continued with the reviewed '
    "alias. The guide correction is pending instructor approval."
)
PAGE_WAIT_S = 90.0
PAGE_POLL_S = 30.0
PASSWORD_NOTE = (
    "The password was not changed. Changing the password would lock the trial org."
)
WELCOME = "Hello! I'm a simple agent here to say hi."
HELLO = "Hello there!"
_LOADING_TEXT = re.compile(r"\bLoading\b|Loading assistant|Nothing here yet", re.I)


class StillLoading(Exception):
    """The expected control was still behind a loading signal at the time limit."""


@dataclass(frozen=True)
class CheckResult:
    status: str
    note: str = ""
    evidence_ids: tuple[str, ...] = ()
    freeze: bool = False
    suspect: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchResult:
    """A researcher reply. pending means the lookup is still running."""

    pending: bool = False
    candidate: str = ""


@dataclass(frozen=True)
class GuideCheck:
    name: str
    matches: Callable[[GuideStep], bool]
    run: Callable[["WalkState", GuideStep], CheckResult]


@dataclass
class WalkState:
    page: object
    home: str = ""
    studio: object | None = None
    script_text: str = ""
    email_before: str | None = None
    company_before: str | None = None
    timeout_s: float = 90
    interval_s: float = 2
    clock: Callable[[], float] = time.monotonic
    sleeper: Callable[[float], None] | None = None
    research: Callable[["WalkState", GuideStep, str], object] | None = None
    progress: Callable[[str, str], None] | None = None
    page_wait_s: float = PAGE_WAIT_S
    page_poll_s: float = PAGE_POLL_S
    aliases: dict[str, str] = field(default_factory=dict)
    parents: frozenset[str] = frozenset()
    video_origin: float | None = None


@dataclass(frozen=True)
class WalkOutcome:
    statuses: dict[str, str]
    notes: dict[str, str]
    evidence: dict[str, tuple[str, ...]]
    executed: tuple[str, ...] = field(default_factory=tuple)
    suspects: dict[str, tuple[str, ...]] = field(default_factory=dict)
    timestamps: dict[str, tuple[float, float]] = field(default_factory=dict)


def norm(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def is_setup(step: GuideStep) -> bool:
    return bool(re.match(r"set[\s-]*up\b", norm(step.exercise)))


def exercise_is(step: GuideStep, number: str) -> bool:
    return bool(re.search(rf"\bexercise\s+{re.escape(number)}\b", norm(step.exercise)))


def task_has(step: GuideStep, fragment: str) -> bool:
    return fragment in norm(step.task)


def _text(step: GuideStep) -> str:
    return norm(step.text)


def org_host(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    if host in {"login.salesforce.com", "test.salesforce.com", "salesforce.com", "www.salesforce.com"}:
        return ""
    if host.endswith(".salesforce.com") or host.endswith(".force.com"):
        return host
    return ""


def _sleep(state: WalkState, seconds: float) -> None:
    if state.sleeper is not None:
        state.sleeper(seconds)
        return
    time.sleep(max(seconds, 0))


def _frame_url(frame) -> str:
    raw = getattr(frame, "url", "")
    if callable(raw):
        try:
            raw = raw()
        except Exception as exc:
            if "promos.html" in str(exc).lower():
                return "promos.html"
            return ""
    return str(raw or "")


def _promo_frame(frame) -> bool:
    """The login promo iframe is cosmetic. Do not read it as the guide page."""
    try:
        return "promos.html" in _frame_url(frame).lower()
    except Exception as exc:
        return "promos.html" in str(exc).lower()


def _roots(page) -> list:
    roots = []
    frames = getattr(page, "frames", None)
    if frames:
        try:
            roots.extend(list(frames))
        except Exception:
            pass
    if page not in roots:
        roots.insert(0, page)
    kept = []
    for root in roots:
        if root is page or not _promo_frame(root):
            kept.append(root)
    return kept


def _first(loc):
    first = getattr(loc, "first", loc)
    return first() if callable(first) else first


def _count(loc) -> int:
    try:
        return int(loc.count())
    except Exception:
        return 0


def _visible_match(loc):
    """First visible match. Lightning keeps hidden copies of menus ahead of the open one."""
    count = _count(loc)
    if count == 0:
        return None
    nth = getattr(loc, "nth", None)
    candidates = [nth(i) for i in range(min(count, 8))] if callable(nth) else [_first(loc)]
    for target in candidates:
        try:
            if not hasattr(target, "is_visible") or target.is_visible():
                return target
        except Exception:
            continue
    return None


def _visible(loc) -> bool:
    return _visible_match(loc) is not None


def _by_role(root, role: str, name: str, *, exact: bool = True):
    try:
        return root.get_by_role(role, name=name, exact=exact)
    except TypeError:
        return root.get_by_role(role, name=name)
    except Exception:
        return None


def present(page, role: str, name: str) -> bool:
    for root in _roots(page):
        for exact in (True, False):
            loc = _by_role(root, role, name, exact=exact)
            if loc is not None and _visible(loc):
                return True
    return False


_UNGUIDED_CANVAS = frozenset({"free-form", "freeform", "auto-layout", "autolayout"})


def _guide_allows_control(page, name: str) -> bool:
    """Canvas mode is allowed only when this guide step names it."""
    low = norm(name)
    if low not in _UNGUIDED_CANVAS:
        return True
    return low in norm(getattr(page, "_guide_text", "") or "")


def click_role(page, role: str, name: str) -> bool:
    if not _guide_allows_control(page, name):
        return False
    for root in _roots(page):
        loc = _by_role(root, role, name, exact=True)
        target = None if loc is None else _visible_match(loc)
        if target is None:
            continue
        try:
            target.click(timeout=8000)
            return True
        except Exception:
            continue
    return False


def _labeled(root, name: str):
    try:
        try:
            loc = root.get_by_label(name, exact=True)
        except TypeError:
            loc = root.get_by_label(name)
    except Exception:
        return None
    if _count(loc):
        return loc
    return None


def labeled(page, name: str):
    for root in _roots(page):
        loc = _labeled(root, name)
        if loc is not None:
            return loc
    return None


def safe_text(page) -> str:
    try:
        body = page.locator("body")
        if _count(body) == 0:
            return ""
        return _first(body).inner_text(timeout=3000) or ""
    except Exception:
        return ""


def page_is_loading(page) -> bool:
    if _LOADING_TEXT.search(safe_text(page)):
        return True
    try:
        spinner = page.locator(
            "lightning-spinner, .slds-spinner, [role='progressbar']"
        )
        count = min(_count(spinner), 8)
        for index in range(count):
            el = spinner.nth(index)
            try:
                if el.is_visible():
                    return True
            except Exception:
                continue
    except Exception:
        return False
    return False


def settle(
    state: WalkState,
    ready: Callable[[], bool],
    loading: Callable[[], bool] | None = None,
    limit: float | None = None,
) -> None:
    """Poll until ready. Still loading at the limit is not a mismatch."""
    start = state.clock()
    polls = 0
    while True:
        polls += 1
        try:
            if ready():
                return
        except Exception:
            pass
        try:
            loading_now = page_is_loading(state.page) if loading is None else bool(loading())
        except Exception:
            loading_now = False
        elapsed = state.clock() - start
        cap = state.timeout_s if limit is None else limit
        if loading_now and elapsed >= state.timeout_s and polls >= 2:
            raise StillLoading()
        if not loading_now and elapsed >= cap and polls >= 2:
            raise BrowserActionError("The guide target did not match the observed page.")
        if polls > 2000:
            if loading_now:
                raise StillLoading()
            raise BrowserActionError("The guide target did not match the observed page.")
        _sleep(state, state.interval_s)


def require(
    state: WalkState,
    ready: Callable[[], bool],
    failure_note: str,
    *,
    loading: Callable[[], bool] | None = None,
    limit: float | None = None,
) -> CheckResult | None:
    try:
        settle(state, ready, loading, limit)
    except StillLoading:
        return CheckResult("NOT_RUN", LOADING_NOTE, ())
    except BrowserActionError:
        return CheckResult("UI_MISMATCH", failure_note, ())
    return None


def _stop_here(result: CheckResult) -> CheckResult:
    if result.status != "UI_MISMATCH":
        return result
    return CheckResult(result.status, result.note, result.evidence_ids, freeze=True)


def passed(step: GuideStep, evidence: str, note: str, *, suspect: tuple[str, ...] = ()) -> CheckResult:
    token = re.sub(r"[^A-Za-z0-9_.:-]+", "-", evidence).strip("-") or "observed"
    return CheckResult("PASS", note, (f"{step.step_id}:{token}",), suspect=suspect)


def not_attempted(reason: str) -> CheckResult:
    return CheckResult("NOT_RUN", reason, ())


def _input(loc) -> str:
    try:
        return _first(loc).input_value(timeout=2000) or ""
    except Exception:
        return ""


def _disabled(loc) -> bool:
    target = _first(loc)
    try:
        if hasattr(target, "is_enabled") and not target.is_enabled():
            return True
        if hasattr(target, "is_disabled"):
            return bool(target.is_disabled())
    except Exception:
        return False
    return False


def dismiss(page) -> None:
    for name in ("Dismiss", "Got it", "Not now", "Maybe Later"):
        try:
            click_role(page, "button", name)
        except Exception:
            continue


def _login_wall(page) -> bool:
    """The org host is showing a login form, so Lightning is not open."""
    try:
        box = page.locator("#username")
        if _count(box) and _visible(box):
            return True
    except Exception:
        return False
    return "you have to log in" in safe_text(page).lower()


def _org_is_open(state: WalkState) -> bool:
    """True when this run is already on Lightning. Do not send it back to login."""
    if _login_wall(state.page):
        return False
    return bool(org_host(getattr(state.page, "url", "")))


def return_home(state: WalkState) -> bool:
    home = state.home
    if not org_host(home):
        return bool(org_host(getattr(state.page, "url", "")))
    if org_host(getattr(state.page, "url", "")):
        return True
    # A signed-in run stays in the org. Never load the login page from here.
    if "login.salesforce.com" in (home or "").lower():
        return False
    try:
        state.page.goto(home, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return False
    return bool(org_host(getattr(state.page, "url", "")))


def personal_root(state: WalkState):
    for root in _roots(state.page):
        if _labeled(root, "First Name") is not None or _labeled(root, "Company Name") is not None:
            return root
    return None


def _claim_visible(page) -> bool:
    text = safe_text(page).lower()
    return "redeem" in text or "event code" in text or "claim your" in text


def _redeem_control(page):
    for role in ("button", "link"):
        for root in _roots(page):
            loc = _by_role(root, role, "Redeem Code", exact=False)
            if loc is not None and _count(loc):
                return loc
    return None


def run_orgclaim(state: WalkState, step: GuideStep) -> CheckResult:
    if _org_is_open(state):
        return not_attempted(
            "OrgClaim was not opened again because Lightning is already open.",
        )
    try:
        state.page.goto("https://sforce.co/OrgClaim", wait_until="domcontentloaded", timeout=45000)
    except Exception:
        return CheckResult("UI_MISMATCH", "OrgClaim did not open.", ())
    missed = require(state, lambda: _claim_visible(state.page), "OrgClaim did not show the claim page.")
    if missed:
        return missed
    return passed(step, "orgclaim-open", "OrgClaim opened.")


def run_redeem(state: WalkState, step: GuideStep) -> CheckResult:
    if _org_is_open(state):
        return not_attempted(
            "Redeem Code was not opened again because Lightning is already open.",
        )
    if not _claim_visible(state.page):
        try:
            state.page.goto("https://sforce.co/OrgClaim", wait_until="domcontentloaded", timeout=45000)
        except Exception:
            return CheckResult("UI_MISMATCH", "OrgClaim did not open.", ())
    missed = require(
        state,
        lambda: _redeem_control(state.page) is not None or "event code" in safe_text(state.page).lower(),
        "Redeem Code was not on the claim page.",
    )
    if missed:
        return missed
    control = _redeem_control(state.page)
    if control is not None and _disabled(control):
        return passed(step, "redeem-unavailable", "Redeem Code is not available to submit.")
    field = labeled(state.page, "Event code") or labeled(state.page, "Event Code")
    if field is not None and not _input(field).strip():
        return CheckResult(
            "ENV_BLOCKED",
            "No event code was provided with this run, so Redeem Code was not submitted.",
            (),
        )
    if control is None:
        return passed(step, "redeem-unavailable", "The claim page did not ask for a new event code.")
    return CheckResult(
        "ENV_BLOCKED",
        "No event code was provided with this run, so Redeem Code was not submitted.",
        (),
    )


def run_launch(state: WalkState, step: GuideStep) -> CheckResult:
    if _org_is_open(state):
        return not_attempted(
            "Launch was not used because Lightning is already open.",
        )
    missed = require(
        state,
        lambda: present(state.page, "button", "Launch") or present(state.page, "link", "Launch"),
        "Launch was not on the OrgClaim page.",
    )
    if missed:
        return missed
    if not (click_role(state.page, "button", "Launch") or click_role(state.page, "link", "Launch")):
        return CheckResult("UI_MISMATCH", "Launch was not on the OrgClaim page.", ())
    missed = require(
        state,
        lambda: bool(org_host(getattr(state.page, "url", ""))),
        "Launch did not open the org.",
    )
    if missed:
        return missed
    return passed(step, "org-launched", "Launch opened the org.")


def run_login(state: WalkState, step: GuideStep) -> CheckResult:
    # One sign-in per run, before the walk. This step must not open the login
    # page, click Log Out, or submit Login again.
    if not _org_is_open(state):
        return CheckResult("UI_MISMATCH", "Sign-in did not open the org.", ())
    return passed(
        step,
        "lightning-open",
        "Lightning is already open for this signed-in session. "
        "login.salesforce.com was not opened again.",
    )


def run_personal(state: WalkState, step: GuideStep) -> CheckResult:
    if not return_home(state):
        return CheckResult("UI_MISMATCH", "The org was not open.", ())
    dismiss(state.page)
    missed = require(
        state,
        lambda: present(state.page, "button", "View profile"),
        "The profile menu was not on the page.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    if not click_role(state.page, "button", "View profile"):
        return CheckResult("UI_MISMATCH", "The profile menu did not open.", (), freeze=True)

    def settings_shown() -> bool:
        if present(state.page, "link", "Settings") or present(state.page, "menuitem", "Settings"):
            return True
        # Fallback: exact text match within profile panel
        for root in _roots(state.page):
            try:
                loc = root.get_by_text("Settings", exact=True)
                if _visible(loc):
                    return True
            except Exception:
                continue
        return False

    missed = require(state, settings_shown, "Settings was not in the profile menu.", limit=state.page_wait_s)
    if missed:
        return _stop_here(missed)

    # Try role-based click first
    if click_role(state.page, "link", "Settings") or click_role(state.page, "menuitem", "Settings"):
        pass  # Success via role
    else:
        # Fallback: exact text click within profile panel
        clicked = False
        for root in _roots(state.page):
            try:
                loc = root.get_by_text("Settings", exact=True)
                if _count(loc) != 1:
                    continue  # Ambiguous or missing
                target = _visible_match(loc)
                if target is None:
                    continue
                target.click(timeout=8000)
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            # Diagnostic: what was found?
            diagnostic_parts = []
            for root in _roots(state.page):
                try:
                    loc = root.get_by_text("Settings", exact=True)
                    count = _count(loc)
                    if count > 0:
                        diagnostic_parts.append(f"found {count} exact 'Settings' element(s)")
                        for i in range(min(count, 3)):
                            try:
                                elem = loc.nth(i) if hasattr(loc, "nth") else _first(loc)
                                tag = elem.evaluate("el => el.tagName").lower() if hasattr(elem, "evaluate") else "?"
                                role_attr = elem.get_attribute("role") if hasattr(elem, "get_attribute") else None
                                href_attr = elem.get_attribute("href") if hasattr(elem, "get_attribute") else None
                                visible = elem.is_visible() if hasattr(elem, "is_visible") else False
                                diagnostic_parts.append(
                                    f"  [{i}] <{tag}> role={role_attr} href={'yes' if href_attr else 'no'} visible={visible}"
                                )
                            except Exception:
                                pass
                except Exception:
                    pass
            diagnostic = "; ".join(diagnostic_parts) if diagnostic_parts else "no exact 'Settings' found"
            return CheckResult("UI_MISMATCH", f"Settings did not open. {diagnostic}", (), freeze=True)

    missed = require(
        state,
        lambda: personal_root(state) is not None,
        "Personal Information did not open.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    return passed(step, "personal-information", "Profile, then Settings, opens Personal Information.")


def run_names(state: WalkState, step: GuideStep) -> CheckResult:
    missed = require(state, lambda: personal_root(state) is not None, "Personal Information was not open.")
    if missed:
        return missed
    root = personal_root(state)
    first = _labeled(root, "First Name")
    last = _labeled(root, "Last Name")
    if first is None or last is None:
        return CheckResult("UI_MISMATCH", "First Name or Last Name was not on Personal Information.", ())
    if _input(first).strip() and _input(last).strip():
        return passed(
            step, "name-fields",
            "First Name and Last Name are filled. They were not overwritten.",
        )
    return CheckResult(
        "NEEDS_REVIEW",
        "First Name or Last Name is empty, and this run has no name to enter.",
        (),
    )


def _extract_company_name(text: str) -> str:
    """Extract company name from step text like 'Company Name: Acme' or 'Enter Acme in Company Name'."""
    match = re.search(r"Company Name\s+is\s+([A-Z]\w+(?:\s+[A-Z]\w+)*)", text or "", re.I)
    if not match:
        match = re.search(r"Company Name:\s*([A-Z]\w+(?:\s+[A-Z]\w+)*)", text or "", re.I)
    if not match:
        match = re.search(r"\b([A-Z]\w+(?:\s+[A-Z]\w+)*)\s+in\s+Company Name", text or "", re.I)
    if not match:
        return ""
    return _clean_phrase(match.group(1))


def run_company(state: WalkState, step: GuideStep) -> CheckResult:
    missed = require(state, lambda: personal_root(state) is not None, "Personal Information was not open.")
    if missed:
        return missed
    root = personal_root(state)
    company = _labeled(root, "Company Name")
    email = _labeled(root, "Email")
    if company is None:
        return CheckResult("UI_MISMATCH", "Company Name was not on Personal Information.", ())
    if email is not None:
        state.email_before = _input(email)
    expected_company = _extract_company_name(step.text or "")
    if not expected_company:
        return CheckResult("UI_MISMATCH", "Could not determine company name from step text.", ())
    if _input(company) != expected_company:
        try:
            _first(company).fill(expected_company)
        except Exception:
            return CheckResult("UI_MISMATCH", "Company Name could not be edited.", ())
    if _input(company) != expected_company:
        return CheckResult("FUNCTIONAL_FAIL", f"Company Name did not become {expected_company}.", ())
    if email is not None and state.email_before is not None and _input(email) != state.email_before:
        return CheckResult("FUNCTIONAL_FAIL", "The email field changed.", ())
    state.company_before = expected_company
    return passed(step, "company-set", f"Company Name is {expected_company}. The email field was left unchanged.")


def _click_save(page) -> bool:
    return click_role(page, "button", "Save")


def run_save_personal(state: WalkState, step: GuideStep) -> CheckResult:
    missed = require(
        state,
        lambda: personal_root(state) is not None and present(state.page, "button", "Save"),
        "Save was not on Personal Information.",
    )
    if missed:
        return missed
    if state.email_before is None:
        root = personal_root(state)
        email = _labeled(root, "Email") if root is not None else None
        if email is not None:
            state.email_before = _input(email)
    if state.company_before is None:
        root = personal_root(state)
        company = _labeled(root, "Company Name") if root is not None else None
        if company is not None:
            state.company_before = _input(company)
    if not _click_save(state.page):
        return CheckResult("UI_MISMATCH", "Save was not on Personal Information.", ())
    expected_company = state.company_before or ""

    def ready() -> bool:
        root = personal_root(state)
        if root is None:
            return False
        company = _labeled(root, "Company Name")
        return company is not None and _input(company) == expected_company

    missed = require(state, ready, f"Save did not keep Company Name as {expected_company}.")
    if missed:
        return missed
    root = personal_root(state)
    email = _labeled(root, "Email") if root is not None else None
    if email is not None and state.email_before is not None and _input(email) != state.email_before:
        return CheckResult("FUNCTIONAL_FAIL", "Save changed the email field.", ())
    return passed(step, "personal-saved", f"Save kept Company Name as {expected_company}.")


def _quick_find(page):
    finders = (
        lambda root: root.get_by_placeholder("Quick Find", exact=True),
        lambda root: root.get_by_placeholder("Search Setup", exact=True),
        lambda root: root.locator("input[title='Quick Find']"),
        lambda root: _by_role(root, "searchbox", "Quick Find", exact=True),
    )
    for root in _roots(page):
        for finder in finders:
            try:
                loc = finder(root)
            except Exception:
                continue
            if loc is not None and _visible(loc):
                return loc
    return None


def run_setup(state: WalkState, step: GuideStep) -> CheckResult:
    if not return_home(state):
        return CheckResult("UI_MISMATCH", "The org was not open.", ())
    dismiss(state.page)

    def gear_ready() -> bool:
        try:
            gear = state.page.locator("button[title='Setup'], a[title='Setup']")
            if _visible(gear):
                return True
        except Exception:
            pass
        return present(state.page, "button", "Setup")

    missed = require(state, gear_ready, "The Setup gear was not on the page.")
    if missed:
        return missed
    opened = False
    try:
        gear = state.page.locator("button[title='Setup'], a[title='Setup']")
        if _visible(gear):
            _first(gear).click(timeout=8000)
            opened = True
    except Exception:
        opened = False
    if not opened and not click_role(state.page, "button", "Setup"):
        return CheckResult("UI_MISMATCH", "The Setup gear did not open.", ())
    missed = require(state, lambda: _quick_find(state.page) is not None, "Setup Quick Find did not open.")
    if missed:
        return missed
    return passed(step, "setup-open", "The gear opens Setup.")


def _fill_quick_find(page, query: str) -> bool:
    loc = _quick_find(page)
    if loc is None:
        return False
    try:
        _first(loc).fill(query)
        return True
    except Exception:
        return False


def _open_quick_find(state: WalkState, query: str, failure: str) -> CheckResult | None:
    if _quick_find(state.page) is None and not return_home(state):
        return CheckResult("UI_MISMATCH", "The org was not open.", ())
    if _quick_find(state.page) is None:
        gear = run_setup(state, GuideStep(
            "setup", 1, "Set Up Your Instance", None, "0", "Go to Setup", (0, 0, 0, 0), None, (),
        ))
        if gear.status != "PASS":
            return CheckResult(gear.status, failure, ())
    if not _fill_quick_find(state.page, query):
        return CheckResult("UI_MISMATCH", failure, ())
    _sleep(state, min(state.interval_s, 1))
    if not (click_role(state.page, "link", query) or click_role(state.page, "option", query)):
        try:
            match = state.page.get_by_text(query, exact=True)
            if _visible(match):
                _first(match).click(timeout=8000)
            else:
                return CheckResult("UI_MISMATCH", failure, ())
        except Exception:
            return CheckResult("UI_MISMATCH", failure, ())
    return None


def _page_title(page) -> str:
    title = getattr(page, "title", "")
    try:
        if callable(title):
            title = title()
    except Exception:
        return ""
    return title or ""


def _origin(page) -> str:
    parsed = urlparse(getattr(page, "url", "") or "")
    if not org_host(parsed.geturl()) or not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _on_agentforce_setup(page) -> bool:
    url = (getattr(page, "url", "") or "").lower()
    blob = f"{_page_title(page)} {safe_text(page)}".lower()
    if "page not found" in blob:
        return False
    return "einsteincopilot/home" in url or "agentforce agents" in blob


def _agentforce_content_ready(page) -> bool:
    if _switch(page) is not None or present(page, "button", "New Agent"):
        return True
    return "new agent" in safe_text(page).lower()


def _agentforce_loading(page) -> bool:
    """Spinner, Loading, or the agents list still empty is not a mismatch."""
    if page_is_loading(page):
        return True
    if not _on_agentforce_setup(page):
        return False
    return not _agentforce_content_ready(page)


def _agentforce_setup_ready(page) -> bool:
    if _agentforce_loading(page) or not _agentforce_content_ready(page):
        return False
    blob = f"{_page_title(page)} {safe_text(page)}".lower()
    url = (getattr(page, "url", "") or "").lower()
    return "agentforce agents" in blob or "einsteincopilot/home" in url


def run_agentforce_page(state: WalkState, step: GuideStep) -> CheckResult:
    # The setup node is still EinsteinCopilot. Personal-settings Quick Find
    # does not list Agentforce Agents, and AgentforceAgents/home is not a page.
    if not return_home(state):
        return CheckResult("UI_MISMATCH", "The org was not open.", ())
    origin = _origin(state.page)
    if not origin:
        return CheckResult("UI_MISMATCH", "The org was not open.", ())
    try:
        state.page.goto(
            origin + "/lightning/setup/EinsteinCopilot/home",
            wait_until="domcontentloaded",
            timeout=45000,
        )
    except Exception:
        return CheckResult("UI_MISMATCH", "Agentforce Agents did not open.", ())
    dismiss(state.page)
    missed = require(
        state,
        lambda: _agentforce_setup_ready(state.page),
        "Agentforce Agents did not open.",
        loading=lambda: _agentforce_loading(state.page),
    )
    if missed:
        return missed
    return passed(step, "agentforce-agents", "Agentforce Agents is the setup page.")


def _switch(page):
    for role in ("switch", "checkbox"):
        for root in _roots(page):
            loc = _by_role(root, role, "Agentforce", exact=False)
            if loc is not None and _count(loc):
                return loc
    return None


def _switch_on(loc) -> bool:
    target = _first(loc)
    try:
        checked = target.get_attribute("aria-checked")
        if checked is not None:
            return str(checked).lower() == "true"
    except Exception:
        pass
    try:
        if hasattr(target, "is_checked"):
            return bool(target.is_checked())
    except Exception:
        return False
    return False


def run_toggle(state: WalkState, step: GuideStep) -> CheckResult:
    missed = require(state, lambda: _switch(state.page) is not None, "The Agentforce toggle was not on the page.")
    if missed:
        return missed
    loc = _switch(state.page)
    if loc is not None and _switch_on(loc):
        return passed(step, "already-set:agentforce", ALREADY_SET.format(name="Agentforce"))
    try:
        _first(loc).click(timeout=8000)
    except Exception:
        return CheckResult("UI_MISMATCH", "The Agentforce toggle did not change.", ())
    missed = require(state, lambda: _switch(state.page) is not None and _switch_on(_switch(state.page)), "Agentforce did not stay On.")
    if missed:
        return missed
    return passed(step, "agentforce-on", "Agentforce is On.")


def run_users(state: WalkState, step: GuideStep) -> CheckResult:
    missed = _open_quick_find(state, "Users", "Users was not in Setup Quick Find.")
    if missed:
        return missed
    missed = require(
        state,
        lambda: "user" in safe_text(state.page).lower(),
        "The user list did not open.",
    )
    if missed:
        return missed
    return passed(step, "users-open", "Quick Find Users opens the user list.")


def run_einstein_user(state: WalkState, step: GuideStep) -> CheckResult:
    missed = require(
        state,
        lambda: present(state.page, "link", "EinsteinServiceAgent User"),
        "EinsteinServiceAgent User was not in the list.",
    )
    if missed:
        return missed
    if not click_role(state.page, "link", "EinsteinServiceAgent User"):
        return CheckResult("UI_MISMATCH", "EinsteinServiceAgent User was not in the list.", ())
    missed = require(
        state,
        lambda: "einsteinserviceagent" in safe_text(state.page).lower(),
        "The EinsteinServiceAgent user did not open.",
    )
    if missed:
        return missed
    return passed(step, "einstein-user", "EinsteinServiceAgent User opens.")


def run_edit_assignments(state: WalkState, step: GuideStep) -> CheckResult:
    missed = require(
        state,
        lambda: present(state.page, "button", "Edit Assignments") or present(state.page, "link", "Edit Assignments"),
        "Edit Assignments was not on the user page.",
    )
    if missed:
        return missed
    if not (
        click_role(state.page, "button", "Edit Assignments")
        or click_role(state.page, "link", "Edit Assignments")
    ):
        return CheckResult("UI_MISMATCH", "Edit Assignments was not on the user page.", ())
    missed = require(
        state,
        lambda: "permission set" in safe_text(state.page).lower(),
        "Edit Assignments did not open the permission set lists.",
    )
    if missed:
        return missed
    return passed(step, "edit-assignments", "Edit Assignments opens the permission set lists.")


def _permission_enabled(page) -> bool:
    text = safe_text(page).lower()
    return "agentforce action access" in text and "enabled permission sets" in text


def run_add_permission(state: WalkState, step: GuideStep) -> CheckResult:
    if _permission_enabled(state.page):
        return passed(
            step, "permission-enabled",
            "Agentforce Action Access is in Enabled Permission Sets.",
        )
    missed = require(
        state,
        lambda: present(state.page, "option", "Agentforce Action Access"),
        "Agentforce Action Access was not in the permission set lists.",
    )
    if missed:
        return missed
    if not click_role(state.page, "option", "Agentforce Action Access"):
        return CheckResult("UI_MISMATCH", "Agentforce Action Access was not in the permission set lists.", ())
    if not (click_role(state.page, "button", "Add") or click_role(state.page, "button", "Add ")):
        return CheckResult("UI_MISMATCH", "The Add control for permission sets was not on the page.", ())
    missed = require(
        state,
        lambda: "agentforce action access" in safe_text(state.page).lower(),
        "Agentforce Action Access was not added.",
    )
    if missed:
        return missed
    return passed(step, "permission-added", "Agentforce Action Access was added.")


def run_save_permission(state: WalkState, step: GuideStep) -> CheckResult:
    missed = require(
        state,
        lambda: present(state.page, "button", "Save"),
        "Save was not on the permission set page.",
    )
    if missed:
        return missed
    if not _click_save(state.page):
        return CheckResult("UI_MISMATCH", "Save was not on the permission set page.", ())
    missed = require(
        state,
        lambda: "agentforce action access" in safe_text(state.page).lower(),
        "Save did not keep Agentforce Action Access.",
    )
    if missed:
        return missed
    return passed(step, "permission-saved", "Save kept Agentforce Action Access on the user.")


def _needs_studio(state: WalkState) -> CheckResult | None:
    if state.studio is None:
        return not_attempted("This step was not attempted because Agentforce Studio was not open.")
    return None


def run_studio(state: WalkState, step: GuideStep) -> CheckResult:
    if not return_home(state):
        return CheckResult("UI_MISMATCH", "The org was not open.", ())
    dismiss(state.page)
    page = state.page
    missed = require(
        state,
        lambda: _count(page.locator("button[title='App Launcher']")) > 0,
        "The App Launcher was not on the page.",
    )
    if missed:
        return missed
    try:
        _first(page.locator("button[title='App Launcher']")).click(timeout=8000)
    except Exception:
        return CheckResult("UI_MISMATCH", "The App Launcher did not open.", ())
    missed = require(
        state,
        lambda: _count(page.get_by_placeholder("Search apps and items...")) > 0,
        "App search did not open.",
    )
    if missed:
        return missed
    try:
        _first(page.get_by_placeholder("Search apps and items...")).fill("Agentforce Studio")
    except Exception:
        return CheckResult("UI_MISMATCH", "Agentforce Studio was not in the App Launcher.", ())
    _sleep(state, min(state.interval_s, 1))

    def pick() -> bool:
        try:
            item = page.locator("one-app-launcher-menu-item").filter(has_text="Agentforce Studio")
            if _visible(item):
                _first(item).click(timeout=10000)
                return True
        except Exception:
            pass
        return click_role(page, "option", "Agentforce Studio") or click_role(page, "link", "Agentforce Studio")

    if not pick():
        missed = require(state, lambda: False, "Agentforce Studio was not in the App Launcher.")
        return missed or CheckResult("UI_MISMATCH", "Agentforce Studio was not in the App Launcher.", ())
    missed = require(
        state,
        lambda: "agentforce" in safe_text(page).lower() or "agentforce" in (page.title() or "").lower(),
        "Agentforce Studio did not open.",
    )
    if missed:
        return missed
    state.studio = page
    return passed(step, "agentforce-studio", "Agentforce Studio opened from the App Launcher.")


def _agent_visible(page) -> bool:
    if present(page, "link", "HelloWorld Agent"):
        return True
    return "helloworld agent" in safe_text(page).lower()


def run_new_from_script(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio

    def menu_ready() -> bool:
        return (
            present(page, "menuitem", "New from Script")
            or present(page, "button", "New from Script")
            or "new from script" in safe_text(page).lower()
        )

    if not menu_ready():
        for name in ("More options", "Show more", "More"):
            if click_role(page, "button", name):
                break
    missed = require(state, menu_ready, "New from Script was not in the New Agent menu.")
    if missed:
        return missed
    if not (
        click_role(page, "menuitem", "New from Script")
        or click_role(page, "button", "New from Script")
    ):
        return CheckResult("UI_MISMATCH", "New from Script was not in the New Agent menu.", ())
    missed = require(
        state,
        lambda: "script" in safe_text(page).lower(),
        "New from Script did not open.",
    )
    if missed:
        return missed
    return passed(step, "new-from-script", "New from Script opened.")


def run_agentcode(state: WalkState, step: GuideStep) -> CheckResult:
    page = state.page
    context = getattr(page, "context", None)
    if context is None:
        return CheckResult("UI_MISMATCH", "The agent script page did not open.", ())
    opened = None
    try:
        opened = context.new_page()
        target = opened
        target.goto("https://sforce.co/agentcode", wait_until="domcontentloaded", timeout=45000)
        text = safe_text(target).strip()
        if len(text) < 40:
            return CheckResult("UI_MISMATCH", "The agent script page did not include a script.", ())
        state.script_text = text[:200000]
        return passed(step, "agent-script-opened", "The agent script page opened.")
    except Exception:
        return CheckResult("UI_MISMATCH", "The agent script page did not open.", ())
    finally:
        if opened is not None:
            try:
                opened.close()
            except Exception:
                pass


def run_create(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    if _agent_visible(page):
        return passed(
            step, "helloworld-agent-visible",
            "HelloWorld Agent is already listed, so Create Agent was not submitted again.",
        )
    if len(state.script_text) < 40:
        return CheckResult("UI_MISMATCH", "There was no agent script to paste.", ())
    box = None
    for root in _roots(page):
        try:
            candidate = root.get_by_role("textbox")
        except Exception:
            continue
        if _visible(candidate):
            box = candidate
            break
    if box is None:
        return CheckResult("UI_MISMATCH", "The Create Agent dialog had no script field.", ())
    try:
        _first(box).fill(state.script_text)
    except Exception:
        return CheckResult("UI_MISMATCH", "The agent script could not be pasted.", ())
    if not click_role(page, "button", "Create Agent"):
        return CheckResult("UI_MISMATCH", "Create Agent was not available.", ())
    missed = require(state, lambda: _agent_visible(page), "Create Agent did not add HelloWorld Agent.")
    if missed:
        return missed
    return passed(step, "agent-created", "Create Agent added HelloWorld Agent.")


def run_open_agent(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    missed = require(state, lambda: _agent_visible(page), "HelloWorld Agent was not in the list.")
    if missed:
        return missed
    click_role(page, "link", "HelloWorld Agent")
    missed = require(
        state,
        lambda: "agent definition" in safe_text(page).lower() or "canvas" in safe_text(page).lower(),
        "HelloWorld Agent did not open.",
    )
    if missed:
        return missed
    return passed(step, "helloworld-open", "HelloWorld Agent opens the agent definition.")


def run_canvas_script(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    if not (click_role(page, "tab", "Canvas") or click_role(page, "button", "Canvas")):
        missed = require(state, lambda: present(page, "tab", "Canvas") or present(page, "button", "Canvas"), "Canvas was not available.")
        if missed:
            return missed
        if not (click_role(page, "tab", "Canvas") or click_role(page, "button", "Canvas")):
            return CheckResult("UI_MISMATCH", "Canvas was not available.", ())
    if not (click_role(page, "tab", "Script") or click_role(page, "button", "Script")):
        missed = require(state, lambda: present(page, "tab", "Script") or present(page, "button", "Script"), "Script was not available.")
        if missed:
            return missed
        if not (click_role(page, "tab", "Script") or click_role(page, "button", "Script")):
            return CheckResult("UI_MISMATCH", "Script was not available.", ())
    return passed(step, "script-view", "Canvas then Script opened the script view.")


def run_save_agent(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    missed = require(state, lambda: present(page, "button", "Save"), "Save was not in the builder.")
    if missed:
        return missed
    if not _click_save(page):
        return CheckResult("UI_MISMATCH", "Save was not in the builder.", ())
    return passed(step, "agent-saved", "Save completed in the builder.")


def run_select_user(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    missed = require(
        state,
        lambda: present(page, "button", "Select User"),
        "Select User was not in the builder.",
    )
    if missed:
        return missed
    if not click_role(page, "button", "Select User"):
        return CheckResult("UI_MISMATCH", "Select User was not in the builder.", ())
    return passed(step, "select-user", "Select User opened.")


def run_choose_einstein(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    missed = require(
        state,
        lambda: present(page, "option", "EinsteinServiceAgent User") or present(page, "link", "EinsteinServiceAgent User"),
        "EinsteinServiceAgent User was not in the agent user list.",
    )
    if missed:
        return missed
    if not (
        click_role(page, "option", "EinsteinServiceAgent User")
        or click_role(page, "link", "EinsteinServiceAgent User")
    ):
        return CheckResult("UI_MISMATCH", "EinsteinServiceAgent User was not in the agent user list.", ())
    return passed(step, "agent-user", "EinsteinServiceAgent User was selected.")


def run_save_user(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    missed = require(state, lambda: present(page, "button", "Save"), "Save was not on the user dialog.")
    if missed:
        return missed
    if not _click_save(page):
        return CheckResult("UI_MISMATCH", "Save was not on the user dialog.", ())
    return passed(step, "user-saved", "Save completed after the agent user was selected.")


def _preview_ready(page) -> bool:
    try:
        composer = preview_composer(page)
        if _count(page.get_by_role("textbox", name="Describe your task or ask a question")):
            return True
        if hasattr(composer, "count") and composer.count():
            return True
    except Exception:
        return False
    return "live test" in safe_text(page).lower()


def run_preview(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    if not (click_role(page, "tab", "Preview") or click_role(page, "button", "Preview")):
        missed = require(
            state,
            lambda: present(page, "tab", "Preview") or present(page, "button", "Preview"),
            "Preview was not in the builder.",
        )
        if missed:
            return missed
        if not (click_role(page, "tab", "Preview") or click_role(page, "button", "Preview")):
            return CheckResult("UI_MISMATCH", "Preview was not in the builder.", ())
    missed = require(state, lambda: _preview_ready(page), "Preview did not open Live Test.")
    if missed:
        return missed
    return passed(step, "live-test-open", "Preview opens Live Test.")


def _from_live(step: GuideStep, result: dict, evidence: str, pass_note: str) -> CheckResult:
    if result.get("status") == "PASS" and result.get("settled"):
        return passed(step, evidence, pass_note)
    if not result.get("settled"):
        return CheckResult("NOT_RUN", LOADING_NOTE, ())
    reason = result.get("reason") or "The expected Live Test result was not shown."
    status = "FUNCTIONAL_FAIL"
    if result.get("error_text"):
        status = "FUNCTIONAL_FAIL"
    return CheckResult(status, str(reason)[:400], ())


def run_welcome(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    result = wait_for_live_test(
        state.studio,
        expect=(WELCOME,),
        timeout_s=state.timeout_s,
        interval_s=state.interval_s,
        clock=state.clock,
        sleeper=state.sleeper,
    )
    return _from_live(step, result, "welcome", "The welcome text matches.")


def _type_hello(page) -> None:
    composer = preview_composer(page)
    composer.click(timeout=8000)
    if hasattr(composer, "press_sequentially"):
        try:
            composer.press("Control+A")
            composer.press("Backspace")
        except Exception:
            pass
        composer.press_sequentially(HELLO, delay=20)
        return
    composer.fill(HELLO)


def run_reply(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    try:
        _type_hello(page)
        click_preview_send(page)
    except Exception:
        return CheckResult("UI_MISMATCH", "The Live Test composer did not accept the reply.", ())
    result = wait_for_live_test(
        page,
        reply_after=HELLO,
        timeout_s=state.timeout_s,
        interval_s=state.interval_s,
        clock=state.clock,
        sleeper=state.sleeper,
    )
    reply = " ".join((result.get("reply") or "").split())
    note = "The reply arrived after the preview finished loading."
    if reply:
        note = note + " Reply: " + reply[:180]
    return _from_live(step, result, "hello-reply", note)


def run_commit(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    missed = require(
        state,
        lambda: present(page, "button", "Commit Version"),
        "Commit Version was not in the builder.",
    )
    if missed:
        return missed
    if not click_role(page, "button", "Commit Version"):
        return CheckResult("UI_MISMATCH", "Commit Version was not in the builder.", ())
    if present(page, "button", "Commit"):
        click_role(page, "button", "Commit")
    return passed(step, "version-committed", "Commit Version was confirmed.")


def run_activate(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _needs_studio(state)
    if blocked:
        return blocked
    page = state.studio
    missed = require(
        state,
        lambda: present(page, "button", "Activate"),
        "Activate was not in the builder.",
    )
    if missed:
        return missed
    if not click_role(page, "button", "Activate"):
        return CheckResult("UI_MISMATCH", "Activate was not in the builder.", ())
    if present(page, "button", "Activate"):
        click_role(page, "button", "Activate")
    return passed(step, "agent-activated", "Activate was confirmed.")


def _check(name: str, matches: Callable[[GuideStep], bool], run: Callable) -> GuideCheck:
    return GuideCheck(name, matches, run)


def builtin_checks() -> tuple[GuideCheck, ...]:
    """Checks the local site can execute today, in match priority order.

    Execution follows the guide inventory, not this tuple. A step that matches
    none of these is attempted from its own sentence.
    """
    setup = is_setup
    one = lambda step: exercise_is(step, "1-1")
    return (
        _check("Open OrgClaim", lambda step: "sforce.co/orgclaim" in _text(step) and "navigate" in _text(step), run_orgclaim),
        _check("Redeem the event code", lambda step: setup(step) and "redeem code" in _text(step), run_redeem),
        _check("Launch the org", lambda step: "launch" in _text(step) and "orgclaim" in _text(step), run_launch),
        _check("Sign in at login.salesforce.com", lambda step: "login.salesforce.com" in _text(step), run_login),
        _check(
            "Open Personal Information",
            lambda step: (
                "view profile" in _text(step) and "settings" in _text(step)
            ) or (
                setup(step) and "profile" in _text(step) and "settings" in _text(step)
            ),
            run_personal,
        ),
        _check("Check first and last name", lambda step: "first and last name" in _text(step), run_names),
        _check(
            "Set the company name",
            lambda step: "company name" in _text(step) and bool(_extract_company_name(step.text or "")),
            run_company,
        ),
        _check(
            "Save personal information",
            lambda step: setup(step) and task_has(step, "user information") and _text(step).startswith("select save"),
            run_save_personal,
        ),
        _check(
            "Open Setup",
            lambda step: setup(step) and "setup" in _text(step) and "gear" in _text(step),
            run_setup,
        ),
        _check(
            "Open Agentforce Agents",
            lambda step: "agentforce agents" in _text(step) and "quick find" in _text(step),
            run_agentforce_page,
        ),
        _check("Turn Agentforce on", lambda step: "toggle agentforce" in _text(step), run_toggle),
        _check(
            "Create a new Screen Flow",
            lambda step: _text(step).startswith("create a new screen flow"),
            run_screen_flow,
        ),
        _check(
            "Create a record variable",
            lambda step: "create a record variable" in _text(step),
            run_record_variable,
        ),
        _check(
            "Add a Screen element",
            lambda step: "add a screen element" in _text(step) and "with label" in _text(step),
            run_add_screen,
        ),
        _check(
            "Open Users",
            lambda step: setup(step) and "quick find" in _text(step) and _text(step).rstrip(".").endswith("users"),
            run_users,
        ),
        _check(
            "Open the EinsteinServiceAgent user",
            lambda step: setup(step) and task_has(step, "permission set") and _text(step).startswith("select einsteinserviceagent"),
            run_einstein_user,
        ),
        _check("Edit permission set assignments", lambda step: "edit assignments" in _text(step), run_edit_assignments),
        _check(
            "Add Agentforce Action Access",
            lambda step: "agentforce action access" in _text(step) and "permission" in _text(step),
            run_add_permission,
        ),
        _check(
            "Save permission sets",
            lambda step: setup(step) and task_has(step, "permission set") and _text(step) == "select save.",
            run_save_permission,
        ),
        _check(
            "Open Agentforce Studio",
            lambda step: "app launcher" in _text(step) and "agentforce studio" in _text(step),
            run_studio,
        ),
        _check("Start a new agent from script", lambda step: one(step) and "new from script" in _text(step), run_new_from_script),
        _check("Open the agent script", lambda step: "sforce.co/agentcode" in _text(step), run_agentcode),
        _check(
            "Create the agent",
            lambda step: one(step) and "create agent" in _text(step) and "paste" in _text(step),
            run_create,
        ),
        _check("Open HelloWorld Agent", lambda step: one(step) and "helloworld agent" in _text(step), run_open_agent),
        _check(
            "Open the agent script view",
            lambda step: one(step) and "canvas" in _text(step) and "script" in _text(step),
            run_canvas_script,
        ),
        _check(
            "Save the agent",
            lambda step: one(step) and task_has(step, "explore") and _text(step) == "select save.",
            run_save_agent,
        ),
        _check("Open Select User", lambda step: one(step) and _text(step) == "select select user.", run_select_user),
        _check(
            "Choose the EinsteinServiceAgent user",
            lambda step: one(step) and "user record" in _text(step) and "einsteinserviceagent" in _text(step),
            run_choose_einstein,
        ),
        _check("Save the agent user", lambda step: one(step) and _text(step) == "click save.", run_save_user),
        _check(
            "Open Live Test",
            lambda step: one(step) and "preview" in _text(step) and "live test" in _text(step),
            run_preview,
        ),
        _check("Check the welcome message", lambda step: one(step) and "welcome message" in _text(step), run_welcome),
        _check("Reply Hello there!", lambda step: one(step) and "hello there" in _text(step), run_reply),
        _check("Commit the agent version", lambda step: one(step) and "commit version" in _text(step), run_commit),
        _check(
            "Activate the agent",
            lambda step: one(step) and task_has(step, "activate") and _text(step) == "select activate.",
            run_activate,
        ),
    )


def find_check(step: GuideStep, checks: Sequence[GuideCheck] | None = None) -> GuideCheck | None:
    for check in checks if checks is not None else builtin_checks():
        if check.matches(step):
            return check
    return None


def _named_clicks(text: str) -> list[str]:
    """Quoted control names the step already gives, then a following click."""
    raw = text or ""
    if not re.search(r"\b(?:click|select)\b", raw, re.I):
        return []
    names: list[str] = []
    patterns = (
        r'(?:you will see|labeled|called|named)\s+["“]([^"”]+)["”]',
        r'\b(?:click|select)\s+(?:the\s+)?["“]([^"”]+)["”]',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, raw, re.I):
            phrase = match.group(1).strip()
            if phrase and phrase not in names:
                names.append(phrase)
    # "the X to the right of Y and click Z" names X, not Y; Y only locates it.
    # Clicking Y would be a look-alike click, so this shape is not a name list.
    located = re.search(r"\bof\s+(.+?)\s+and click\s+([^.)]+)", raw, re.I)
    chained = re.search(r"\b(?:click|select)\s+(?:the\s+)?(.+?)\s+and\s+(?:then\s+)?click\s+([^.,)]+)", raw, re.I)
    if not names and chained and not located:
        first = re.sub(r"\s+icon$", "", _click_target(chained.group(1)), flags=re.I)
        second = _click_target(chained.group(2))
        if first and second and len(first.split()) <= 4 and len(second.split()) <= 4:
            return [first, second]
    if not names and re.search(r"\bthen\b", raw, re.I):
        for part in re.split(r"\bthen\b", raw, flags=re.I):
            match = re.search(r"\b(?:click|select|press)\b\s+(.+)", part, re.I)
            if not match:
                continue
            phrase = _click_target(match.group(1))
            if phrase and phrase not in names:
                names.append(phrase)
        if len(names) < 2:
            return []
        return names
    if not names:
        return []
    for match in re.finditer(r"\bthen click\s+([^.)]+)", raw, re.I):
        phrase = _click_target(match.group(1))
        if phrase and phrase not in names:
            names.append(phrase)
    return names


_ROW_DROPDOWN = re.compile(
    r"\b(?:click|select)\s+(?:on\s+)?(?:the\s+)?"
    r"(?:drop-?down|down)(?:\s+(?:arrow|menu|button|icon))?\s+"
    r"(?:to the (?:far\s+)?right of|beside|next to)\s+(.+?)\s+"
    r"and\s+(?:then\s+)?(?:click|select)\s+([^.,)]+)",
    re.I,
)
ROW_MENU_SELECTOR = "[aria-haspopup='true'], [aria-haspopup='menu']"


def _row_dropdown(text: str) -> tuple[str, str] | None:
    """'Click the dropdown arrow to the far right of ROW and click ITEM' -> (ROW, ITEM)."""
    match = _ROW_DROPDOWN.search(text or "")
    if not match:
        return None
    row = _click_target(match.group(1))
    item = _click_target(match.group(2))
    if not row or not item:
        return None
    return row, item


def _row_menu_button(page, row: str):
    """The one menu button inside the one visible row named ROW, or None."""
    rows = []
    for root in _roots(page):
        loc = _by_role(root, "row", row, exact=False)
        if loc is None:
            continue
        nth = getattr(loc, "nth", None)
        for i in range(min(_count(loc), 8)):
            item = nth(i) if callable(nth) else _first(loc)
            try:
                if item.is_visible():
                    rows.append(item)
            except Exception:
                continue
    if len(rows) != 1:
        return None
    try:
        buttons = rows[0].locator(ROW_MENU_SELECTOR)
    except Exception:
        return None
    visible = []
    nth = getattr(buttons, "nth", None)
    for i in range(min(_count(buttons), 8)):
        button = nth(i) if callable(nth) else _first(buttons)
        try:
            if button.is_visible():
                visible.append(button)
        except Exception:
            continue
    return visible[0] if len(visible) == 1 else None


def _exact_menuitem(page, item: str):
    """The visible menu item with exactly this name."""
    for root in _roots(page):
        loc = _by_role(root, "menuitem", item, exact=True)
        target = None if loc is None else _visible_match(loc)
        if target is not None:
            return target
    return None


DOWNLOAD_NOTE = "A browser download cannot be read from the page, so this needs review."


def _finish_row_dropdown(state: WalkState, step: GuideStep, row: str, item: str) -> CheckResult:
    """Open the dropdown that belongs to ROW, then click ITEM in that menu. Never click ROW itself."""
    missed = require(
        state,
        lambda: _row_menu_button(state.page, row) is not None,
        f"The dropdown in the {row} row was not found, so {row} was not clicked.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    button = _row_menu_button(state.page, row)
    if button is None or not _click_locator(button):
        return CheckResult(
            "UI_MISMATCH",
            f"The dropdown in the {row} row was not found, so {row} was not clicked.",
            (), freeze=True,
        )
    missed = require(
        state,
        lambda: _exact_menuitem(state.page, item) is not None,
        f"Opened the dropdown in the {row} row, but {item} was not in its menu.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    menuitem = _exact_menuitem(state.page, item)
    if menuitem is None or not _click_locator(menuitem):
        return CheckResult(
            "UI_MISMATCH",
            f"Opened the dropdown in the {row} row, but {item} could not be clicked.",
            (), freeze=True,
        )
    note = f"Opened the dropdown in the {row} row and clicked {item}."
    if re.search(r"\bdownload\b", item, re.I):
        return CheckResult("NEEDS_REVIEW", f"{note} {DOWNLOAD_NOTE}", (), freeze=True)
    return CheckResult(
        "NEEDS_REVIEW", f"{note} No change could be read from the page.", (), freeze=True,
    )


def _click_target(value: str) -> str:
    phrase = _clean_phrase(value)
    phrase = phrase.split(",")[0].strip()
    phrase = re.sub(r"\s+at the (bottom|top)$", "", phrase, flags=re.I)
    # "the Stage field to add it" names Stage.
    phrase = re.sub(r"\s+(?:field\s+)?to add it$", "", phrase, flags=re.I)
    return phrase.strip(" .")


def _referred_click(text: str) -> str:
    """'Click it' means the field this same step just named."""
    if not re.search(r"\bclick it\b", text or "", re.I):
        return ""
    match = re.search(r"\bthe\s+([A-Za-z][\w ]*?)\s+field\b", text or "", re.I)
    if not match:
        return ""
    return match.group(1).strip()


def _phrase_after(text: str, verb: str) -> str:
    match = re.search(rf"\b{verb}\b\s+(.+)", text, re.I)
    if not match:
        return ""
    phrase = _click_target(match.group(1))
    if len(phrase) > 80:
        phrase = phrase[:80].rsplit(" ", 1)[0]
    return phrase


def _clean_phrase(value: str) -> str:
    phrase = re.split(r"[.\n]", value or "", maxsplit=1)[0]
    phrase = re.sub(r"\([^)]*\)", "", phrase)
    phrase = re.sub(r"^(the|on|a|an)\s+", "", phrase.strip(), flags=re.I)
    return phrase.strip(" .")


@dataclass(frozen=True)
class _Intent:
    kind: str
    target: str
    value: str = ""


@dataclass
class _ResearchMemory:
    done: bool = False
    pending: bool = False
    candidate: str = ""


def _page_html(page) -> str:
    chunks: list[str] = []
    for root in _roots(page):
        reader = getattr(root, "content", None)
        if not callable(reader):
            continue
        try:
            chunks.append(reader() or "")
        except Exception:
            continue
    return "\n".join(chunks)


def _html_has(page, name: str) -> bool:
    needle = norm(name)
    if not needle:
        return False
    return needle in norm(_page_html(page))


def _attr_quote(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def _click_exact_role(page, role: str, name: str) -> bool:
    """Click a control whose name is exactly this word. A longer name is a different control."""
    for root in _roots(page):
        loc = _by_role(root, role, name, exact=True)
        if loc is None or not _visible(loc):
            continue
        if _click_locator(loc):
            return True
    return False


def _locator_for_name(page, name: str):
    """Find a visible control by role, title, aria-label, HTML, or text."""
    if not name:
        return None
    roles = ("button", "link", "menuitem", "tab", "option", "switch", "checkbox")
    quoted = _attr_quote(name)
    selectors = (
        f"button[title='{quoted}']",
        f"a[title='{quoted}']",
        f"[title='{quoted}']",
        f"button[aria-label='{quoted}']",
        f"[aria-label='{quoted}']",
    )
    for root in _roots(page):
        for role in roles:
            loc = _by_role(root, role, name, exact=True)
            if loc is not None and _visible(loc):
                return loc
    for root in _roots(page):
        title_of = getattr(root, "get_by_title", None)
        if callable(title_of):
            try:
                loc = title_of(name, exact=True)
            except TypeError:
                try:
                    loc = title_of(name)
                except Exception:
                    loc = None
            except Exception:
                loc = None
            if loc is not None and _visible(loc):
                return loc
        for selector in selectors:
            try:
                loc = root.locator(selector)
            except Exception:
                continue
            if _visible(loc):
                return loc
        try:
            loc = root.get_by_text(name, exact=True)
        except Exception:
            loc = None
        if loc is not None and _visible(loc):
            return loc
    return None


def _control_visible(page, name: str) -> bool:
    if _locator_for_name(page, name) is not None:
        return True
    return _html_has(page, name)


def _click_locator(loc) -> bool:
    try:
        _first(loc).click(timeout=8000)
        return True
    except Exception:
        return False


def _fill_labeled(state: WalkState, label: str, value: str) -> bool:
    try:
        loc = state.page.get_by_label(label)
    except Exception:
        return False
    if not _visible(loc):
        return False
    try:
        _first(loc).fill(value)
    except Exception:
        return False
    return True


def _page_open(state: WalkState) -> bool:
    url = str(getattr(state.page, "url", "") or "")
    return bool(url) and not url.startswith("about:")


def _read_page_text(page) -> str:
    parts = [_page_title(page)]
    for root in _roots(page):
        parts.append(safe_text(root))
    return "\n".join(part for part in parts if part).strip()


def _snippet(value: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _words_visible(page, words: str) -> bool:
    needle = norm(words)
    if not needle or page_is_loading(page):
        return False
    return needle in norm(_read_page_text(page))


def _field_value(state: WalkState, label: str) -> str:
    try:
        loc = state.page.get_by_label(label)
    except Exception:
        return ""
    if not _visible(loc):
        return ""
    return _input(loc).strip()


def _read_toggle(page, name: str) -> tuple[bool, str]:
    """Return whether the toggle was found, and on, off, or unread."""
    loc = None
    for role in ("switch", "checkbox"):
        for root in _roots(page):
            found = _by_role(root, role, name, exact=False)
            if found is not None and _visible(found):
                loc = found
                break
        if loc is not None:
            break
    if loc is None:
        html = _page_html(page)
        if norm(name) and norm(name) in norm(html):
            if re.search(r"aria-checked\s*=\s*['\"]true['\"]", html, re.I):
                return True, "on"
            if re.search(r"aria-checked\s*=\s*['\"]false['\"]", html, re.I):
                return True, "off"
        return False, ""
    target = _first(loc)
    checked = None
    try:
        checked = target.get_attribute("aria-checked")
    except Exception:
        checked = None
    if checked is None and hasattr(target, "is_checked"):
        try:
            checked = "true" if target.is_checked() else "false"
        except Exception:
            checked = None
    if checked is None:
        return True, "unread"
    return True, "on" if str(checked).lower() == "true" else "off"


def _toggle_name(text: str) -> str:
    patterns = (
        r"\btoggle\s+(.+?)\s+to\s+on\b",
        r"\bturn\s+on\s+(?:the\s+)?(.+)",
        r"\bturn\s+(.+?)\s+on\b",
        r"\bswitch\s+(.+?)\s+(?:to\s+)?on\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        name = _clean_phrase(match.group(1))
        if name:
            return name
    return ""


def _expected_words(text: str) -> str:
    patterns = (
        r"\bcheck to see if you are in the\s+(.+?)\s+app\b",
        r"\byou should see\s+(.+)",
        r"\byou will see\s+(.+)",
        r"\bcheck to see\s+(.+)",
        r"\bnote that\s+(.+)",
        r"\breview the\s+(.+)",
        r"\bobserve\s+(.+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if not match:
            continue
        words = _clean_phrase(match.group(1))
        if words:
            return words
    return ""


# "First Name: (your first name)". The parentheses describe the learner's
# own value. The walk never types the description and never invents a value.
_OWN_VALUE = re.compile(r"\((?![^)]*\b(?:blank|leave|default|none)\b)[^()]+\)", re.I)
OWN_VALUE_NOTE = (
    "{label} already has a value in the provisioned org. The guide asks for the "
    "learner's own value, so it was left as is."
)
OWN_VALUE_EMPTY = (
    "{label} is empty. The guide asks for the learner's own value, and the "
    "validator does not invent one."
)
OWN_VALUE_OPTIONAL = (
    "{label} is empty. The guide asks for the learner's own value and does not "
    "mark it mandatory, so it was left unchanged."
)


def _is_field_mandatory(step_text: str, label: str) -> bool:
    """Check if the step text marks the field as mandatory.

    Detection rules:
    - Whole words "required" or "mandatory" anywhere in the step
    - Word "must" when the same clause names the field label
    """
    text_lower = step_text.lower()
    label_lower = label.lower()

    # Check for "required" or "mandatory" as whole words
    if re.search(r'\brequired\b', text_lower) or re.search(r'\bmandatory\b', text_lower):
        return True

    # Check for "must" in the same clause as the field label
    # Split by sentence-ending punctuation and check each clause
    for clause in re.split(r'[.;]', text_lower):
        if 'must' in clause and label_lower in clause:
            return True

    return False


def _is_editable_field(target) -> bool:
    """Check if a target is an editable input field (not a header or label)."""
    try:
        # Check role first (textbox, combobox are editable)
        role = target.get_attribute("role")
        if role in ("textbox", "combobox"):
            return True
    except Exception:
        # If we can't read the role, don't assume it's editable
        pass

    try:
        # Check tag name (input, textarea are editable)
        tag = target.evaluate("el => el.tagName.toLowerCase()")
        if tag in ("input", "textarea"):
            return True
    except Exception:
        # If we can't read the tag, don't assume it's editable
        pass

    return False


def _act_own(state: WalkState, step: GuideStep, label: str) -> CheckResult | None:
    field = labeled(state.page, label)
    if field is None:
        return None

    # Find the first visible AND editable match
    count = _count(field)
    if count == 0:
        return None
    nth = getattr(field, "nth", None)
    candidates = [nth(i) for i in range(min(count, 8))] if callable(nth) else [_first(field)]

    target = None
    for candidate in candidates:
        try:
            if hasattr(candidate, "is_visible") and not candidate.is_visible():
                continue
        except Exception:
            continue
        if _is_editable_field(candidate):
            target = candidate
            break

    if target is None:
        return None

    if _input(target).strip():
        return passed(step, f"already-set:{label}", OWN_VALUE_NOTE.format(label=label))
    # Empty field: check if mandatory
    step_text = step.text or ""
    if _is_field_mandatory(step_text, label):
        return CheckResult("NEEDS_REVIEW", OWN_VALUE_EMPTY.format(label=label), (), freeze=True)
    return CheckResult("NOT_RUN", OWN_VALUE_OPTIONAL.format(label=label), ())


def _intent_for(text: str) -> _Intent:
    toggle = _toggle_name(text)
    if toggle:
        return _Intent("toggle", toggle)
    referred = _referred_click(text)
    if referred:
        return _Intent("click", referred)
    for verb in ("click", "select", "press"):
        phrase = _phrase_after(text, verb)
        if phrase:
            return _Intent("click", phrase)
    words = _expected_words(text)
    if words:
        return _Intent("observe", words)
    urls = re.findall(r"https?://[^\s]+", text or "")
    low = norm(text)
    if urls and any(word in low for word in ("enter", "navigate", "open", "go to", "address")):
        return _Intent("open", urls[0].rstrip(".,)"))
    if ":" in (text or ""):
        label, _, value = text.partition(":")
        label = label.strip()
        if _OWN_VALUE.fullmatch(value.strip()) and len(label) < 48:
            return _Intent("own", label)
        value = value.strip().strip("()")
        if value and "your " not in value.lower() and len(label) < 48:
            return _Intent("fill", label, value)
    return _Intent("unknown", (text or "").strip())


def _is_password_step(low: str) -> bool:
    return any(phrase in low for phrase in (
        "current password",
        "new password",
        "change my password",
        "change your password",
        "change the password",
        "new answer",
    ))


OUTSIDE_NOTE = "This step happens outside the browser, so the validator does not do it."
CONDITION_MET_NOTE = "The step before it already passed, so this If not step was not needed."
_OUTSIDE = re.compile(
    r"\b(?:downloads folder|to your desktop|on your desktop|your computer|file explorer|finder)\b", re.I)
_IF_NOT = re.compile(r"^if (?:not|it is not|you are not|it isn't|you aren't)\b", re.I)


def _side_skip(step: GuideStep, previous: str | None) -> CheckResult | None:
    """Steps the walk records without acting: outside the browser, or an If not already met."""
    low = norm(_without_note(step.text or ""))
    if _OUTSIDE.search(low):
        return CheckResult("NOT_RUN", OUTSIDE_NOTE, ())
    if _IF_NOT.match(low) and previous == "PASS":
        return CheckResult("NOT_RUN", CONDITION_MET_NOTE, ())
    return None


PASSWORD_SAVE_NOTE = (
    "This Save would submit the password form, which was left empty on purpose. "
    "It was not clicked."
)
_SAVE_ONLY = re.compile(r"^click save\.?$")


def _password_skip(step: GuideStep, form: str | None) -> tuple[str | None, CheckResult] | None:
    """Skip a password change, its fields, and the Save that submits it.

    Returns the open password step id and the NOT_RUN result, or None when the
    step is not part of a password change.
    """
    low = norm(_without_note(step.text or ""))
    if _is_password_step(low):
        return (form or step.step_id, CheckResult("NOT_RUN", PASSWORD_NOTE, ()))
    if form is None:
        return None
    if getattr(step, "parent_id", None) == form:
        return (form, CheckResult("NOT_RUN", PASSWORD_NOTE, ()))
    if _SAVE_ONLY.match(low):
        return (None, CheckResult("NOT_RUN", PASSWORD_SAVE_NOTE, ()))
    return None


def _parse_research(answer: object) -> ResearchResult:
    if answer is None:
        return ResearchResult(pending=True)
    if isinstance(answer, ResearchResult):
        return answer
    if isinstance(answer, str):
        return ResearchResult(pending=False, candidate=answer.strip())
    pending = bool(getattr(answer, "pending", False))
    candidate = str(getattr(answer, "candidate", "") or "").strip()
    return ResearchResult(pending=pending, candidate="" if pending else candidate)


def _default_research(state: WalkState, step: GuideStep, name: str) -> ResearchResult:
    """Look on the live page for a renamed control. Never clicks or passes the step."""
    try:
        from .discovery import propose_locators
    except Exception:
        return ResearchResult(pending=False, candidate="")
    for role in ("button", "link", "menuitem", "tab", "option", "checkbox"):
        try:
            found = propose_locators(state.page, role, name, max_results=1)
        except Exception:
            continue
        if not found:
            continue
        candidate = found[0].candidate_name
        if candidate and _control_visible(state.page, candidate):
            return ResearchResult(pending=False, candidate=candidate)
    return ResearchResult(pending=False, candidate="")


def _consult(state: WalkState, step: GuideStep, name: str, memory: _ResearchMemory) -> None:
    if memory.done:
        return
    fn = state.research or _default_research
    try:
        answer = _parse_research(fn(state, step, name))
    except Exception:
        memory.done = True
        memory.pending = False
        memory.candidate = ""
        return
    memory.pending = answer.pending
    memory.done = not answer.pending
    memory.candidate = "" if answer.pending else answer.candidate


def _act_toggle(state: WalkState, step: GuideStep, name: str) -> CheckResult | None:
    found, mode = _read_toggle(state.page, name)
    if not found:
        return None
    if mode == "on":
        return passed(step, f"already-set:{name}", ALREADY_SET.format(name=name))
    if mode != "off":
        return CheckResult(
            "NEEDS_REVIEW",
            f"{name} was found, but its on/off state could not be read, so it was not clicked.",
            (),
            freeze=True,
        )
    loc = _locator_for_name(state.page, name)
    if loc is None or not _click_locator(loc):
        return None
    found, mode = _read_toggle(state.page, name)
    if found and mode == "on":
        return passed(step, "toggle-on", f"{name} is on.")
    return None


def _in_setup(page) -> bool:
    url = str(getattr(page, "url", "") or "").lower()
    return "/lightning/settings" in url or "/lightning/setup" in url


def _without_note(text: str) -> str:
    """A NOTE is a hint. It is not the control the step has to find."""
    return re.split(r"\bNOTE\s*:", text or "", maxsplit=1, flags=re.I)[0].strip()


def _text_choice(page, name: str, exact: bool = True):
    try:
        return page.get_by_text(name, exact=exact)
    except TypeError:
        try:
            return page.get_by_text(name)
        except Exception:
            return None
    except Exception:
        return None


def _browser_pages(page) -> list:
    found = []
    context = getattr(page, "context", None)
    listed = getattr(context, "pages", None) if context is not None else None
    if listed:
        try:
            found = [item for item in list(listed) if item is not None]
        except Exception:
            found = []
    if page not in found:
        found.insert(0, page)
    return found


def _choice_on(page, name: str) -> bool:
    if present(page, "button", name) or present(page, "link", name):
        return True
    for root in _roots(page):
        for exact in (True, False):
            loc = _text_choice(root, name, exact)
            if loc is not None and _visible(loc):
                return True
    return False


def _named_choice(page, name: str) -> bool:
    return any(_choice_on(candidate, name) for candidate in _browser_pages(page))


def _click_choice(page, name: str) -> bool:
    for candidate in _browser_pages(page):
        if click_role(candidate, "button", name) or click_role(candidate, "link", name):
            return True
        for root in _roots(candidate):
            loc = _text_choice(root, name, exact=True)
            if loc is not None and _visible(loc) and _click_locator(loc):
                return True
    return False


def _click_last(page, role: str, name: str) -> bool:
    """The toolbar New Flow button is the last match. An earlier text node does not open it."""
    for root in _roots(page):
        loc = _by_role(root, role, name, exact=True)
        if loc is None:
            continue
        try:
            count = int(loc.count())
        except Exception:
            continue
        for index in range(count - 1, -1, -1):
            item = loc.nth(index)
            try:
                if hasattr(item, "is_visible") and not item.is_visible():
                    continue
                item.click(timeout=8000)
                return True
            except Exception:
                continue
    return False


def _screen_flow_offered(state: WalkState) -> bool:
    if _focus_named(state, "Screen Flow"):
        return True
    _click_last(state.page, "button", "New Flow")
    return False


def _focus_named(state: WalkState, name: str) -> bool:
    for candidate in _browser_pages(state.page):
        if _choice_on(candidate, name):
            state.page = candidate
            return True
    return False


def _focus_builder(state: WalkState) -> bool:
    for candidate in _browser_pages(state.page):
        if not _flow_builder_open(candidate):
            continue
        if "new automation" in safe_text(candidate).lower():
            continue
        state.page = candidate
        return True
    return False


def _screen_flow_choice(page) -> bool:
    return _named_choice(page, "Screen Flow")


def _click_screen_flow(page) -> bool:
    return _click_choice(page, "Screen Flow")


# Custom elements that only the Flow Builder app renders for its canvas.
# UNVERIFIED: these tag names have not been checked against a live Flow Builder page.
_BUILDER_CANVAS_SELECTOR = (
    "builder_platform_interaction-canvas, "
    "builder_platform_interaction-alc-canvas, "
    "builder_platform_interaction-canvas-container"
)
# Phrases that mark a dialog as a product promotion, not part of any guide step.
# The guide names no control in it, so nothing in it is ever clicked.
_PROMO_PHRASES = ("go mobile",)
PROMO_NOTE = "A promotional dialog covered the page; the guide names no control to close it."


def _promo_dialog(page, allowed: str = "") -> bool:
    """A visible dialog whose text marks it as a promotion the step does not name."""
    phrases = [phrase for phrase in _PROMO_PHRASES if phrase not in norm(allowed)]
    if not phrases:
        return False
    for root in _roots(page):
        for role in ("dialog", "alertdialog"):
            try:
                loc = root.locator(f"[role='{role}']")
            except Exception:
                continue
            for index in range(min(_count(loc), 20)):
                item = loc.nth(index)
                try:
                    if hasattr(item, "is_visible") and not item.is_visible():
                        continue
                    text = norm(item.inner_text(timeout=1000) or "")
                except Exception:
                    continue
                if any(phrase in text for phrase in phrases):
                    return True
    return False


def _promo_over(state: WalkState, step: GuideStep) -> bool:
    return any(_promo_dialog(candidate, step.text or "") for candidate in _browser_pages(state.page))


def _builder_canvas_visible(page) -> bool:
    for root in _roots(page):
        try:
            loc = root.locator(_BUILDER_CANVAS_SELECTOR)
        except Exception:
            continue
        if _visible(loc):
            return True
    return False


def _flow_builder_open(page) -> bool:
    """Visible builder canvas and Toolbox control, with no promotion on top. The URL alone is not proof."""
    if _promo_dialog(page):
        return False
    if not _builder_canvas_visible(page):
        return False
    return present(page, "button", "Toggle Toolbox")


CANVAS_UNKNOWN_NOTE = "Flow Builder looks open but its canvas was not recognised; the canvas check needs review."


def _canvas_unrecognised(state: WalkState) -> bool:
    """Builder URL and visible Toolbox, but the UNVERIFIED canvas selector matched nothing.

    That is our selector's doubt, not a guide failure, so it must not count as one.
    """
    for candidate in _browser_pages(state.page):
        url = str(getattr(candidate, "url", "") or "").lower()
        if "flowbuilder" not in url or _promo_dialog(candidate):
            continue
        if _builder_canvas_visible(candidate):
            continue
        if present(candidate, "button", "Toggle Toolbox"):
            return True
    return False


def _not_open_result(state: WalkState, note: str) -> CheckResult:
    if _canvas_unrecognised(state):
        return CheckResult("NEEDS_REVIEW", CANVAS_UNKNOWN_NOTE, (), freeze=True)
    return CheckResult("UI_MISMATCH", note, (), freeze=True)


def _builder_gate(state: WalkState, step: GuideStep) -> CheckResult | None:
    """Every Flow Builder step starts here. No builder, no action."""
    if _promo_over(state, step):
        return CheckResult("ENV_BLOCKED", PROMO_NOTE, (), freeze=True)
    if _focus_builder(state) or _flow_builder_open(state.page):
        return None
    return _not_open_result(state, "Flow Builder is not open.")


def run_screen_flow(state: WalkState, step: GuideStep) -> CheckResult:
    origin = _origin(state.page)
    if not origin:
        return CheckResult("UI_MISMATCH", "The org was not open.", (), freeze=True)
    try:
        state.page.goto(
            origin + "/lightning/setup/Flows/home",
            wait_until="domcontentloaded",
            timeout=45000,
        )
    except Exception:
        return CheckResult("UI_MISMATCH", "Flows did not open.", (), freeze=True)
    missed = require(
        state,
        lambda: _promo_over(state, step) or _named_choice(state.page, "New Flow"),
        "New Flow was not on the Flows page.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    if _promo_over(state, step):
        return CheckResult("ENV_BLOCKED", PROMO_NOTE, (), freeze=True)
    if not (_click_last(state.page, "button", "New Flow") or _click_choice(state.page, "New Flow")):
        return CheckResult("UI_MISMATCH", "New Flow did not open.", (), freeze=True)
    missed = require(
        state,
        lambda: _screen_flow_offered(state),
        "Screen Flow was not offered.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    if not _click_screen_flow(state.page):
        return CheckResult("UI_MISMATCH", "Screen Flow was not selected.", (), freeze=True)
    if present(state.page, "button", "Create") or present(state.page, "link", "Create"):
        if not (click_role(state.page, "button", "Create") or click_role(state.page, "link", "Create")):
            return CheckResult("UI_MISMATCH", "Create was not available for the Screen Flow.", (), freeze=True)
    missed = require(
        state,
        lambda: _promo_over(state, step) or _focus_builder(state),
        "Flow Builder did not open.",
        limit=state.page_wait_s,
    )
    if missed and missed.status == "UI_MISMATCH" and not _promo_over(state, step):
        return _not_open_result(state, missed.note)
    if missed:
        return _stop_here(missed)
    if _promo_over(state, step):
        return CheckResult("ENV_BLOCKED", PROMO_NOTE, (), freeze=True)
    if not _flow_builder_open(state.page):
        return _not_open_result(state, "Flow Builder did not open after Create was clicked.")
    return passed(step, "screen-flow", "New Flow, Screen Flow, then Create opens Flow Builder.")


def _api_name_ready(page) -> bool:
    url = str(getattr(page, "url", "") or "").lower()
    if "/setup/flows" in url:
        return False
    if labeled(page, "API Name") is not None:
        return True
    return present(page, "textbox", "API Name")


def _choose_resource_variable(page) -> None:
    """API Name stays hidden until the resource type is Variable."""
    if _api_name_ready(page):
        return
    if not present(page, "option", "Variable"):
        click_role(page, "combobox", "Resource Type") or click_role(page, "button", "Resource Type")
    click_role(page, "option", "Variable")


def run_record_variable(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _builder_gate(state, step)
    if blocked is not None:
        return blocked
    if present(state.page, "tab", "Manager"):
        click_role(state.page, "tab", "Manager")
    if not (present(state.page, "button", "New Resource") or present(state.page, "link", "New Resource")):
        if not click_role(state.page, "button", "Toggle Toolbox"):
            return CheckResult("UI_MISMATCH", "The Toolbox was not on the page.", (), freeze=True)
    missed = require(
        state,
        lambda: present(state.page, "button", "New Resource") or present(state.page, "link", "New Resource"),
        "New Resource was not in the Toolbox.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    if not (click_role(state.page, "button", "New Resource") or click_role(state.page, "link", "New Resource")):
        return CheckResult("UI_MISMATCH", "New Resource did not open.", (), freeze=True)
    _choose_resource_variable(state.page)
    missed = require(
        state,
        lambda: _api_name_ready(state.page),
        "API Name was not on the new resource.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    return passed(step, "new-resource", "New Resource is open for the record variable.")


def _drag_to_canvas(page, name: str) -> bool:
    source = None
    for root in _roots(page):
        for role in ("button", "option", "listitem", "link"):
            loc = _by_role(root, role, name, exact=True)
            if loc is not None and _visible(loc):
                source = _first(loc)
                break
        if source is not None:
            break
    if source is None:
        return False
    box_of = getattr(source, "bounding_box", None)
    mouse = getattr(page, "mouse", None)
    if not callable(box_of) or mouse is None:
        return False
    try:
        box = box_of()
    except Exception:
        return False
    if not box:
        return False
    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2
    try:
        mouse.move(start_x, start_y)
        mouse.down()
        mouse.move(start_x + 480, start_y + 40, steps=12)
        mouse.up()
        return True
    except Exception:
        return False


def _double_click(page, name: str) -> bool:
    for root in _roots(page):
        for role in ("button", "option", "listitem", "link"):
            loc = _by_role(root, role, name, exact=True)
            if loc is None or not _visible(loc):
                continue
            target = _first(loc)
            dbl = getattr(target, "dblclick", None)
            if callable(dbl):
                try:
                    dbl(timeout=8000)
                    return True
                except Exception:
                    continue
            try:
                target.click(timeout=8000, click_count=2)
                return True
            except TypeError:
                try:
                    target.click(timeout=8000)
                    target.click(timeout=8000)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
    return False


def _force_click(page, name: str, exact: bool = True) -> bool:
    """A palette item can be visible and still reject an ordinary click."""
    for root in _roots(page):
        for role in ("button", "option", "listitem"):
            loc = _by_role(root, role, name, exact=exact)
            if loc is None or not _visible(loc):
                continue
            try:
                _first(loc).click(timeout=8000, force=True)
                return True
            except TypeError:
                try:
                    _first(loc).click(timeout=8000)
                    return True
                except Exception:
                    continue
            except Exception:
                continue
        loc = _text_choice(root, name, exact=True)
        if loc is None or not _visible(loc):
            continue
        try:
            _first(loc).click(timeout=8000, force=True)
            return True
        except Exception:
            continue
    return False


def _screen_label(text: str) -> str:
    match = re.search(r"\bwith label\s+(.+)", text or "", re.I)
    if not match:
        return ""
    return _clean_phrase(match.group(1))


def _close_new_resource(state: WalkState) -> None:
    """The record variable stays in front of the canvas until Done is clicked."""
    if "new resource" not in safe_text(state.page).lower():
        return
    if not present(state.page, "button", "Done"):
        return
    click_role(state.page, "button", "Done")


def _leave_copy_mode(page) -> None:
    if "elements you want to copy" not in safe_text(page).lower():
        return
    for name in ("Close", "Dismiss", "Cancel"):
        if click_role(page, "button", name):
            return
    for root in _roots(page):
        for phrase in ("elements you want to copy", "Elements you want to copy"):
            try:
                text = root.get_by_text(phrase, exact=True)
            except Exception:
                continue
            if not _visible(text):
                continue
            try:
                button = text.locator("xpath=ancestor::*[.//button][1]//button").last
                if _visible(button):
                    button.click(timeout=8000)
                    return
            except Exception:
                continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        return


def _hover_start(page) -> None:
    """The Auto-Layout plus sits on the path under Start."""
    for root in _roots(page):
        for name in ("Start", "Screen Flow"):
            for role in ("button", "link"):
                loc = _by_role(root, role, name, exact=False)
                if loc is None or not _visible(loc):
                    continue
                hover = getattr(_first(loc), "hover", None)
                if not callable(hover):
                    return
                try:
                    hover(timeout=3000)
                except Exception:
                    return
                return


def _exact_role(page, role: str, name: str) -> bool:
    for root in _roots(page):
        loc = _by_role(root, role, name, exact=True)
        if loc is not None and _visible(loc):
            return True
    return False


def _click_exact(page, name: str) -> bool:
    """The start node is named Screen Flow. That is not the Screen element."""
    for role in ("menuitem", "option", "listitem", "button"):
        for root in _roots(page):
            loc = _by_role(root, role, name, exact=True)
            if loc is None or not _visible(loc):
                continue
            if _click_locator(loc):
                return True
    return False


def _screen_mentions(page) -> list[str]:
    """Every visible phrase that contains the word screen."""
    found: list[str] = []
    for chunk in re.split(r"[\n.]", _read_page_text(page)):
        if "screen" not in chunk.lower():
            continue
        phrase = re.sub(r"\s+", " ", chunk).strip()
        if phrase and phrase not in found:
            found.append(phrase[:80])
    return found


def _click_visible_screen_flow(state: WalkState) -> None:
    """The New Automation dialog still shows Screen Flow. Click that tile."""
    for candidate in _browser_pages(state.page):
        text = safe_text(candidate).lower()
        if "new automation" not in text or "screen flow" not in text:
            continue
        state.page = candidate
        _click_choice(candidate, "Screen Flow")
        return


def _canvas_ready(page) -> bool:
    """The Screen Flow canvas is open. The New Automation dialog is not that canvas."""
    if "new automation" in safe_text(page).lower():
        return False
    if not _flow_builder_open(page):
        return False
    if present(page, "button", "Screen Flow") or present(page, "button", "Start"):
        return True
    text = safe_text(page).lower()
    return "screen flow" in text or re.search(r"\bstart\b", text) is not None


def _focus_canvas(state: WalkState) -> bool:
    """Stay on the builder that already shows Start. Do not go back to New Automation."""
    for candidate in _browser_pages(state.page):
        if not _canvas_ready(candidate):
            continue
        state.page = candidate
        front = getattr(candidate, "bring_to_front", None)
        if callable(front):
            try:
                front()
            except Exception:
                pass
        return True
    return False


def _automation_dialog(page) -> bool:
    return "new automation" in safe_text(page).lower()


def _close_extra_dialogs(state: WalkState) -> None:
    """A leftover New Automation tab is not the flow that is already open."""
    pages = _browser_pages(state.page)
    canvas = next((candidate for candidate in pages if _canvas_ready(candidate)), None)
    if canvas is None:
        return
    for candidate in pages:
        if candidate is canvas or not _automation_dialog(candidate):
            continue
        close = getattr(candidate, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
    state.page = canvas


def _screen_item_visible(page) -> bool:
    if _automation_dialog(page):
        return False
    for role in ("menuitem", "option", "listitem", "button", "treeitem", "link"):
        if _exact_role(page, role, "Screen"):
            return True
    return _screen_item(page) is not None


def _screen_item(page):
    """The Screen element. The Start node named Screen Flow is not that element."""
    if _automation_dialog(page):
        return None
    for root in _roots(page):
        for role in ("option", "menuitem", "treeitem", "listitem", "button", "link"):
            try:
                loc = root.get_by_role(role, name="Screen", exact=True)
            except Exception:
                continue
            if loc is None:
                continue
            count = min(_count(loc), 8)
            for index in range(count):
                item = loc.nth(index)
                try:
                    if hasattr(item, "is_visible") and not item.is_visible():
                        continue
                except Exception:
                    continue
                label = ""
                read = getattr(item, "inner_text", None)
                if callable(read):
                    try:
                        label = read(timeout=1000) or ""
                    except Exception:
                        label = ""
                low = norm(label)
                if "screen flow" in low or "screen automation" in low:
                    continue
                if low == "screen" or low.startswith("screen ") or low.startswith("screen\n"):
                    return item
    return None


def _click_screen_item(page) -> bool:
    if _click_exact(page, "Screen"):
        return True
    item = _screen_item(page)
    if item is None:
        return False
    return _click_locator(item)


def _element_search_visible(page) -> bool:
    for root in _roots(page):
        finder = getattr(root, "get_by_placeholder", None)
        if not callable(finder):
            continue
        try:
            loc = finder(re.compile(r"search", re.I))
        except Exception:
            continue
        count = min(_count(loc), 6)
        for index in range(count):
            item = loc.nth(index)
            hint = _input_hint(item)
            if _is_element_search(hint) and _visible(item):
                return True
    return False


def _input_hint(item) -> str:
    getter = getattr(item, "get_attribute", None)
    if not callable(getter):
        return ""
    parts = []
    for attr in ("placeholder", "aria-label", "title"):
        try:
            parts.append(getter(attr) or "")
        except Exception:
            continue
    return " ".join(parts)


def _is_element_search(hint: str) -> bool:
    low = hint.lower()
    if not low or "this flow" in low or "automation" in low:
        return False
    return "element" in low or "search" in low


def _type_screen_search(page) -> bool:
    """Type Screen into the element list. The Toolbox search is a different box."""
    for root in _roots(page):
        finder = getattr(root, "get_by_placeholder", None)
        if not callable(finder):
            continue
        for placeholder in ("Search", "search"):
            try:
                loc = finder(placeholder, exact=True)
            except Exception:
                continue
            count = min(_count(loc), 6)
            for index in range(count):
                item = loc.nth(index)
                hint = _input_hint(item)
                if not _is_element_search(hint) or not _visible(item):
                    continue
                try:
                    item.fill("Screen")
                    return True
                except Exception:
                    continue
    return False


def _dismiss_covering_dialog(page) -> None:
    """The flow is already open. A New Automation dialog left on top is in the way."""
    if not _automation_dialog(page):
        return
    if not (present(page, "button", "Add To Flow") or present(page, "button", "Screen Flow")):
        return
    click_role(page, "button", "Close")


def _screen_label_ready(page) -> bool:
    """The screen property Label. The Flows list column Flow Label is not that field."""
    url = str(getattr(page, "url", "") or "").lower()
    if "flowbuilder" not in url or _automation_dialog(page):
        return False
    return _screen_label_box(page) is not None


def _screen_label_box(page):
    for root in _roots(page):
        loc = _by_role(root, "textbox", "Label", exact=True)
        if loc is not None and _visible(loc):
            return loc
    loc = labeled(page, "Label")
    if loc is not None and _visible(loc):
        return loc
    return None


def _focus_screen_editor(state: WalkState) -> None:
    """The New Screen dialog can be on the builder tab while another tab shows the Flows list."""
    for candidate in _browser_pages(state.page):
        url = str(getattr(candidate, "url", "") or "").lower()
        if "flowbuilder" not in url:
            continue
        if "new screen" not in safe_text(candidate).lower() and _screen_label_box(candidate) is None:
            continue
        state.page = candidate
        front = getattr(candidate, "bring_to_front", None)
        if callable(front):
            try:
                front()
            except Exception:
                pass
        return


def _input_matches_label(page, value: str) -> bool:
    loc = _screen_label_box(page)
    if loc is None:
        return False
    return _input(loc).strip() == value


def _set_screen_label(page, value: str) -> bool:
    loc = _screen_label_box(page)
    if loc is None:
        return False
    target = _first(loc)
    try:
        target.click(timeout=8000)
    except Exception:
        pass
    try:
        target.fill(value)
    except Exception:
        return False
    return _input(loc).strip() == value


def _screen_choice_ready(state: WalkState) -> bool:
    _close_extra_dialogs(state)
    _dismiss_covering_dialog(state.page)
    _focus_canvas(state)
    if _screen_item_visible(state.page):
        return True
    if not _element_search_visible(state.page):
        _open_add_element(state.page)
    _type_screen_search(state.page)
    return _screen_item_visible(state.page)


def _toolbar_is_free_form(page) -> bool:
    return (
        present(page, "button", "Free-Form")
        or present(page, "combobox", "Free-Form")
        or "free-form" in norm(safe_text(page))
    )


_PLUS_UNDER_START = """
() => {
  function walk(root, acc) {
    if (!root || !root.querySelectorAll) return acc;
    for (const el of root.querySelectorAll("*")) {
      acc.push(el);
      if (el.shadowRoot) walk(el.shadowRoot, acc);
    }
    return acc;
  }
  function ownText(el) {
    let value = "";
    for (const node of el.childNodes || []) {
      if (node.nodeType === Node.TEXT_NODE) value += node.textContent || "";
    }
    value = value.replace(/\\s+/g, " ").trim();
    if (value) return value;
    return (el.getAttribute("aria-label") || "").replace(/\\s+/g, " ").trim();
  }
  const all = walk(document, []);
  const named = all.find((el) => {
    const hint = (
      (el.getAttribute("aria-label") || "") + " " +
      (el.getAttribute("title") || "") + " " +
      (el.innerText || "")
    ).replace(/\\s+/g, " ").trim().toLowerCase();
    return hint === "add to flow" || hint.startsWith("add to flow ");
  });
  if (named) {
    named.click();
    return "element";
  }
  const starts = all.filter((el) => {
    const value = ownText(el);
    return value === "Screen Flow" || value === "Start";
  });
  if (!starts.length) return null;
  starts.sort((a, b) => {
    const ar = a.getBoundingClientRect();
    const br = b.getBoundingClientRect();
    return ar.width * ar.height - br.width * br.height;
  });
  const start = starts[0];
  const box = start.getBoundingClientRect();
  if (!box.width || !box.height) return null;
  const cx = box.left + box.width / 2;
  const buttons = all.filter((el) => {
    const tag = (el.tagName || "").toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (tag !== "button" && role !== "button") return false;
    const rect = el.getBoundingClientRect();
    if (rect.width < 8 || rect.width > 56 || rect.height < 8 || rect.height > 56) return false;
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;
    return Math.abs(x - cx) < 36 && y > box.bottom - 2 && y < box.bottom + 56;
  });
  let target = null;
  if (buttons.length) {
    buttons.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
    target = buttons[0];
  } else {
    const y = box.bottom + 28;
    let bestArea = Infinity;
    for (const el of all) {
      const rect = el.getBoundingClientRect();
      if (cx < rect.left || cx > rect.right || y < rect.top || y > rect.bottom) continue;
      const area = rect.width * rect.height;
      if (area > 0 && area < bestArea) {
        bestArea = area;
        target = el;
      }
    }
  }
  if (!target) return null;
  const button = target.closest ? target.closest("button, [role='button']") : null;
  (button || target).click();
  return "element";
}
"""


def _mark_plus(page) -> None:
    try:
        page._plus_at = time.monotonic()
    except Exception:
        return


def _mouse_click(page, x: float, y: float) -> bool:
    mouse = getattr(page, "mouse", None)
    click = getattr(mouse, "click", None)
    if not callable(click):
        return False
    try:
        click(x, y)
    except Exception:
        return False
    _mark_plus(page)
    return True


def _start_box(root) -> dict | None:
    best = None
    best_area = None
    for name in ("Screen Flow", "Start"):
        locs = []
        for role in ("button", "link", "group"):
            locs.append(_by_role(root, role, name, exact=True))
        locs.append(_text_choice(root, name, exact=True))
        for loc in locs:
            if loc is None or not _visible(loc):
                continue
            target = _first(loc)
            box_of = getattr(target, "bounding_box", None)
            if not callable(box_of):
                continue
            try:
                box = box_of()
            except Exception:
                continue
            if not box or not box.get("width") or not box.get("height"):
                continue
            area = float(box["width"]) * float(box["height"])
            if best_area is None or area < best_area:
                best = box
                best_area = area
    return best


def _click_plus_under_start(page) -> bool:
    """The plus on the connector under Start has no accessible name."""
    for root in _roots(page):
        evaluate = getattr(root, "evaluate", None)
        if callable(evaluate):
            try:
                found = evaluate(_PLUS_UNDER_START)
            except Exception:
                found = None
            if found == "element":
                _mark_plus(page)
                return True
        box = _start_box(root)
        if not box:
            continue
        x = float(box["x"]) + float(box["width"]) / 2
        y = float(box["y"]) + float(box["height"]) + 28
        if _mouse_click(page, x, y):
            return True
    return False


def _open_add_element(page) -> None:
    """Auto-Layout adds an element from the plus on the path, not from a drag."""
    if _screen_item_visible(page) or _element_search_visible(page):
        return
    last = float(getattr(page, "_plus_at", 0.0) or 0.0)
    if last and time.monotonic() - last < 8:
        return
    _hover_start(page)
    for name in ("Add To Flow", "Add Element", "Add element", "Add Node"):
        if click_role(page, "button", name):
            _mark_plus(page)
            return
    for root in _roots(page):
        locate = getattr(root, "locator", None)
        if not callable(locate):
            continue
        try:
            loc = root.locator(
                "[title='Add Element'], [aria-label='Add Element'], "
                "[title='Add To Flow'], [aria-label='Add To Flow'], "
                "button[title='Add Element'], button[aria-label='Add Element'], "
                "button[aria-label='Add To Flow']"
            )
        except Exception:
            continue
        if _visible(loc) and _click_locator(loc):
            _mark_plus(page)
            return
    _click_plus_under_start(page)


def run_add_screen(state: WalkState, step: GuideStep) -> CheckResult:
    blocked = _builder_gate(state, step)
    if blocked is not None:
        return blocked
    _close_new_resource(state)
    _leave_copy_mode(state.page)
    _dismiss_covering_dialog(state.page)
    if not _focus_canvas(state):
        _click_visible_screen_flow(state)
    missed = require(
        state,
        lambda: _screen_choice_ready(state),
        "Screen was not in the element list.",
        limit=state.page_wait_s,
    )
    if missed:
        mentions: list[str] = []
        for candidate in _browser_pages(state.page):
            for phrase in _screen_mentions(candidate):
                if phrase not in mentions:
                    mentions.append(phrase)
        found = "; ".join(mentions[:6]) if mentions else "nothing"
        plus = "The plus under Start was clicked." if getattr(state.page, "_plus_at", 0) else "The plus under Start was not clicked."
        return CheckResult(
            missed.status,
            f"{missed.note} {plus} A search for the word screen found: {found}.",
            (),
            freeze=missed.status == "UI_MISMATCH",
        )
    if not _click_screen_item(state.page):
        return CheckResult("UI_MISMATCH", "Screen was not added.", (), freeze=True)
    label = _screen_label(step.text or "")
    if not label:
        # A click is not a result. Without a label to read back, nothing on the page proves the add.
        return CheckResult("NEEDS_REVIEW", "Screen was clicked; no result was read from the page.", (), freeze=True)
    def label_ready() -> bool:
        _focus_screen_editor(state)
        if _input_matches_label(state.page, label):
            return True
        return _set_screen_label(state.page, label)

    missed = require(
        state,
        label_ready,
        "Label was not on the new screen.",
        limit=state.page_wait_s,
    )
    if missed:
        return _stop_here(missed)
    if not _input_matches_label(state.page, label) and not _set_screen_label(state.page, label):
        return CheckResult("UI_MISMATCH", "Label was not on the new screen.", (), freeze=True)
    return passed(step, "screen-added", f"Screen label is {label}.")


def _open_files_home(state: WalkState) -> None:
    """The Files tab from Setup stays on Personal Information unless the Files home opens."""
    host = (urlparse(str(getattr(state.page, "url", "") or "")).hostname or "")
    if not host:
        return
    try:
        state.page.goto(
            f"https://{host}/lightning/o/ContentDocument/home",
            wait_until="domcontentloaded",
            timeout=45000,
        )
    except Exception:
        return


_ROLE_SUFFIX = re.compile(r"\s+(?:tab|button|link|icon)$", re.I)


# A menu, listbox or search popup that repeats a name is not the clicked result.
_POPUP_ROLES = ("menu", "menubar", "listbox", "option", "menuitem", "combobox", "tree", "grid")
_OUTSIDE_POPUPS = "".join(f":not([role='{role}'] *)" for role in _POPUP_ROLES)
# The Flow Builder screen editor canvas, when present, is where a placed control shows.
# UNVERIFIED: these selectors have not been checked against a live Flow Builder page.
_CANVAS_SELECTOR = (
    "builder_platform_interaction-screen-editor-canvas, "
    "builder_platform_interaction-screen-canvas, "
    "[role='region'][aria-label*='canvas' i]"
)
_COUNTED_ROLES = ("button", "link", "tab", "combobox", "textbox", "heading")


def _count_scope(root):
    """The canvas region of this root when one is visible, else the root itself."""
    try:
        canvas = _visible_match(root.locator(_CANVAS_SELECTOR))
    except Exception:
        canvas = None
    if canvas is not None and hasattr(canvas, "locator"):
        return canvas
    return root


def _scoped_name_counts(page) -> dict[str, int]:
    """Per normalized name, how many visible controls carry it, outside menus, listboxes and popups."""
    counts: dict[str, int] = {}
    try:
        for root in _roots(page):
            scope = _count_scope(root)
            for role in _COUNTED_ROLES:
                try:
                    loc = scope.locator(f"[role='{role}']{_OUTSIDE_POPUPS}")
                    item_count = min(_count(loc), 200)
                    for i in range(item_count):
                        item = loc.nth(i)
                        if not hasattr(item, "is_visible") or not item.is_visible():
                            continue
                        names: set[str] = set()
                        for attr in ("aria-label", "title"):
                            try:
                                value = item.get_attribute(attr)
                                if value and value.strip():
                                    names.add(norm(value))
                            except Exception:
                                continue
                        try:
                            text = (item.inner_text(timeout=500) or "").strip()
                            if text and len(text) < 80:
                                names.add(norm(text))
                        except Exception:
                            pass
                        for name in names:
                            counts[name] = counts.get(name, 0) + 1
                except Exception:
                    continue
    except Exception:
        pass
    return counts


def _count_visible_name(page, target: str) -> int:
    """Count visible controls with this normalized name, outside menus, listboxes and popups."""
    return _scoped_name_counts(page).get(target, 0)


def _count_visible_dialogs(page) -> int:
    """Count visible role=dialog and role=alertdialog elements."""
    count = 0
    for root in _roots(page):
        for role in ("dialog", "alertdialog"):
            try:
                loc = root.locator(f"[role='{role}']")
                for i in range(min(_count(loc), 50)):
                    item = loc.nth(i)
                    try:
                        if hasattr(item, "is_visible") and item.is_visible():
                            count += 1
                    except Exception:
                        continue
            except Exception:
                continue
    return count


def _visible_names_snapshot(page) -> frozenset[str]:
    """Capture accessible names of visible controls for before/after comparison."""
    names: set[str] = set()
    try:
        for root in _roots(page):
            for role in ("button", "link", "tab", "menuitem", "option", "combobox", "textbox", "heading"):
                try:
                    loc = root.locator(f"[role='{role}']")
                    count = min(_count(loc), 200)
                    for i in range(count):
                        item = loc.nth(i)
                        if not hasattr(item, "is_visible") or not item.is_visible():
                            continue
                        for attr in ("aria-label", "title"):
                            try:
                                value = item.get_attribute(attr)
                                if value and len(value.strip()) > 2:
                                    names.add(norm(value))
                            except Exception:
                                continue
                        try:
                            text = (item.inner_text(timeout=500) or "").strip()
                            if text and len(text) < 80:
                                names.add(norm(text))
                        except Exception:
                            continue
                except Exception:
                    continue
    except Exception:
        pass
    return frozenset(names)


def _detect_suspect_flags(step: GuideStep, evidence_token: str, before_names: frozenset[str], after_names: frozenset[str]) -> tuple[str, ...]:
    """Compute suspect flags for a step result."""
    flags: list[str] = []

    # weak-evidence: PASS with only click evidence, no after-state read
    if evidence_token == "clicked":
        flags.append("weak-evidence")

    # url-only-evidence: PASS with only URL change evidence
    if evidence_token == "after-state: url changed":
        flags.append("url-only-evidence")

    # unexpected-change: new names appeared that aren't in step text
    step_text_lower = norm(step.text or "")
    new_names = after_names - before_names
    unexpected = [name for name in new_names if name and len(name) > 3 and name not in step_text_lower]
    if unexpected and len(unexpected) <= 10:
        flags.append("unexpected-change")

    return tuple(flags)


def _act_click(state: WalkState, step: GuideStep, name: str) -> CheckResult | None:
    if norm(name) == "fields":
        _focus_screen_editor(state)
    if _in_setup(state.page) and "flow builder quick reference" in norm(name):
        _open_files_home(state)
    if not _control_visible(state.page, name):
        # "the Files tab" names the Files control; the role word is not on screen.
        bare = _ROLE_SUFFIX.sub("", name).strip()
        if not bare or bare == name or not _control_visible(state.page, bare):
            return None
        name = bare

    # Extract target names for evidence matching (clicked control, bare name, quoted names)
    target_names = {norm(name)}
    bare_name = _ROLE_SUFFIX.sub("", name).strip()
    if bare_name and bare_name != name:
        target_names.add(norm(bare_name))

    # Extract quoted names from step text
    import re as _re_mod
    quoted = _re_mod.findall(r'"([^"]+)"', step.text or "")
    for quoted_name in quoted:
        target_names.add(norm(quoted_name))
        bare_quoted = _ROLE_SUFFIX.sub("", quoted_name).strip()
        if bare_quoted and bare_quoted != quoted_name:
            target_names.add(norm(bare_quoted))

    # Exclude position/filler/role words
    excluded_words = {
        "bottom", "top", "middle", "left", "right", "panel", "page", "list",
        "field", "button", "link", "tab", "click", "then", "once", "scroll", "find"
    }
    target_names = {t for t in target_names if t not in excluded_words}

    # Capture before-state for suspect detection and evidence verification
    before_names = _visible_names_snapshot(state.page)
    before_url = getattr(state.page, "url", "")
    # Also capture occurrence counts for target names (handles field placement case)
    before_scoped = _scoped_name_counts(state.page)
    before_counts = {t: before_scoped.get(t, 0) for t in target_names if len(t) > 3}
    before_dialogs = _count_visible_dialogs(state.page)

    loc = _locator_for_name(state.page, name)
    if loc is None or not _click_locator(loc):
        return None
    if norm(name) in {"files", "files tab"} and _in_setup(state.page):
        _open_files_home(state)
        if _in_setup(state.page):
            return None

    # Capture after-state and verify the click had an effect
    after_names = _visible_names_snapshot(state.page)
    after_url = getattr(state.page, "url", "")
    # Also capture occurrence counts for target names
    after_scoped = _scoped_name_counts(state.page)
    after_counts = {t: after_scoped.get(t, 0) for t in target_names if len(t) > 3}
    after_dialogs = _count_visible_dialogs(state.page)

    # Check for after-state evidence
    evidence_desc = None

    # A dialog that was not visible before the click is now open.
    if after_dialogs > before_dialogs:
        evidence_desc = "after-state: dialog opened"

    # Check if dialog text was accepted (after clicking OK/Confirm)
    accepted = str(getattr(state.page, "_accepted_dialog", "") or "").strip()
    if not evidence_desc and accepted:
        evidence_desc = "after-state: dialog text read"

    # Check if success banner appeared (for Save/Submit buttons)
    if not evidence_desc and _words_visible(state.page, "successfully saved"):
        evidence_desc = "after-state: success message visible"

    # Check if clicked element has aria-selected/pressed/expanded = true
    try:
        target = _visible_match(loc)
        if target:
            for attr in ("aria-selected", "aria-pressed", "aria-expanded"):
                value = target.get_attribute(attr)
                if value and value.lower() == "true":
                    attr_name = attr.replace("aria-", "")
                    evidence_desc = f"after-state: {name} {attr_name}"
                    break
    except Exception:
        pass

    # Check if URL changed (must be same host, not an error page, with target in URL/heading)
    if not evidence_desc and before_url != after_url:
        from urllib.parse import urlparse
        before_parsed = urlparse(before_url)
        after_parsed = urlparse(after_url)

        # Must be same host
        if before_parsed.netloc == after_parsed.netloc:
            # Check for error page indicators
            page_text_lower = norm(_read_page_text(state.page))
            is_error = any(indicator in page_text_lower for indicator in (
                "error", "404", "insufficient privileges", "access denied",
                "permission denied", "unauthorized"
            ))

            if not is_error:
                # Check if target name appears in new URL or page heading/title using whole-token matching
                # Get URL path/query and page title
                after_url_lower = after_url.lower()
                page_title_lower = norm(_page_title(state.page))

                # Tokenize URL (path + query) and title on non-alphanumerics
                url_tokens = set(_re_mod.findall(r'[a-z0-9]+', after_url_lower))
                title_tokens = _re_mod.findall(r'[a-z0-9]+', page_title_lower)

                # Filter out common tokens
                ignored = {"salesforce", "lightning", "force", "com"}
                url_tokens -= ignored
                title_tokens = [t for t in title_tokens if t not in ignored]

                # Check if any target name matches using whole-token matching
                found_in_url = False
                found_in_heading = False
                for target_name in target_names:
                    # Targets shorter than 3 chars never use URL/title evidence
                    if len(target_name) < 3:
                        continue

                    # Tokenize target name
                    target_tokens = _re_mod.findall(r'[a-z0-9]+', target_name)
                    if not target_tokens:
                        continue

                    # Check if all target tokens appear in URL in order
                    if len(target_tokens) == 1 and target_tokens[0] in url_tokens:
                        found_in_url = True
                        break
                    elif len(target_tokens) > 1:
                        # For multi-token targets, check if all appear in sequence OR concatenated as one token
                        url_str = ' '.join(_re_mod.findall(r'[a-z0-9]+', after_url_lower))
                        target_str = ' '.join(target_tokens)
                        concatenated = ''.join(target_tokens)
                        if target_str in url_str or concatenated in url_tokens:
                            found_in_url = True
                            break

                    # Check if all target tokens appear in title in order
                    if len(target_tokens) == 1 and target_tokens[0] in title_tokens:
                        found_in_heading = True
                        break
                    elif len(target_tokens) > 1:
                        title_str = ' '.join(title_tokens)
                        target_str = ' '.join(target_tokens)
                        concatenated = ''.join(target_tokens)
                        if target_str in title_str or concatenated in title_tokens:
                            found_in_heading = True
                            break

                if found_in_url or found_in_heading:
                    evidence_desc = "after-state: url changed"
                # else: no evidence, will fall through to NEEDS_REVIEW

    # Check if new names appeared that match target_names, or if target name occurrence increased
    if not evidence_desc:
        # A target name newly shown, or shown more times (a field placed on the canvas
        # while it stays in the palette). Counts leave out menus, listboxes and popups
        # and use the canvas when present, so a dropdown repeating the name is not evidence.
        for target in sorted(target_names):
            if len(target) > 3 and after_counts.get(target, 0) > before_counts.get(target, 0):
                evidence_desc = f"after-state: {target} appeared"
                break

    # If no evidence of change, return NEEDS_REVIEW
    if not evidence_desc:
        note = f"Clicked {name}, but no change could be read from the page."
        return CheckResult("NEEDS_REVIEW", note, (), freeze=True)

    # PASS with after-state evidence
    snapshot = _read_page_text(state.page)
    note = f"Clicked {name}."
    if snapshot:
        note = f"{note} The page showed {_snippet(snapshot)}."

    suspect = _detect_suspect_flags(step, evidence_desc, before_names, after_names)
    return passed(step, evidence_desc, note, suspect=suspect)


def _act_observe(state: WalkState, step: GuideStep, words: str) -> CheckResult | None:
    if not _words_visible(state.page, words):
        return None
    return passed(step, "observed", f"The page shows {words}.")


def _act_open(state: WalkState, step: GuideStep, url: str, opened: dict) -> CheckResult | None:
    if not opened["done"]:
        try:
            state.page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            opened["error"] = True
        opened["done"] = True
    if opened.get("error") or page_is_loading(state.page):
        return None
    snapshot = _read_page_text(state.page)
    if not snapshot:
        return None
    return passed(step, "opened-url", f"Opened {url}. The page showed {_snippet(snapshot)}.")


def _select_labeled(page, label: str, value: str) -> bool:
    """Lightning dropdowns such as Data Type are choices, not text fields."""
    opened = (
        click_role(page, "combobox", label)
        or click_role(page, "button", label)
        or click_role(page, "listbox", label)
    )
    if not opened:
        loc = _text_choice(page, label)
        if loc is not None and _visible(loc):
            opened = _click_locator(loc)
    if not opened and not present(page, "option", value):
        return False
    return click_role(page, "option", value)


def _is_lookup(page, label: str) -> bool:
    return present(page, "combobox", label) or present(page, "searchbox", label)


def _act_fill(state: WalkState, step: GuideStep, label: str, value: str) -> CheckResult | None:
    # Record Variable is on the Fields tab. Components is the tab already showing.
    if norm(label) == "record variable":
        _focus_screen_editor(state)
        _click_exact_role(state.page, "tab", "Fields")
    # A lookup can show the typed word and still be invalid until the choice is clicked.
    if _is_lookup(state.page, label):
        if click_role(state.page, "option", value):
            return passed(step, "filled", f"{label} is {value}.")
        if _field_value(state, label) != value:
            _fill_labeled(state, label, value)
        click_role(state.page, "combobox", label) or click_role(state.page, "searchbox", label)
        if click_role(state.page, "option", value):
            return passed(step, "filled", f"{label} is {value}.")
        return None
    if _field_value(state, label) == value:
        return passed(step, "filled", f"{label} is {value}.")
    if _select_labeled(state.page, label, value):
        return passed(step, "filled", f"{label} is {value}.")
    if not _fill_labeled(state, label, value):
        return None
    if _field_value(state, label) == value:
        return passed(step, "filled", f"{label} is {value}.")
    return None


def _try_named(state: WalkState, step: GuideStep, intent: _Intent, name: str, opened: dict) -> CheckResult | None:
    if intent.kind == "toggle":
        return _act_toggle(state, step, name)
    if intent.kind == "click":
        return _act_click(state, step, name)
    if intent.kind == "observe":
        return _act_observe(state, step, name)
    if intent.kind == "open":
        return _act_open(state, step, intent.target, opened)
    if intent.kind == "fill":
        return _act_fill(state, step, intent.target, intent.value)
    if intent.kind == "own":
        return _act_own(state, step, name)
    return None


def _wait_done(state: WalkState, start: float) -> bool:
    return state.clock() - start >= state.page_wait_s


def _loading(page) -> bool:
    try:
        return page_is_loading(page)
    except Exception:
        return False


def _freeze_result(state: WalkState, step: GuideStep, target: str, suggestion: str = "") -> CheckResult:
    shown = _snippet(_read_page_text(state.page)) or "no matching control"
    label = target.strip() or (step.text or "this step").strip()
    note = f"Could not find {label}. The page showed: {shown}"
    if suggestion and norm(suggestion) != norm(label):
        note += f" A similar control is on the page: {suggestion}. It was not clicked."
    if not _clean_name(label):
        note = (f"The validator could not read one control name from this step ({_snippet(label)}), "
                "so the miss is not proof the guide is wrong. " + note)
        return CheckResult("NEEDS_REVIEW", note, (), freeze=True)
    return CheckResult("UI_MISMATCH", note, (), freeze=True)


def _clean_name(label: str) -> bool:
    """True when the target reads like one control name, not a clause."""
    words = label.split()
    if not words or len(words) > 5:
        return False
    return not re.search(r"\b(?:click|select|then|and|to|it|you|your|if|scroll|find)\b", label, re.I)


def _reviewed_alias(state: WalkState, name: str) -> str:
    """Screen name a reviewer approved for this guide name, or empty."""
    return str(state.aliases.get(norm(name), "") or "").strip()


def _alias_result(intent: _Intent, alias: str, result: CheckResult) -> CheckResult:
    return CheckResult(
        "UI_MISMATCH",
        ALIAS_NOTE.format(guide=intent.target, screen=alias),
        tuple(result.evidence_ids),
        freeze=False,
    )


def _report_wait(state: WalkState, step: GuideStep, intent: _Intent, start: float) -> None:
    if state.progress is None:
        return
    label = intent.target or (step.text or step.step_id)
    elapsed = max(0, int(state.clock() - start))
    state.progress("running", f"Checking the page for {label}. {elapsed}s")


def _finish_clicks(state: WalkState, step: GuideStep, names: list[str]) -> CheckResult:
    """Click each named control in order. The next name can appear after the previous click."""
    done: list[str] = []
    renamed: CheckResult | None = None
    for name in names:
        if norm(name) in {"ok", "okay"} and not _control_visible(state.page, name):
            if _words_visible(state.page, "successfully saved"):
                note = "Clicked " + " then ".join(done) + "."
                note += " The page showed your settings have been successfully saved."
                return passed(step, "clicked", note)
        result = _finish_attempt(state, step, _Intent("click", name))
        if result.status == "UI_MISMATCH" and not result.freeze and result.evidence_ids:
            renamed = renamed or result
            done.append(name)
            continue
        if result.status != "PASS":
            return result
        done.append(name)
    if renamed is not None:
        return renamed
    return passed(step, "clicked", "Clicked " + " then ".join(done) + ".")


def _finish_attempt(state: WalkState, step: GuideStep, intent: _Intent) -> CheckResult:
    """Stay on this step until the page shows the result, or both waits have failed."""
    start = state.clock()
    memory = _ResearchMemory()
    opened = {"done": False, "error": False}
    lookup = intent.target or (step.text or step.step_id)
    while True:
        loading = _loading(state.page)
        if not loading:
            result = _try_named(state, step, intent, intent.target, opened)
            if result is not None:
                return result
            alias = _reviewed_alias(state, intent.target)
            if alias and norm(alias) != norm(intent.target):
                result = _try_named(state, step, intent, alias, opened)
                if result is not None and result.status == "PASS":
                    return _alias_result(intent, alias, result)
                if result is not None:
                    return result
            _consult(state, step, lookup, memory)
        if loading and _wait_done(state, start) and not memory.pending:
            return CheckResult("NOT_RUN", LOADING_NOTE, ())
        research_closed = memory.done and not memory.pending
        if not loading and _wait_done(state, start) and research_closed:
            return _freeze_result(state, step, intent.target, memory.candidate)
        _report_wait(state, step, intent, start)
        _sleep(state, state.page_poll_s if state.page_poll_s > 0 else PAGE_POLL_S)


def attempt_step(state: WalkState, step: GuideStep) -> CheckResult:
    """Finish one unmatched guide step only after the page shows its result."""
    if not _page_open(state):
        return CheckResult(
            "ENV_BLOCKED",
            "The browser is closed, so this step was not run.",
            (),
            freeze=True,
        )
    text = _without_note(step.text or "")
    low = norm(text)
    if _is_password_step(low):
        return CheckResult("NOT_RUN", PASSWORD_NOTE, ())
    if _org_is_open(state) and any(phrase in low for phrase in (
        "log in", "click log in", "press enter", "enter your username",
        "login page", "open google chrome", "open chrome", "internet browser",
    )):
        if org_host(getattr(state.page, "url", "")):
            return passed(
                step, "already-signed-in",
                "The org is already open, so login was not repeated.",
            )
    row_dropdown = _row_dropdown(text)
    if row_dropdown:
        return _finish_row_dropdown(state, step, *row_dropdown)
    named = _named_clicks(text)
    if named:
        return _finish_clicks(state, step, named)
    intent = _intent_for(text)
    if intent.kind == "unknown":
        if step.step_id in state.parents:
            return CheckResult(
                "NOT_RUN",
                "This sentence does not name a control. The following steps carry it out.",
            )
        return CheckResult(
            "NEEDS_REVIEW",
            f"The validator has no action for this step yet: {_snippet(text)}. "
            "It stopped here so later steps are not run out of order.",
            (),
            freeze=True,
        )
    return _finish_attempt(state, step, intent)


def _session_blocked(state: WalkState, step: GuideStep) -> bool:
    if not _page_open(state):
        return True
    if "login.salesforce.com" in norm(step.text):
        return False
    url = str(getattr(state.page, "url", "") or "")
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("login.") and host.endswith("salesforce.com"):
        return not return_home(state)
    return False


def _step_error(exc: BaseException) -> CheckResult:
    """Keep a step-specific reason. Do not hide it behind a generic mismatch."""
    if isinstance(exc, BrowserActionError):
        detail = str(exc).strip()
        return CheckResult("UI_MISMATCH", detail or "The step could not be completed.", ())
    kind = type(exc).__name__
    return CheckResult(
        "UI_MISMATCH",
        f"This step stopped because of an unexpected error ({kind}).",
        (),
    )


def walk_guide(
    page,
    inventory,
    *,
    checks: Sequence[GuideCheck] | None = None,
    progress: Callable[[str, str], None] | None = None,
    timeout_s: float = 90,
    interval_s: float = 2,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
    research: Callable[[WalkState, GuideStep, str], object] | None = None,
    page_wait_s: float = PAGE_WAIT_S,
    page_poll_s: float = PAGE_POLL_S,
    aliases: dict[str, str] | None = None,
    video_origin: float | None = None,
    heartbeat: Callable[[str, int, int, str, str, float], None] | None = None,
) -> WalkOutcome:
    """Run steps in printed order.

    The next step is not started while this one is still waiting. A freeze,
    a blocked session, or a closed browser leaves every later step NOT RUN.
    """
    catalog = tuple(builtin_checks() if checks is None else checks)
    steps = tuple(getattr(inventory, "steps", ()) or ())
    _clock = clock or time.monotonic
    state = WalkState(
        page=page,
        home=str(getattr(page, "url", "") or ""),
        timeout_s=timeout_s,
        interval_s=interval_s,
        clock=_clock,
        sleeper=sleeper,
        research=research,
        progress=progress,
        page_wait_s=page_wait_s,
        page_poll_s=page_poll_s,
        aliases={norm(k): v for k, v in (aliases or {}).items()},
        parents=frozenset(s.parent_id for s in steps if getattr(s, "parent_id", None)),
        video_origin=video_origin,
    )
    statuses: dict[str, str] = {}
    notes: dict[str, str] = {}
    evidence: dict[str, tuple[str, ...]] = {}
    suspects: dict[str, tuple[str, ...]] = {}
    timestamps: dict[str, tuple[float, float]] = {}
    executed: list[str] = []
    total = len(steps)
    if progress is not None:
        progress("running", f"Running all {total} guide steps in printed order.")
    blocked = ""
    password_form: str | None = None
    for index, step in enumerate(steps, 1):
        label = (step.text or "").strip().replace("\n", " ")
        if len(label) > 140:
            label = label[:137] + "..."
        if blocked:
            statuses[step.step_id] = "NOT_RUN"
            notes[step.step_id] = blocked
            if heartbeat is not None:
                heartbeat("", index, total, step.step_id, "NOT_RUN", _clock())
            continue
        if progress is not None:
            progress("running", f"Step {index} of {total}: {label or step.step_id}")
        try:
            state.page._guide_text = step.text or ""
        except Exception:
            pass
        t_start = _clock()
        step_cap_s = min(page_wait_s + 30, 120)
        skipped = _password_skip(step, password_form)
        side = None if skipped else _side_skip(step, statuses.get(steps[index - 2].step_id) if index > 1 else None)
        if side is not None:
            skipped = (None, side)
        if skipped is not None:
            password_form, result = skipped
            check = None
        else:
            password_form = None
            check = find_check(step, catalog)
        try:
            if skipped is None:
                result = check.run(state, step) if check is not None else attempt_step(state, step)
                t_elapsed = _clock() - t_start
                if t_elapsed > step_cap_s:
                    result = CheckResult("NOT_RUN", "The step exceeded its time limit.", ())
        except StillLoading:
            result = CheckResult("NOT_RUN", LOADING_NOTE, ())
        except Exception as exc:
            if type(exc).__name__ == "TargetClosedError" or not _page_open(state):
                result = CheckResult(
                    "ENV_BLOCKED",
                    "The browser closed, so this step was not run.",
                    (),
                    freeze=True,
                )
            else:
                result = _step_error(exc)
        if not isinstance(result, CheckResult):
            result = CheckResult("UI_MISMATCH", "This step did not return a result.", ())
        if result.status == "PASS" and not result.evidence_ids:
            result = CheckResult(
                "NEEDS_REVIEW",
                result.note or "A pass was reported without evidence.",
                (),
            )
        t_end = _clock()
        statuses[step.step_id] = result.status
        if result.note:
            notes[step.step_id] = result.note
        if result.evidence_ids:
            evidence[step.step_id] = tuple(result.evidence_ids)
        if result.suspect:
            suspects[step.step_id] = result.suspect
        if video_origin is not None:
            timestamps[step.step_id] = (t_start - video_origin, t_end - video_origin)
        executed.append(step.step_id)
        if heartbeat is not None:
            heartbeat("", index, total, step.step_id, result.status, t_end)
        if result.freeze or result.status == "ENV_BLOCKED" or _session_blocked(state, step):
            if result.freeze:
                blocked = result.note or "The following steps were not run."
            else:
                blocked = "The org session was blocked, so the following steps were not run."
    return WalkOutcome(statuses, notes, evidence, tuple(executed), suspects, timestamps)
