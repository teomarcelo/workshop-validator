"""W-142/W-144/W-145: recover from the guide's own dialog left open after a blocked task.

At the first step of a task that starts right after a blocked task, a dialog a guide step
opened can still cover the page. The walk presses Escape once (only with focus inside the
dialog), then navigates for real to the org home recorded at walk start. It never clicks
inside the dialog. Unrelated pop-ups keep W-080.

Real browser, synthetic HTML, every URL served by a local route (no network).
Each test runs through walk_guide.
"""

import os
import unittest
from unittest.mock import patch

try:
    from playwright.sync_api import Error as PlaywrightError, Keyboard, Locator, sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, _landing_stop, _open_guide_dialog, walk_guide

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
EX_A = "Exercise 1: Sample Alpha"
HOME = "https://example.lightning.force.com/lightning/page/home"
LOGIN = "https://login.salesforce.com/?wv=synthetic"
OFF_ORG = "https://example.com/wv-synthetic-off-org"
TITLE = "New Sample Screen"
RECOVERED = f'Environment: the guide\'s own dialog "{TITLE}" was still open after the blocked task a2; the walk '
TODAY_STOP = (f'"{TITLE}" still covered the page after the failure in a2; '
              "the walk could not close it, so the following steps were not run.")


def _step(step_id, task, number):
    return GuideStep(step_id, 1, EX_A, task, number, f"Do sample action {step_id}.",
                     (90.0, 10.0, 400.0, 24.0), None, ())


TWO_TASKS = (
    _step("a1", "Task 1: Sample one", "1"),
    _step("a2", "Task 1: Sample one", "2"),
    _step("a3", "Task 1: Sample one", "3"),
    _step("b1", "Task 2: Sample two", "1"),
    _step("b2", "Task 2: Sample two", "2"),
)

_SCRIPT = """<script>
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') wvEvent('escape:' + (document.activeElement && document.activeElement.id || 'none'));
  }, true);
  const field = document.getElementById('field');
  field.addEventListener('keydown', () => wvEvent('field-keydown'));
  field.addEventListener('input', () => wvEvent('field-input'));
  const box = document.getElementById('box');
  if (box) {
    box.addEventListener('click', () => wvEvent('dialog-click'), true);
    const cancel = document.getElementById('cancel');
    if (cancel) cancel.addEventListener('click', () => box.remove());
    if (box.dataset.escape === 'closes') {
      document.addEventListener('keydown', (e) => { if (e.key === 'Escape') box.remove(); });
    }
  }
</script>"""


def page_html(dialog_title=None, *, escape_closes=False, focusable=True, modal=True):
    """A synthetic org page with a guide field and, optionally, one dialog over it."""
    dialog = ""
    if dialog_title:
        buttons = '<button id="cancel">Cancel</button><button id="save">Save</button>' if focusable else ""
        dialog = (f'<div id="box" role="dialog" aria-modal="{"true" if modal else "false"}" '
                  f'aria-labelledby="box-title" data-escape="{"closes" if escape_closes else "ignores"}" '
                  'style="position:fixed;inset:0;background:#fff;z-index:50">'
                  f'<h2 id="box-title">{dialog_title}</h2><p>Made-up sample dialog body.</p>{buttons}</div>')
    return ("<!doctype html><html><head><title>Sample Home</title></head><body>"
            '<main><h1>Sample Home</h1><label for="field">Sample Field</label>'
            f'<input id="field" value="kept value"></main>{dialog}{_SCRIPT}</body></html>')


LOGIN_HTML = ("<!doctype html><html><head><title>Sample Login</title></head><body>"
              '<form><label for="username">Username</label><input id="username"></form></body></html>')


def _ok(state, step):
    return CheckResult("PASS", "done", (f"{step.step_id}:sample-done",))


def _opens_guide_dialog(state, step):
    _open_guide_dialog(state, TITLE)
    return _ok(state, step)


