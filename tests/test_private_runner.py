from contextlib import contextmanager
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.browser_worker import UIAssertion
from workshop_validator.evidence import AssertionSpec, Observation
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.pipeline import PlannedStep
from workshop_validator.guide_walk import CheckResult, GuideCheck
from workshop_validator.private_runner import (
    PageStillLoading, PrivateRunnerError, executable_plans, load_plan,
    load_source_review, login_to_salesforce, playwright_session,
    refuse_unsettled, run_uploaded_guide)
import inspect


class PrivatePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=os.path.join(self.tmp.name,'plan.json')

    def write(self, guide_sha256='a'*64, steps=None):
        data={"schema_version":1,"guide_sha256":guide_sha256,
              "steps":steps if steps is not None else [{
                "step_id":"S001","page":1,"title":"Synthetic save",
                "actions":[{"kind":"click","role":"button","accessible_name":"Save"}],
                "assertions":[{"assertion_id":"visible","kind":"DOM_VISIBLE",
                               "expected":True,"role":"status",
                               "accessible_name":"Save status"}]
              }]}
        with open(self.path,'w') as f:json.dump(data,f)

    def test_parse_pinned_guide_and_source_anchors(self):
        self.write()
        steps=load_plan(self.path,pdf_digest='a'*64,page_count=4)
        self.assertEqual(steps[0].step_id,'S001')
        self.assertEqual(steps[0].actions[0].kind,'click')
        self.assertTrue(steps[0].assertions[0].spec.expected)

    def test_wrong_guide_hash_is_rejected(self):
        self.write()
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='b'*64,page_count=4)

    def test_invalid_page_and_unsafe_action_rejected(self):
        self.write(steps=[{"step_id":"S01","page":99,"title":"Example",
             "actions":[{"kind":"javascript","role":"button","accessible_name":"Save"}],
             "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='a'*64,page_count=4)
        self.write(steps=[{"step_id":"S01","page":1,"title":"Example",
             "actions":[{"kind":"javascript","role":"button","accessible_name":"Save"}],
             "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='a'*64,page_count=4)



    def test_inline_note_placement_uses_private_explicit_anchors(self):
        from workshop_validator.private_runner import build_inline_note_placements
        from workshop_validator.pdf_annotations import ReviewNote
        self.write(steps=[{"step_id":"S001","page":1,
            "title":"Synthetic instruction", "inline_after_y":140,
            "actions":[{"kind":"click","role":"button","accessible_name":"Save"}],
            "assertions":[{"assertion_id":"ok","kind":"DOM_VISIBLE","expected":True,
                           "role":"status","accessible_name":"Status"}]}])
        note=ReviewNote(1,"S001","NEEDS_REVIEW","Synthetic step","Needs testing")
        placements=build_inline_note_placements(self.path,[note])
        self.assertEqual(len(placements),1)
        self.assertEqual(placements[0].insert_after_y,140)
        self.assertIn("behavioral verification",placements[0].summary)

    def test_missing_inline_anchor_is_rejected_not_placed_elsewhere(self):
        from workshop_validator.private_runner import build_inline_note_placements
        from workshop_validator.pdf_annotations import ReviewNote
        self.write()
        note=ReviewNote(1,"S001","NEEDS_REVIEW","Step","Missing evidence")
        with self.assertRaises(PrivateRunnerError):
            build_inline_note_placements(self.path,[note])

    def test_malformed_numeric_source_anchor_is_rejected(self):
        self.write(steps=[{"step_id":"S001","page":1,"title":"Step",
            "inline_after_y":"not-a-coordinate", "actions":[], "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest="a"*64,page_count=4)

try:
    from playwright.sync_api import TimeoutError as _PlaywrightTimeout
except ImportError:
    _PlaywrightTimeout = TimeoutError

_USERNAME_BUTTON = "Log In with Username"


class _El:
    """One fake DOM element. Presence and visibility follow the page state."""

    def __init__(self, page, selector, label, present, visible):
        self.page = page
        self.selector = selector
        self.label = label
        self._present = present
        self._visible = visible

    @property
    def present(self):
        return self._present()

    @property
    def visible(self):
        return self._visible()


class _Loc:
    """Fake locator with real Playwright semantics.

    count() counts hidden matches too, first is the first match in DOM order,
    visibility is per element, filter(visible=True) keeps only visible
    matches, and is_visible/wait_for/click/fill are strict (one match).
    """

    def __init__(self, page, label, match, visible_only=False, nth=None):
        self.page = page
        self.label = label
        self.match = match
        self.visible_only = visible_only
        self.nth = nth

    def _matches(self):
        found = [el for el in self.page.elements() if self.match(el)]
        if self.visible_only:
            found = [el for el in found if el.visible]
        if self.nth is not None:
            found = found[self.nth:self.nth + 1]
        return found

    def _one(self):
        found = self._matches()
        if len(found) > 1:
            raise AssertionError(
                "strict mode: %s matched %d elements" % (self.label, len(found))
            )
        return found[0] if found else None

    def filter(self, visible=None, **kwargs):
        if kwargs or visible is not True:
            raise AssertionError("fake supports only filter(visible=True)")
        return _Loc(self.page, self.label, self.match, True, self.nth)

    def or_(self, other):
        def match(el):
            return any(el is x for x in self._matches()) or any(
                el is x for x in other._matches())
        return _Loc(self.page, "or", match)

    @property
    def first(self):
        return _Loc(self.page, self.label,
                    lambda el: any(el is x for x in self._matches()), nth=0)

    def count(self):
        return len(self._matches())

    def is_visible(self):
        el = self._one()
        return bool(el and el.visible)

    def inner_text(self, timeout=None):
        if self.label == "body":
            return ""
        raise AssertionError("fake has no text for %s" % self.label)

    def wait_for(self, state=None, timeout=None):
        self.page.waits.append((self.label, timeout))
        # Waiting lets a slow page finish rendering.
        self.page.rendered = True
        el = self._one()
        if el is None or not el.visible:
            raise _PlaywrightTimeout("locator did not become visible")

    def fill(self, value, timeout=None):
        el = self._one()
        self.page.events.append(("fill", self.label))
        if el is not None and el.selector == "#password" and not el.visible:
            raise AssertionError("filled a hidden password field")
        if el is None or not el.visible:
            raise _PlaywrightTimeout("%s not visible" % self.label)

    def click(self, timeout=None):
        el = self._one()
        if el is None or not el.visible:
            raise _PlaywrightTimeout("%s not visible" % self.label)
        self.page.events.append(("click", el.label))
        self.page.clicked(el)


class _Page:
    def __init__(self, combined_form=False, promo_blocked=False, email_screen=False,
                 has_username_button=True, slow_render=False,
                 username_after_click=True, username_button_count=1,
                 hidden_username_first=True, wall_polls=0):
        self.events = []
        self.wall_polls = wall_polls
        self.on_click = None
        self.waits = []
        self.role_queries = []
        self.password_visible = False
        self.combined_form = combined_form
        self.promo_blocked = promo_blocked
        self.promo_ignored = False
        self.email_screen = email_screen
        self.has_username_button = has_username_button
        self.username_visible = not email_screen
        self.rendered = not slow_render
        self.username_after_click = username_after_click
        self.username_button_count = username_button_count
        self.navigated = False
        self.url = "https://example.lightning.force.com/lightning/page/home"
        # DOM order on the email card: the hidden username form comes first,
        # then the Log In with Username button(s), as Salesforce renders it.
        on_card = lambda: self.email_screen and self.has_username_button
        username = _El(
            self, "#username", "#username",
            lambda: not self.email_screen or hidden_username_first
            or self.username_visible,
            lambda: self.username_visible,
        )
        buttons = [
            _El(self, None, "button:" + _USERNAME_BUTTON,
                lambda i=i: on_card() and i < self.username_button_count,
                lambda: True)
            for i in range(max(username_button_count, 1))
        ]
        password = _El(self, "#password", "#password", lambda: True,
                       lambda: self.password_visible or self.combined_form)
        login = _El(self, "#Login", "#Login", lambda: True,
                    lambda: self.username_visible)
        self._dom = [username, *buttons, password, login]
        # The org's own login wall after sign-in, shown for wall_polls polls.
        self._wall = [_El(self, "#username", "#username", lambda: True,
                          lambda: True)]

    def elements(self):
        if self.navigated:
            return self._wall if self.wall_polls > 0 else []
        if not self.rendered:
            return []
        return [el for el in self._dom if el.present]

    def wait_for_timeout(self, ms):
        self.waits.append(("timeout", ms))
        self.wall_polls -= 1

    def clicked(self, el):
        if self.on_click is not None:
            self.on_click(el.label)
        if el.selector is None:
            if self.email_screen and self.username_after_click:
                self.username_visible = True
        elif el.selector == "#Login":
            if self.password_visible or self.combined_form:
                self.navigated = True
            else:
                self.password_visible = True

    def route(self, pattern, handler=None, **kwargs):
        text = pattern if isinstance(pattern, str) else getattr(pattern, "pattern", "")
        if "promos.html" in str(text).replace("\\", "").lower():
            self.promo_ignored = True

    def goto(self, url, wait_until=None, timeout=None):
        self.events.append(("goto", url))
        if self.promo_blocked and not self.promo_ignored:
            raise RuntimeError(
                "net::ERR_ABORTED https://login.salesforce.com/promos.html"
            )

    def locator(self, selector):
        return _Loc(self, selector, lambda el: el.selector == selector)

    def get_by_role(self, role, name=None, exact=False):
        self.role_queries.append((role, name, exact))
        # Real get_by_role skips hidden elements by default.
        return _Loc(self, "button:%s" % name, lambda el: (
            role == "button" and name == _USERNAME_BUTTON and exact is True
            and el.selector is None and el.visible))

    def wait_for_url(self, predicate, timeout=None):
        if not predicate(self.url):
            raise AssertionError("still on the login host")


@unittest.skipUnless(sync_playwright is not None, "Playwright required for login adapter tests")
class LoginAdapterTests(unittest.TestCase):
    def test_username_first_form_clicks_before_password(self):
        page = _Page(combined_form=False)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])

    def test_combined_form_does_not_submit_username_alone(self):
        page = _Page(combined_form=True)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])

    def test_missing_secret_fails_before_navigation(self):
        page = _Page()
        with self.assertRaises(PrivateRunnerError):
            login_to_salesforce(page, "", "secret-value")
        self.assertEqual(page.events, [])

    def test_blocked_promo_iframe_does_not_fail_login(self):
        page = _Page(promo_blocked=True)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertTrue(page.promo_ignored)
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertEqual(self._password_submits(page), [("click", "#Login")])
        self.assertEqual(
            [event for event in page.events if event[0] == "goto"],
            [("goto", "https://login.salesforce.com/")],
        )
        self.assertFalse(any(event[0] == "reload" for event in page.events))

    def test_password_login_is_submitted_once(self):
        username_first = _Page(combined_form=False)
        login_to_salesforce(username_first, "learner@example.com", "secret-value")
        self.assertEqual(self._password_submits(username_first), [("click", "#Login")])
        combined = _Page(combined_form=True)
        login_to_salesforce(combined, "learner@example.com", "secret-value")
        self.assertEqual(combined.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertEqual(self._password_submits(combined), [("click", "#Login")])

    def test_email_screen_clicks_username_button_first(self):
        page = _Page(email_screen=True, has_username_button=True)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("click", "button:Log In with Username"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertTrue(page.role_queries)
        # The live card renders the control as a link; a button still counts.
        self.assertEqual({role for role, _, _ in page.role_queries},
                         {"link", "button"})
        for role, name, exact in page.role_queries:
            self.assertEqual(name, "Log In with Username")
            self.assertIs(exact, True)

    def test_classic_screen_skips_username_button(self):
        page = _Page(combined_form=False, email_screen=False)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertNotIn(("click", "button:Log In with Username"), page.events)

    def test_email_screen_without_username_button_fails(self):
        page = _Page(email_screen=True, has_username_button=False)
        with self.assertRaises(PrivateRunnerError) as ctx:
            login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertIn(
            "neither the username form nor the Log In with Username button appeared",
            str(ctx.exception),
        )
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
        ])

    def test_slow_classic_screen_waits_then_signs_in(self):
        page = _Page(slow_render=True)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertEqual(page.waits[0][0], "or")
        self.assertGreaterEqual(page.waits[0][1], 25000)

    def test_slow_email_screen_waits_then_clicks_username_button(self):
        page = _Page(slow_render=True, email_screen=True)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("click", "button:Log In with Username"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertEqual(page.waits[0][0], "or")

    def test_username_form_missing_after_button_names_the_screen(self):
        page = _Page(email_screen=True, username_after_click=False)
        with self.assertRaises(PrivateRunnerError) as ctx:
            login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(
            str(ctx.exception),
            "Salesforce login page showed the Log In with Email screen; the "
            "username form did not appear after Log In with Username",
        )
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("click", "button:Log In with Username"),
        ])
        self.assertFalse([event for event in page.events if event[0] == "fill"])

    def test_two_username_buttons_refuse_without_clicking(self):
        page = _Page(email_screen=True, username_button_count=2)
        with self.assertRaises(PrivateRunnerError) as ctx:
            login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertIn("more than one Log In with Username button", str(ctx.exception))
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
        ])
        self.assertFalse([event for event in page.events if event[0] in ("click", "fill")])

    def test_hidden_username_ahead_of_button_is_counted_but_not_used(self):
        page = _Page(email_screen=True, hidden_username_first=True)
        self.assertEqual(page.locator("#username").count(), 1)
        self.assertFalse(page.locator("#username").is_visible())
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events[:3], [
            ("goto", "https://login.salesforce.com/"),
            ("click", "button:Log In with Username"),
            ("fill", "#username"),
        ])

    def test_login_waits_share_one_deadline(self):
        clock = [1000.0]
        page = _Page(email_screen=True)

        def advance(label):
            if label == "button:" + _USERNAME_BUTTON:
                clock[0] = 1140.0
        page.on_click = advance
        with patch("workshop_validator.private_runner._login_clock",
                   lambda: clock[0]):
            login_to_salesforce(page, "learner@example.com", "secret-value")
        # 140 s used of 150 s: the form wait after the click gets 10 s, not 20 s.
        self.assertEqual(page.waits[0], ("or", 30000))
        self.assertEqual(page.waits[1], ("#username", 10000))

    def test_spent_deadline_stops_before_password(self):
        clock = [1000.0]
        page = _Page(email_screen=True)

        def advance(label):
            if label == "#Login":
                clock[0] = 1160.0
        page.on_click = advance
        with patch("workshop_validator.private_runner._login_clock",
                   lambda: clock[0]):
            with self.assertRaises(PrivateRunnerError) as ctx:
                login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(
            str(ctx.exception),
            "Salesforce login timed out or requires interactive authentication",
        )
        self.assertNotIn(("fill", "#password"), page.events)

    def test_org_login_wall_is_polled_until_gone(self):
        page = _Page(wall_polls=2)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(
            [wait for wait in page.waits if wait[0] == "timeout"],
            [("timeout", 2000), ("timeout", 2000)],
        )

    def test_org_login_wall_that_stays_fails_after_45_seconds(self):
        clock = [1000.0]
        page = _Page(wall_polls=10 ** 6)
        original = page.wait_for_timeout

        def tick(ms):
            original(ms)
            clock[0] += ms / 1000
        page.wait_for_timeout = tick
        with patch("workshop_validator.private_runner._login_clock",
                   lambda: clock[0]):
            with self.assertRaises(PrivateRunnerError) as ctx:
                login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(
            str(ctx.exception),
            "Salesforce login timed out or requires interactive authentication",
        )
        polls = [wait for wait in page.waits if wait[0] == "timeout"]
        self.assertEqual(len(polls), 23)

    @staticmethod
    def _password_submits(page):
        filled = page.events.index(("fill", "#password"))
        return [event for event in page.events[filled:] if event == ("click", "#Login")]


_SMOKE_LOGIN_URL = "https://login.example.test/"
_SMOKE_HOME_URL = "https://example.lightning.force.com/lightning/page/home"

# Synthetic login pages. #Login reveals #password first, then goes to the
# Lightning home with the filled values and the button click count.
_SMOKE_SCRIPT = """
<script>
window.__usernameClicks = 0;
function showForm() {
  document.getElementById('username').style.display = '';
  document.getElementById('Login').style.display = '';
}
function usernameButton(delayMs) {
  window.__usernameClicks += 1;
  if (delayMs) { setTimeout(showForm, delayMs); } else { showForm(); }
}
function submitLogin() {
  var pw = document.getElementById('password');
  if (pw.style.display === 'none') { pw.style.display = ''; return; }
  var q = new URLSearchParams({
    u: document.getElementById('username').value,
    p: pw.value,
    clicks: String(window.__usernameClicks)
  });
  location.href = '%s?' + q.toString();
}
</script>
""" % _SMOKE_HOME_URL

_SMOKE_FORM = """
<input id="username" style="%s">
<input id="password" type="password" style="display:none">
<button id="Login" type="button" style="%s" onclick="submitLogin()">Log In</button>
"""


def _smoke_page(body, delay_card_ms=0):
    if delay_card_ms:
        body = (
            '<div id="late"></div><script>setTimeout(function(){'
            'document.getElementById("late").outerHTML = %s;}, %d);</script>'
            % (json.dumps(body), delay_card_ms)
        )
    return "<html><head>%s</head><body>%s</body></html>" % (_SMOKE_SCRIPT, body)


def _smoke_classic():
    # Classic form plus a visible Log In with Username button that must
    # never be clicked while the form is showing.
    return _smoke_page(
        (_SMOKE_FORM % ("", "")) +
        '<button type="button" onclick="usernameButton(0)">'
        'Log In with Username</button>'
    )


def _smoke_email_card(buttons=1, form_delay_ms=0, card_delay_ms=0):
    # Hidden username form placed BEFORE the visible button(s).
    card = (_SMOKE_FORM % ("display:none", "display:none")) + "".join(
        '<button type="button" onclick="usernameButton(%d)">'
        'Log In with Username</button>' % form_delay_ms
        for _ in range(buttons)
    ) + '<input id="email" placeholder="Email">'
    return _smoke_page(card, delay_card_ms=card_delay_ms)


# Synthetic email card inside three nested open shadow roots, like the live
# page. It leads to a username-first form: #username and #Login, with the
# password box shown only after the username is submitted. The query string
# reports control clicks, Login submits and anything typed into Email.
_SHADOW_SCRIPT = """
<script>
window.__usernameClicks = 0;
window.__continueClicks = 0;
window.__loginSubmits = 0;
function showForm() {
  document.getElementById('username').style.display = '';
  document.getElementById('Login').style.display = '';
}
function usernameControl() {
  window.__usernameClicks += 1;
  document.getElementById('shell').style.display = 'none';
  showForm();
}
function emailValue() {
  var root = document.getElementById('shell').shadowRoot
    .querySelector('x-mid').shadowRoot
    .querySelector('x-inner').shadowRoot;
  return root.getElementById('email').value;
}
function submitLogin() {
  window.__loginSubmits += 1;
  var pw = document.getElementById('password');
  if (pw.style.display === 'none') {
    if (window.__passwordNever) { return; }
    pw.style.display = '';
    return;
  }
  var q = new URLSearchParams({
    u: document.getElementById('username').value,
    p: pw.value,
    clicks: String(window.__usernameClicks),
    submits: String(window.__loginSubmits),
    cont: String(window.__continueClicks),
    email: 'v:' + emailValue()
  });
  location.href = '%s?' + q.toString();
}
function nest(host, tag, html) {
  var root = host.attachShadow({mode: 'open'});
  if (tag) {
    var child = document.createElement(tag);
    root.appendChild(child);
    return child;
  }
  root.innerHTML = html;
  return null;
}
</script>
""" % _SMOKE_HOME_URL

_SHADOW_LINK = (
    '<a class="slds-button" href="#" '
    'onclick="usernameControl(); return false;">'
    '<svg aria-hidden="true" width="1" height="1"></svg>'
    'Log In with Username</a>'
)
_SHADOW_BUTTON = (
    '<button type="button" onclick="usernameControl()">'
    'Log In with Username</button>'
)


def _shadow_card_page(control=_SHADOW_LINK, controls=1, password_never=False,
                      password_shown=False):
    card = (
        '<h2>Log In with Email</h2>'
        '<label>Email <input id="email" type="email"></label>'
        '<label><input type="checkbox"> Remember Me</label>'
        '<button type="button" onclick="window.__continueClicks += 1">'
        'Continue</button><span>or</span>' + control * controls
    )
    pw_style = "" if password_shown else "display:none"
    form = (
        '<input id="username" type="email" style="display:none">'
        '<input id="password" type="password" style="%s">'
        '<input id="Login" type="submit" value="Log In" style="display:none"'
        ' onclick="submitLogin(); return false;">' % pw_style
    )
    build = (
        "<script>window.__passwordNever = %s;"
        "var mid = nest(document.getElementById('shell'), 'x-mid');"
        "var inner = nest(mid, 'x-inner');"
        "nest(inner, null, %s);</script>"
        % ("true" if password_never else "false", json.dumps(card))
    )
    return "<html><head>%s</head><body><x-shell id=\"shell\"></x-shell>%s%s" \
        "</body></html>" % (_SHADOW_SCRIPT, form, build)


def _username_first_page(password_never=False, password_shown=False):
    # Plain visit: the classic form is shown directly, no card.
    page = _shadow_card_page(password_never=password_never,
                             password_shown=password_shown)
    return page.replace(
        "</body>",
        "<script>document.getElementById('shell').style.display='none';"
        "showForm();</script></body>",
    )


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class LoginBrowserSmokeTests(unittest.TestCase):
    """Real Chromium, synthetic HTML served by page.route. No network."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get("CHROMIUM_PATH") or None
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, login_html):
        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        self.requests = []

        def serve(route):
            url = route.request.url
            self.requests.append(url)
            if url == _SMOKE_LOGIN_URL:
                route.fulfill(status=200, content_type="text/html", body=login_html)
            elif url.startswith(_SMOKE_HOME_URL):
                route.fulfill(status=200, content_type="text/html",
                              body="<html><body><h1>Home</h1></body></html>")
            else:
                route.abort()

        context.route("**/*", serve)
        return page

    def signed_in(self, page):
        from urllib.parse import parse_qs, urlparse as parse
        self.assertTrue(page.url.startswith(_SMOKE_HOME_URL), page.url)
        query = parse_qs(parse(page.url).query)
        return {key: values[0] for key, values in query.items()}

    def test_classic_form_fills_fields_and_never_clicks_username_button(self):
        page = self.open(_smoke_classic())
        login_to_salesforce(page, "learner@example.com", "synthetic-pass",
                            login_url=_SMOKE_LOGIN_URL)
        self.assertEqual(self.signed_in(page), {
            "u": "learner@example.com", "p": "synthetic-pass", "clicks": "0",
        })

    def test_hidden_username_before_visible_button_clicks_button(self):
        page = self.open(_smoke_email_card())
        login_to_salesforce(page, "learner@example.com", "synthetic-pass",
                            login_url=_SMOKE_LOGIN_URL)
        self.assertEqual(self.signed_in(page), {
            "u": "learner@example.com", "p": "synthetic-pass", "clicks": "1",
        })

    def test_email_card_with_late_form_still_signs_in(self):
        # The card itself is built by script after 1 s, and the username
        # form appears 3 s after the button click.
        page = self.open(_smoke_email_card(form_delay_ms=3000, card_delay_ms=1000))
        login_to_salesforce(page, "learner@example.com", "synthetic-pass",
                            login_url=_SMOKE_LOGIN_URL)
        self.assertEqual(self.signed_in(page), {
            "u": "learner@example.com", "p": "synthetic-pass", "clicks": "1",
        })

    def test_two_visible_username_buttons_refuse_without_clicking(self):
        page = self.open(_smoke_email_card(buttons=2))
        with self.assertRaises(PrivateRunnerError) as ctx:
            login_to_salesforce(page, "learner@example.com", "synthetic-pass",
                                login_url=_SMOKE_LOGIN_URL)
        self.assertEqual(
            str(ctx.exception),
            "Salesforce login page showed more than one Log In with Username "
            "button; nothing was clicked",
        )
        self.assertEqual(page.url, _SMOKE_LOGIN_URL)
        self.assertEqual(page.evaluate("window.__usernameClicks"), 0)
        self.assertFalse(page.locator("#username").is_visible())
        self.assertEqual(page.locator("#username").input_value(), "")

    def _signs_in_through_shadow_card(self, control):
        page = self.open(_shadow_card_page(control=control))
        login_to_salesforce(page, "learner@example.com", "synthetic-pass",
                            login_url=_SMOKE_LOGIN_URL)
        # One control click, username submit then password submit, Email
        # untouched and Continue never clicked.
        self.assertEqual(self.signed_in(page), {
            "u": "learner@example.com", "p": "synthetic-pass", "clicks": "1",
            "submits": "2", "cont": "0", "email": "v:",
        })

    def test_shadow_card_username_link_leads_to_username_first_form(self):
        self._signs_in_through_shadow_card(_SHADOW_LINK)

    def test_shadow_card_username_button_leads_to_username_first_form(self):
        self._signs_in_through_shadow_card(_SHADOW_BUTTON)

    def test_two_visible_username_links_refuse_without_clicking(self):
        page = self.open(_shadow_card_page(controls=2))
        with self.assertRaises(PrivateRunnerError) as ctx:
            login_to_salesforce(page, "learner@example.com", "synthetic-pass",
                                login_url=_SMOKE_LOGIN_URL)
        self.assertEqual(
            str(ctx.exception),
            "Salesforce login page showed more than one Log In with Username "
            "button; nothing was clicked",
        )
        self.assertEqual(page.url, _SMOKE_LOGIN_URL)
        self.assertEqual(page.evaluate("window.__usernameClicks"), 0)
        self.assertEqual(page.evaluate("window.__continueClicks"), 0)
        self.assertEqual(page.evaluate("window.__loginSubmits"), 0)
        self.assertEqual(page.evaluate("emailValue()"), "")

    def test_combined_form_with_password_visible_submits_once(self):
        page = self.open(_username_first_page(password_shown=True))
        login_to_salesforce(page, "learner@example.com", "synthetic-pass",
                            login_url=_SMOKE_LOGIN_URL)
        self.assertEqual(self.signed_in(page), {
            "u": "learner@example.com", "p": "synthetic-pass", "clicks": "0",
            "submits": "1", "cont": "0", "email": "v:",
        })

    def test_password_box_never_shown_names_the_username_step(self):
        page = self.open(_username_first_page(password_never=True))
        # A short shared budget keeps the test quick; the password wait
        # itself must time out, not the budget check.
        with patch("workshop_validator.private_runner.LOGIN_WAIT_SECONDS", 8):
            with self.assertRaises(PrivateRunnerError) as ctx:
                login_to_salesforce(page, "learner@example.com",
                                    "synthetic-pass", login_url=_SMOKE_LOGIN_URL)
        self.assertEqual(
            str(ctx.exception),
            "Salesforce login page: the password box did not appear after "
            "the username step",
        )
        self.assertEqual(page.evaluate("window.__loginSubmits"), 1)
        self.assertEqual(page.locator("#password").input_value(), "")


def guide_step(step_id, number="1", text="Open the page"):
    return GuideStep(
        step_id, 1, "Exercise 1", "Task 1: Start", number, text,
        (90.0, 10.0, 200.0, 24.0), None, (),
    )


def one_plan(step_id="P001N001"):
    return PlannedStep(
        step_id, 1, "Open the page", (),
        (UIAssertion(AssertionSpec("visible", "DOM_VISIBLE", True), "button", "Save"),),
    )


class UploadedGuideTests(unittest.TestCase):
    def test_no_authored_check_stays_not_run_after_sign_in(self):
        calls = []

        def login(username, password):
            calls.append((username, password))

        inventory = GuideInventory(1, (guide_step("P001N001"), guide_step("P001N002", "2", "Save")), (), ())
        folder = Path(self.tmp.name) / "report"
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=login, output_dir=folder, login_budget=5,
        )
        self.assertEqual(calls, [("learner@example.com", "secret-value")])
        self.assertTrue(result.signed_in)
        self.assertEqual(result.checks_executed, 0)
        self.assertEqual(executable_plans(inventory), ())
        self.assertEqual([step.status for step in result.report_steps], ["NOT_RUN", "NOT_RUN"])
        status = json.loads((folder / "status.json").read_text())
        self.assertEqual(status["checks_executed"], 0)
        self.assertNotIn("secret-value", json.dumps(status))
        self.assertNotIn("learner@example.com", json.dumps(status))
        report = (folder / "validation-summary.html").read_text()
        self.assertIn("INCOMPLETE", report)
        self.assertNotIn("secret-value", report)
        self.assertNotIn(">VERIFIED<", report)

    def test_sign_in_failure_does_not_run_checks_or_fail_steps(self):
        def boom(username, password):
            raise PrivateRunnerError("Salesforce authentication failed; no steps verified")

        def execute_step(page, step, context):
            raise AssertionError("checks must not run after a failed sign-in")

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=boom, plans=(one_plan(),), execute_step=execute_step, login_budget=5,
        )
        self.assertFalse(result.signed_in)
        self.assertEqual(result.checks_executed, 0)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")
        self.assertNotIn("secret-value", result.message)

    def test_login_timeout_is_bounded_and_not_a_guide_failure(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hang(username, password):
            release.wait(10)

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=hang, login_budget=0,
        )
        self.assertFalse(result.signed_in)
        self.assertIn("timed out", result.message)
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")

    def test_pipeline_pass_keeps_unplanned_steps_not_run(self):
        def login(username, password):
            return None

        def execute_step(page, step, context):
            spec = step.assertions[0].spec
            return [Observation(
                evidence_id=f"{context.run_id}:{context.step_id}:dom:0",
                assertion_id=spec.assertion_id,
                kind=spec.kind,
                actual=True,
                run_id=context.run_id,
                org_id=context.org_id,
                step_id=context.step_id,
                collector_id=context.executor_id,
            )]

        inventory = GuideInventory(1, (
            guide_step("P001N001"),
            guide_step("P001N002", "2", "Save the page"),
        ), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=login, plans=(one_plan(),), execute_step=execute_step, login_budget=5,
        )
        by_id = {step.step_id: step for step in result.report_steps}
        self.assertEqual(by_id["P001N001"].status, "PASS")
        self.assertTrue(by_id["P001N001"].evidence_ids)
        self.assertEqual(by_id["P001N002"].status, "NOT_RUN")
        self.assertEqual(result.checks_executed, 1)
        self.assertNotIn("secret-value", by_id["P001N001"].note)

    def test_still_loading_is_not_a_failure(self):
        def execute_step(page, step, context):
            raise PageStillLoading()

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=lambda username, password: None,
            plans=(one_plan(),), execute_step=execute_step, login_budget=5,
        )
        step = result.report_steps[0]
        self.assertEqual(step.status, "NOT_RUN")
        self.assertIn("still loading", step.note)
        self.assertEqual(result.checks_executed, 0)
        self.assertEqual(refuse_unsettled("FUNCTIONAL_FAIL", still_loading=True), "NOT_RUN")

    def test_default_path_opens_a_playwright_session(self):
        opened = []

        @contextmanager
        def session(username, password, **kwargs):
            opened.append((username, password))
            page = type("Page", (), {"url": "https://example.lightning.force.com/lightning/page/home"})()
            yield page

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        with patch("workshop_validator.private_runner.playwright_session", session):
            result = run_uploaded_guide(
                "learner@example.com", "secret-value", inventory, login_budget=5,
            )
        self.assertEqual(opened, [("learner@example.com", "secret-value")])
        self.assertTrue(result.signed_in)
        self.assertEqual(result.report_steps[0].status, "NEEDS_REVIEW")
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.checks_executed, 1)

    def test_session_sign_in_failure_does_not_fail_guide_steps(self):
        @contextmanager
        def session(username, password):
            raise PrivateRunnerError("Salesforce authentication failed; no steps verified")
            yield None

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory, session=session, login_budget=5,
        )
        self.assertFalse(result.signed_in)
        self.assertIn("authentication failed", result.message)
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")

    def test_session_error_hides_the_password(self):
        @contextmanager
        def session(username, password):
            raise RuntimeError(password)
            yield None

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory, session=session, login_budget=5,
        )
        self.assertFalse(result.signed_in)
        self.assertEqual(result.message, "Sign-in could not finish.")
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")

    def test_session_runs_checks_in_order_and_scrubs_secrets(self):
        page = type("Page", (), {"url": "https://example.lightning.force.com/lightning/page/home"})()

        @contextmanager
        def session(username, password):
            yield page

        def fail(state, step):
            return CheckResult(
                "FUNCTIONAL_FAIL",
                "rejected secret-value",
                (),
            )

        def ok(state, step):
            return CheckResult("PASS", "opened", (f"{step.step_id}:opened",))

        checks = (
            GuideCheck("Fail the first", lambda step: step.number == "1", fail),
            GuideCheck("Pass the second", lambda step: step.number == "2", ok),
        )
        inventory = GuideInventory(1, (
            guide_step("P001N001", "1", "Select Preview"),
            guide_step("P001N002", "2", "Welcome message is shown"),
            guide_step("P001N003", "3", "Do something else"),
        ), (), ())
        folder = Path(self.tmp.name) / "ordered"
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            session=session, checks=checks, output_dir=folder, login_budget=5,
        )
        by_id = {step.step_id: step for step in result.report_steps}
        self.assertEqual(by_id["P001N001"].status, "FUNCTIONAL_FAIL")
        self.assertEqual(by_id["P001N001"].note, "The step note was omitted.")
        self.assertNotIn("secret-value", by_id["P001N001"].note)
        self.assertEqual(by_id["P001N002"].status, "PASS")
        self.assertTrue(by_id["P001N002"].evidence_ids)
        self.assertEqual(by_id["P001N003"].status, "NEEDS_REVIEW")
        self.assertEqual(result.checks_executed, 3)
        report = (folder / "validation-summary.html").read_text()
        self.assertNotIn("secret-value", report)
        self.assertNotIn("learner@example.com", report)

    def test_report_is_not_written_inside_the_repository(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=lambda username, password: None,
            output_dir=root / "inside", repo_root=root, login_budget=5,
        )
        self.assertEqual(list(root.rglob("status.json")), [])
        self.assertEqual(list(root.rglob("*.html")), [])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)


class _StateHarness:
    def __init__(self):
        self.launch_kwargs = None
        self.contexts = []
        self.navigations = []


def _state_playwright(harness):
    class FakePage:
        def __init__(self):
            self.url = "about:blank"

        def set_default_timeout(self, timeout):
            return None

        def goto(self, url, wait_until=None, timeout=None):
            harness.navigations.append(url)
            if getattr(harness, "login_wall", False) and "lightning/page/home" in url:
                self.url = "https://example.my.salesforce.com/"
                self.login_wall = True
            else:
                self.url = url
                self.login_wall = False

        def wait_for_timeout(self, timeout):
            return None

        def locator(self, selector):
            page = self

            class Box:
                def count(self):
                    return 1 if selector == "#username" and getattr(page, "login_wall", False) else 0

                def is_visible(self):
                    return self.count() > 0

            return Box()

        @property
        def video(self):
            return None

    class FakeContext:
        def new_page(self):
            return FakePage()

        def storage_state(self, path=None):
            return {
                "cookies": [{
                    "name": "sid",
                    "value": "fake-session",
                    "domain": "example.my.salesforce.com",
                    "path": "/",
                }],
                "origins": [{
                    "origin": "https://example.lightning.force.com",
                    "localStorage": [],
                }],
            }

        def close(self):
            return None

    class FakeBrowser:
        def new_context(self, **kwargs):
            harness.contexts.append(kwargs)
            return FakeContext()

        def close(self):
            return None

    class FakeChromium:
        def launch(self, **kwargs):
            harness.launch_kwargs = kwargs
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def sync_playwright():
        return FakePlaywright()

    return sync_playwright


@unittest.skipUnless(sync_playwright is not None, "Playwright required for session storage tests")
class StoredSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.private = self.root / "private"
        self.password = "secret-value"
        self.username = "learner@example.com"

    def test_storage_state_is_reused_when_already_logged_in(self):
        harness = _StateHarness()
        login_calls = []

        def fake_login(page, username, password):
            login_calls.append(username)
            page.goto("https://login.salesforce.com/")
            page.url = "https://example.lightning.force.com/lightning/page/home"

        first = self.private / "run-one"
        second = self.private / "run-two"
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=first, repo_root=self.repo,
            ):
                pass
        self.assertEqual(login_calls, [self.username])
        self.assertEqual(
            harness.navigations,
            ["https://login.salesforce.com/"],
        )
        self._assert_headed_with_video(harness, first)
        self.assertNotIn("storage_state", harness.contexts[0])
        storage = self.private / "browser-state.json"
        self.assertTrue(storage.is_file())
        self.assertFalse(storage.is_symlink())
        self.assertEqual(os.stat(storage).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.private).st_mode & 0o777, 0o700)
        saved = storage.read_text(encoding="utf-8")
        self.assertNotIn(self.password, saved)
        self.assertNotIn(self.username, saved)
        self.assertEqual(list(self.repo.rglob("browser-state.json")), [])
        self.assertEqual(list(self.repo.rglob("*.webm")), [])

        harness.navigations.clear()
        login_calls.clear()
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=second, repo_root=self.repo,
            ) as page:
                self.assertIn("lightning.force.com", page.url)
        self.assertEqual(login_calls, [])
        self.assertEqual(harness.navigations, [
            "https://example.lightning.force.com/lightning/page/home",
        ])
        self.assertNotIn("login.salesforce.com", " ".join(harness.navigations))
        self._assert_headed_with_video(harness, second)
        self.assertEqual(harness.contexts[-1]["storage_state"], str(storage.resolve()))

    def test_login_wall_on_the_org_host_signs_in_again(self):
        harness = _StateHarness()
        login_calls = []

        def fake_login(page, username, password):
            login_calls.append(username)
            page.goto("https://login.salesforce.com/")
            page.url = "https://example.lightning.force.com/lightning/page/home"
            page.login_wall = False

        first = self.private / "run-one"
        second = self.private / "run-two"
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=first, repo_root=self.repo,
            ):
                pass
        harness.navigations.clear()
        login_calls.clear()
        harness.login_wall = True
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=second, repo_root=self.repo,
            ) as page:
                self.assertIn("lightning.force.com", page.url)
        self.assertEqual(login_calls, [self.username])
        self.assertIn("https://login.salesforce.com/", harness.navigations)

    def test_storage_inside_the_repo_is_not_written(self):
        harness = _StateHarness()

        def fake_login(page, username, password):
            page.url = "https://example.lightning.force.com/lightning/page/home"

        inside = self.repo / "captured"
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=inside, repo_root=self.repo,
            ):
                pass
        self.assertEqual(list(self.repo.rglob("browser-state.json")), [])
        self.assertNotIn("storage_state", harness.contexts[0])
        self.assertNotIn("record_video_dir", harness.contexts[0])
        self.assertEqual(harness.launch_kwargs["headless"], False)

    def _assert_headed_with_video(self, harness, folder):
        self.assertEqual(harness.launch_kwargs["headless"], False)
        self.assertNotIn("channel", harness.launch_kwargs)
        context = harness.contexts[-1]
        self.assertEqual(Path(context["record_video_dir"]).resolve(), folder.resolve())
        self.assertEqual(context["viewport"], {"width": 1500, "height": 1000})
        self.assertEqual(context["record_video_size"], {"width": 1500, "height": 1000})
        self.assertIn("Chrome/", context["user_agent"])
        self.assertNotIn("Headless", context["user_agent"])

    def _patched(self, harness, fake_login):
        return _stored_patches(harness, fake_login)


class _stored_patches:
    def __init__(self, harness, fake_login):
        self.harness = harness
        self.fake_login = fake_login
        self._stack = None

    def __enter__(self):
        from contextlib import ExitStack
        self._stack = ExitStack()
        self._stack.__enter__()
        self._stack.enter_context(patch(
            "playwright.sync_api.sync_playwright", _state_playwright(self.harness),
        ))
        self._stack.enter_context(patch(
            "workshop_validator.private_runner.login_to_salesforce", self.fake_login,
        ))
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._stack.__exit__(exc_type, exc, tb)


class DefaultsTests(unittest.TestCase):
    def test_page_wait_default_is_90_seconds(self):
        """CLAUDE.md: poll ~2 s, up to ~90 s"""
        sig = inspect.signature(run_uploaded_guide)
        page_wait_s_default = sig.parameters['page_wait_s'].default
        self.assertEqual(page_wait_s_default, 90)


if __name__ == "__main__":
    unittest.main()