def _frozen(status):
    return lambda state, step: CheckResult(status, "Sample control was not found.", (), freeze=True)


class LandingStopTests(unittest.TestCase):
    """The landing after the recovery's goto is read from the URL alone, never by navigating again."""

    def test_blank_or_empty_url_is_page_lost(self):
        self.assertEqual(_landing_stop("about:blank"), "page-lost")
        self.assertEqual(_landing_stop(""), "page-lost")

    def test_login_host_is_login(self):
        self.assertEqual(_landing_stop(LOGIN), "login")

    def test_org_and_other_hosts_are_not_a_stop(self):
        self.assertIsNone(_landing_stop(HOME))
        self.assertIsNone(_landing_stop(OFF_ORG))


@unittest.skipUnless(SMOKE, "RUN_BROWSER_SMOKE=1 not set")
class RecoverAfterBlockedTaskBrowserTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._pw = sync_playwright().start()
        cls.browser = cls._pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls._pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.events = []
        self.navs = []
        self.served = {"home": "", "home_mode": "html", "counting": False}
        self.home_hits = []
        self.page.expose_function("wvEvent", lambda event: self.events.append(event))
        self.page.route("https://login.salesforce.com/**", self._login_route)
        self.page.route("https://example.lightning.force.com/**", self._home_route)
        self.page.route("https://example.com/**", self._off_org_route)

    def _off_org_route(self, route):
        route.fulfill(status=200, body=self.served["home"], content_type="text/html")

    def _login_route(self, route):
        route.fulfill(status=200, body=LOGIN_HTML, content_type="text/html")

    def _home_route(self, route):
        mode = self.served["home_mode"]
        if self.served["counting"]:
            self.home_hits.append(route.request.url)
        if mode == "blank":  # the home page goes blank while it loads
            route.fulfill(status=200, content_type="text/html",
                          body='<html><head><script>location.replace("about:blank")</script></head></html>')
        elif mode == "offorg":
            route.fulfill(status=302, headers={"Location": OFF_ORG})
        elif mode == "abort":
            route.abort()
        elif mode == "login":
            route.fulfill(status=302, headers={"Location": LOGIN})
        else:
            route.fulfill(status=200, body=self.served["home"], content_type="text/html")

    def _start(self, start_html, home_html=None, home_mode="html", focus_field=False, url=HOME):
        """Open URL with START_HTML; later visits to the home get HOME_HTML or HOME_MODE."""
        self.served["home"] = start_html
        self.page.goto(url)
        if focus_field:
            self.page.focus("#field")
        self.served["home"] = home_html if home_html is not None else start_html
        self.served["home_mode"] = home_mode
        self.served["counting"] = True
        self.page.on("framenavigated",
                     lambda frame: self.navs.append(frame.url) if frame == self.page.main_frame else None)

    def _walk(self, results, items=TWO_TASKS):
        calls = []

        def run(state, step):
            calls.append(step.step_id)
            return results.get(step.step_id, _ok)(state, step)

        outcome = walk_guide(
            self.page, GuideInventory(1, tuple(items), (), ()),
            checks=(GuideCheck("sample", lambda item: True, run),),
            timeout_s=0, interval_s=1, page_wait_s=5,
        )
        self.page.wait_for_timeout(300)  # let page bindings reach Python
        return outcome, calls

    def _escapes(self):
        return [event for event in self.events if event.startswith("escape:")]

    def test_dialog_closes_on_escape_task_runs(self):
        self._start(page_html(TITLE, escape_closes=True))
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertEqual(outcome.notes["b1"], RECOVERED + "pressed Escape, then started this task "
                         "(owner rule W-144). done")
        self.assertEqual(self.events.count("dialog-click"), 0, self.events)
        self.assertEqual(len(self._escapes()), 1, self.events)
        self.assertEqual(self.navs, [])

    def test_ui_mismatch_block_then_guide_dialog_recovers(self):
        """W-145: a frozen real failure (W-117) blocks a task the same way as NEEDS_REVIEW."""
        self._start(page_html(TITLE, escape_closes=True))
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("UI_MISMATCH")})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertIn(RECOVERED + "pressed Escape, then started", outcome.notes["b1"])
        self.assertEqual(self.navs, [])

    def test_dialog_ignores_escape_real_navigation_home(self):
        self._start(page_html(TITLE), home_html=page_html())
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("UI_MISMATCH")})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertEqual(outcome.notes["b1"], RECOVERED + "pressed Escape and returned to the org home, "
                         "then started this task (owner rule W-144). done")
        self.assertEqual(self.navs, [HOME])
        self.assertEqual(len(self._escapes()), 1, self.events)
        self.assertEqual(self.events.count("dialog-click"), 0, self.events)

    def test_dialog_survives_escape_and_navigation_stops_with_today_note(self):
        self._start(page_html(TITLE))  # the home also serves the dialog
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("FUNCTIONAL_FAIL")})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual(outcome.statuses["b1"], "NOT_RUN")
        self.assertEqual(outcome.notes["b1"], TODAY_STOP)
        self.assertEqual(outcome.notes["b2"], TODAY_STOP)
        self.assertEqual(self.navs, [HOME])
        self.assertEqual(self.events.count("dialog-click"), 0, self.events)

    def test_home_redirects_to_login_full_session_sentence(self):
        self._start(page_html(TITLE), home_mode="login")
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2"])
        sentence = "The org session ended (sent to the login page), so the following steps were not run."
        self.assertEqual(outcome.notes["b1"], sentence)
        self.assertEqual(outcome.notes["b2"], sentence)
        self.assertEqual(self.home_hits, [HOME])  # exactly one goto: the landing is read, not re-navigated
        self.assertEqual(self.navs, [LOGIN])

    def test_page_goes_blank_during_navigation_full_page_lost_sentence(self):
        self._start(page_html(TITLE), home_mode="blank")
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2"])
        sentence = "The page left the org (blank or closed page), so the following steps were not run."
        self.assertEqual(outcome.notes["b1"], sentence)
        self.assertEqual(outcome.notes["b2"], sentence)
        self.assertEqual(self.home_hits, [HOME])

    def test_home_redirects_off_org_no_recovery_claimed(self):
        self._start(page_html(TITLE), home_html=page_html(), home_mode="offorg")
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual(outcome.notes["b1"], "The return to the org home after the failure in a2 did not "
                         "reach the org, so the following steps were not run.")
        self.assertNotIn("returned to the org home", outcome.notes["b1"])
        self.assertEqual(self.navs, [OFF_ORG])

    def test_walk_started_off_org_no_navigation_today_note(self):
        self._start(page_html(TITLE), url=OFF_ORG)
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual(outcome.notes["b1"], TODAY_STOP)
        self.assertEqual(self.navs, [])
        self.assertEqual(self.home_hits, [])

    def test_later_step_needing_blocked_state_fails_on_its_own(self):
        """After a recovery, a step that needed the blocked task's state fails with its own reason."""
        self._start(page_html(TITLE, escape_closes=True))
        outcome, calls = self._walk({
            "a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW"),
            "b1": lambda state, step: CheckResult("UI_MISMATCH", "Sample screen from a2 was not found.", (),
                                                  freeze=True),
        })
        self.assertEqual(calls, ["a1", "a2", "b1"])
        self.assertEqual(outcome.statuses["b1"], "UI_MISMATCH")
        self.assertEqual(outcome.notes["b1"], RECOVERED + "pressed Escape, then started this task "
                         "(owner rule W-144). Sample screen from a2 was not found.")
        self.assertEqual(outcome.statuses["b2"], "NOT_RUN")

    def test_new_unrelated_popup_on_home_no_close_claimed(self):
        self._start(page_html(TITLE), home_html=page_html("Sample Notice", focusable=False))
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual(outcome.notes["b1"], '"Sample Notice" covered the page after the walk returned to the '
                         "org home after the failure in a2; no close was tried, so the following steps were not run.")
        self.assertNotIn("could not close it", outcome.notes["b1"])
        self.assertEqual(self.navs, [HOME])

    def test_focus_in_guide_field_outside_dialog_no_escape_into_field(self):
        """The dialog cannot take focus: Escape is skipped, the navigation still runs."""
        self._start(page_html(TITLE, focusable=False), home_html=page_html(), focus_field=True)
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        self.assertEqual(self.events.count("field-keydown"), 0, self.events)
        self.assertEqual(self.events.count("field-input"), 0, self.events)
        self.assertEqual(self._escapes(), [])
        self.assertEqual(outcome.notes["b1"], RECOVERED + "returned to the org home, then started this task "
                         "(owner rule W-144). done")
        self.assertEqual(self.navs, [HOME])

    def test_unrelated_popup_keeps_today_behaviour(self):
        """A pop-up no guide step opened, that W-080 cannot close: no recovery, today's note."""
        self._start(page_html("Sample Notice", focusable=False), home_html=page_html())
        outcome, calls = self._walk({"a2": _frozen("UI_MISMATCH")})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual(outcome.notes["b1"], '"Sample Notice" still covered the page after the failure in a2; '
                         "the walk could not close it, so the following steps were not run.")
        self.assertNotIn("blocked task", outcome.notes["b1"])
        self.assertEqual(self.navs, [])

    def test_no_blocked_task_no_recovery_at_next_task(self):
        """Only after a blocked task: a guide dialog open at a normal task start is left alone."""
        self._start(page_html(TITLE, escape_closes=True), home_html=page_html())
        outcome, calls = self._walk({"a1": _opens_guide_dialog})
        self.assertEqual(calls, ["a1", "a2", "a3", "b1", "b2"])
        self.assertEqual(outcome.notes["b1"], "done")
        self.assertEqual(self._escapes(), [])
        self.assertEqual(self.navs, [])

    def test_escape_raises_no_escape_note_navigation_runs(self):
        self._start(page_html(TITLE), home_html=page_html())
        with patch.object(Keyboard, "press", side_effect=PlaywrightError("synthetic keyboard failure")):
            outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        self.assertEqual(outcome.notes["b1"], RECOVERED + "returned to the org home, then started this task "
                         "(owner rule W-144). done")
        self.assertEqual(self.navs, [HOME])

    def test_dialog_handle_unreadable_no_escape_navigation_runs(self):
        real = Locator.element_handle

        def element_handle(loc, *args, **kwargs):
            if kwargs.get("timeout") == 2000:  # the recovery's own read of the dialog
                raise PlaywrightError("synthetic handle failure")
            return real(loc, *args, **kwargs)

        self._start(page_html(TITLE, escape_closes=True), home_html=page_html())
        with patch.object(Locator, "element_handle", element_handle):
            outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        self.assertEqual(self._escapes(), [])
        self.assertEqual(outcome.notes["b1"], RECOVERED + "returned to the org home, then started this task "
                         "(owner rule W-144). done")
        self.assertEqual(self.navs, [HOME])

    def test_navigation_fails_true_stop_note(self):
        self._start(page_html(TITLE), home_mode="abort")
        outcome, calls = self._walk({"a1": _opens_guide_dialog, "a2": _frozen("NEEDS_REVIEW")})
        self.assertEqual(calls, ["a1", "a2"])
        self.assertEqual(outcome.notes["b1"], "The walk could not finish the return to the org home after the "
                         "failure in a2 (Error), so the following steps were not run.")
        self.assertNotIn("returned to the org home", outcome.notes["b1"])


if __name__ == "__main__":
    unittest.main()
