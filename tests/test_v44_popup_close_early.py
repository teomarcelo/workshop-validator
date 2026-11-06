"""V44 / W-080: close an unrelated covering pop-up as soon as it blocks the step's control.

A tour dialog (aria-modal, a title, "Step 1 of 2", buttons Dismiss, Next and an X named
Close) covered a visible Save button. The walk tried to click Save until the wait ran out,
and only then reached the pop-up retry, with no time left to close anything. Now a covered
visible control hands the miss to the retry at once, while the step still has time.

Real browser only, synthetic HTML. The step's own popup (opened by Save, with OK) is the
guide's and is never closed by the W-080 closer.
"""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.automate import INCONCLUSIVE, gate_outcome_of, outcome_of
from workshop_validator.report import ReportStep
from tests.test_guide_walk import step

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
PAGE_URL = "https://example.lightning.force.com/lightning/setup/SampleSettings/home"
TOUR_TITLE = "Welcome to the Sample Tour"
PAGE_WAIT_S = 20
STEP_CAP_S = 30
# "Closed early" is an order of events, not a wall-time bound (a 12 s bound flaked under
# heavy load). The walk's control wait ends when _wait_done first returns True, which needs
# PAGE_WAIT_S (20 s) on the walk's own clock. The old code closes a pop-up only after that
# (or never, once the step cap is spent). The fix closes on the first poll that sees the
# covered control, so the close must come before any wait has ended.

TOUR_BUTTONS = (
    '<button onclick="tour(\'Dismiss\')">Dismiss</button>'
    '<button onclick="tour(\'Next\')">Next</button>'
    '<button aria-label="Close" onclick="tour(\'Close\')">X</button>'
)
NEXT_ONLY = '<button onclick="tour(\'Next\')">Next</button>'

PAGE_HTML = """<!doctype html><html><head><title>Sample Settings</title></head><body>
<main><h1>Sample Settings</h1><p>Made-up settings page.</p>
<button id="save" style="{save_style}" onclick="save()">Save</button></main>
<div id="tour" role="dialog" {tour_modal} aria-labelledby="tour-title" style="{tour_style}">
  <h2 id="tour-title">{tour_title}</h2>
  <p id="tour-step">Step 1 of 2</p>
  <p>Made-up help text for a tour the guide does not mention.</p>
  {tour_buttons}
</div>
<script>
  window.clicked = [];
  const TOUR_STYLE = "position:fixed;inset:0;background:rgba(255,255,255,0.96);z-index:50;display:block";
  function tour(name) {{
    window.clicked.push('tour:' + name);
    if (name === 'Next') {{ document.getElementById('tour-step').textContent = 'Step 2 of 2'; return; }}
    document.getElementById('tour').remove();
  }}
  function save() {{
    window.clicked.push('Save');
    const box = document.createElement('div');
    box.id = 'stored';
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-labelledby', 'stored-title');
    box.style.cssText = 'position:fixed;top:20%;left:20%;padding:20px;background:#fff;z-index:60';
    box.innerHTML = '<h2 id="stored-title">Changes Stored</h2><p>Made-up result text.</p>' +
      '<button aria-label="Close" onclick="stored(\\'Close\\')">X</button>';
    document.body.appendChild(box);
    setTimeout(() => {{
      const ok = document.createElement('button');
      ok.textContent = 'OK';
      ok.onclick = () => stored('OK');
      ok.disabled = {ok_disabled_ms} > 0;
      box.appendChild(ok);
      setTimeout(() => {{ ok.disabled = false; }}, {ok_disabled_ms});
    }}, {ok_delay_ms});
  }}
  function stored(name) {{
    window.clicked.push('stored:' + name);
    document.getElementById('stored').remove();
    if (name === 'OK') {{
      const toast = document.createElement('div');
      toast.setAttribute('role', 'status');
      toast.textContent = 'Sample settings successfully saved.';
      document.body.appendChild(toast);
    }}
  }}
  {setup}
</script></body></html>"""

SHOWN = "position:fixed;inset:0;background:rgba(255,255,255,0.96);z-index:50;display:block"


def save_step():
    return step("save", "Exercise 1: Sample", "Task 1: Sample.", "5",
                "Click Save, then read the popup and click OK")


FORM_HTML = """<!doctype html><html><head><title>Sample Records</title></head><body>
<main><h1>Sample Records</h1><p>Made-up record list.</p>
<button onclick="window.clicked.push('page:Save')">Save</button></main>
<div id="form" role="dialog" aria-modal="true" aria-labelledby="form-title"
  style="position:fixed;inset:0;background:rgba(255,255,255,0.96);z-index:50">
  <h2 id="form-title">New Sample Record</h2>
  <label>Sample Name <input id="sample-name" value="Made-up name"></label>
  <button aria-label="Close" onclick="window.clicked.push('form:Close'); this.parentNode.remove()">X</button>
  <button onclick="formSave()">Save</button>
</div>
<script>
  window.clicked = [];
  function formSave() {
    window.clicked.push('form:Save');
    window.saved = document.getElementById('sample-name').value;
    document.getElementById('form').remove();
    const toast = document.createElement('div');
    toast.setAttribute('role', 'status');
    toast.textContent = 'Sample record was created.';
    document.body.appendChild(toast);
  }
</script></body></html>"""

# Every pointer event Save receives, in order with the tour clicks (window.clicked).
SAVE_EVENTS = ("const s = document.getElementById('save'); window.saveClicks = 0;"
               " ['mousedown', 'mouseup', 'click'].forEach((t) => s.addEventListener(t, () => {"
               " window.clicked.push('event:' + t); if (t === 'click') window.saveClicks += 1; }));")

# An aria-modal tip in a corner that never covers Save.
CORNER_TIP = ("position:fixed;top:auto;left:auto;right:0;bottom:0;width:240px;height:120px;"
              "background:#fff;z-index:50;display:block")


def plain_step(text):
    return step("save", "Exercise 1: Sample", "Task 1: Sample.", "5", text)


def page_html(tour_buttons=TOUR_BUTTONS, tour_style=SHOWN, save_style="",
              ok_delay_ms=0, ok_disabled_ms=0, setup="", tour_modal='aria-modal="true"'):
    return PAGE_HTML.format(
        tour_title=TOUR_TITLE, tour_buttons=tour_buttons, tour_style=tour_style,
        save_style=save_style, ok_delay_ms=ok_delay_ms,
        ok_disabled_ms=ok_disabled_ms, setup=setup, tour_modal=tour_modal,
    )


@unittest.skipUnless(SMOKE, "Browser smoke only (RUN_BROWSER_SMOKE=1 and Chromium)")
class PopupCloseEarlyBrowserTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(
            headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None,
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def open(self, html):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page.goto(PAGE_URL)
        page.wait_for_timeout(100)
        return page

    def walk(self, page, step_text=None):
        state = guide_walk.WalkState(
            page=page, timeout_s=2, interval_s=0.2, page_wait_s=PAGE_WAIT_S, page_poll_s=0.5,
        )
        state.step_deadline = state.clock() + STEP_CAP_S
        # Order of events: "close" for each W-080 close attempt, "wait-ended" each time a
        # control wait reports it is done.
        self.events = []
        real_done, real_close = guide_walk._wait_done, guide_walk._close_unrelated_popup

        def wait_done(*args):
            done = real_done(*args)
            if done:
                self.events.append("wait-ended")
            return done

        def close(*args):
            self.events.append("close")
            return real_close(*args)

        with mock.patch.object(guide_walk, "_wait_done", wait_done), \
                mock.patch.object(guide_walk, "_close_unrelated_popup", close):
            result = guide_walk.attempt_step(state, plain_step(step_text) if step_text else save_step())
        return state, result, page.evaluate("window.clicked")

    def assert_closed_early(self):
        self.assertIn("close", self.events)
        self.assertNotIn("wait-ended", self.events[:self.events.index("close")], self.events)

    def assert_closed_once(self, state, clicked):
        tour_clicks = [name for name in clicked if name.startswith("tour:")]
        self.assertEqual(len(tour_clicks), 1, clicked)
        self.assertIn(tour_clicks[0], ("tour:Dismiss", "tour:Close"), clicked)
        self.assertNotIn("tour:Next", clicked)
        self.assertEqual(state.popup_notes, [guide_walk._popup_note(TOUR_TITLE)])
        self.assertIn("W-080", state.popup_notes[0])

    def test_tour_present_before_step_is_closed_early_then_save_and_ok(self):
        page = self.open(page_html())
        state, result, clicked = self.walk(page)
        self.assert_closed_early()
        self.assert_closed_once(state, clicked)
        self.assertEqual(clicked[1:], ["Save", "stored:OK"], clicked)
        self.assertEqual(result.status, "PASS", result.note)
        # Closing is never evidence: the PASS rests on the clicks, not on the close.
        self.assertNotIn("W-080", result.note)

    def test_tour_that_appears_two_seconds_in_is_closed_early(self):
        setup = ("setTimeout(() => { document.getElementById('save').style.display = '';"
                 " document.getElementById('tour').style.cssText = TOUR_STYLE; }, 2000);")
        page = self.open(page_html(tour_style="display:none", save_style="display:none", setup=setup))
        state, result, clicked = self.walk(page)
        self.assert_closed_early()
        self.assert_closed_once(state, clicked)
        self.assertEqual(clicked[1:], ["Save", "stored:OK"], clicked)
        self.assertEqual(result.status, "PASS", result.note)

    def test_guide_popup_opened_by_save_is_never_closed_by_w080(self):
        # The step's own popup has an X named Close. Its OK shows a second later and stays
        # disabled three more seconds: neither a missing nor an unclickable OK closes the popup.
        page = self.open(page_html(ok_delay_ms=1000, ok_disabled_ms=3000))
        state, result, clicked = self.walk(page)
        self.assert_closed_early()
        self.assert_closed_once(state, clicked)
        self.assertNotIn("stored:Close", clicked)
        self.assertEqual(clicked[1:], ["Save", "stored:OK"], clicked)
        self.assertEqual(result.status, "PASS", result.note)

    def test_tour_with_only_next_is_never_clicked_and_stops_with_a_note(self):
        page = self.open(page_html(tour_buttons=NEXT_ONLY))
        state, result, clicked = self.walk(page)
        self.assertEqual(clicked, [])
        self.assertEqual(state.popup_notes, [])
        self.assertEqual(result.status, "ENV_BLOCKED", result.note)
        self.assertTrue(result.freeze)
        self.assertIn(f'"{TOUR_TITLE}" modal covered the page', result.note)
        self.assertIn("Escape pressed yes", result.note)
        self.assertIn("still visible yes", result.note)
        # It stops on the first poll, not after a full control wait.
        self.assertNotIn("wait-ended", self.events, self.events)

    def test_form_with_its_own_save_is_never_closed_and_no_save_is_clicked(self):
        # Code review finding 1: the step's own form dialog has a Save, and a page Save sits
        # behind it. The form is never closed, the input is kept, and the covered page Save is
        # never clicked. Which Save the guide means cannot be read, so the step is NEEDS_REVIEW.
        page = self.open(FORM_HTML)
        state, result, clicked = self.walk(page, "Click Save")
        self.assertEqual(clicked, [], (clicked, result.note))
        self.assertTrue(page.evaluate("document.getElementById('form') !== null"))
        self.assertEqual(page.evaluate("document.getElementById('sample-name').value"), "Made-up name")
        self.assertEqual(state.popup_notes, [])
        self.assertNotIn("close", self.events)
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertTrue(result.freeze)
        self.assertIn('"New Sample Record" dialog, which has its own Save', result.note)
        # It stops on the first poll, and the run is inconclusive, never a pass or a failure.
        self.assertNotIn("wait-ended", self.events, self.events)
        run = SimpleNamespace(signed_in=True, message="", report_steps=(
            ReportStep("s0", 1, "Step 0", "PASS"),
            ReportStep("s1", 1, "Step 1", result.status, note=result.note),
        ))
        self.assertEqual(outcome_of(run)[0], INCONCLUSIVE)
        self.assertEqual(gate_outcome_of(run), INCONCLUSIVE)

    def test_form_with_its_own_save_and_nothing_behind_gets_its_save_clicked(self):
        page = self.open(FORM_HTML.replace(
            "<button onclick=\"window.clicked.push('page:Save')\">Save</button>", ""))
        state, result, clicked = self.walk(page, "Click Save")
        self.assertEqual(clicked, ["form:Save"], (clicked, result.note))
        self.assertEqual(page.evaluate("window.saved"), "Made-up name")
        self.assertEqual(state.popup_notes, [])

    def test_trial_click_while_covered_sends_save_no_events(self):
        # Code review finding 2: the cover check's trial click must not reach Save. Save gets
        # exactly one mousedown, mouseup and click, all after the tour was closed.
        page = self.open(page_html(setup=SAVE_EVENTS))
        state, result, clicked = self.walk(page)
        self.assert_closed_early()
        self.assert_closed_once(state, clicked)
        # clicked[0] is the tour close, so every Save event comes after it.
        self.assertEqual([c for c in clicked[1:] if c.startswith("event:")],
                         ["event:mousedown", "event:mouseup", "event:click"], clicked)
        self.assertEqual(page.evaluate("window.saveClicks"), 1)
        self.assertEqual(result.status, "PASS", result.note)

    def test_modal_that_does_not_cover_save_is_left_open_and_save_is_clicked_once(self):
        # Rule audit: the trial click is the guard. With an aria-modal tip in a corner and
        # Save still clickable, nothing is closed, nothing is noted, and Save is clicked once.
        page = self.open(page_html(tour_style=CORNER_TIP, setup=SAVE_EVENTS))
        state, result, clicked = self.walk(page, "Click Save")
        self.assertEqual([c for c in clicked if c.startswith("event:")],
                         ["event:mousedown", "event:mouseup", "event:click"], (clicked, result.note))
        self.assertEqual(page.evaluate("window.saveClicks"), 1)
        self.assertFalse([c for c in clicked if c.startswith("tour:")], clicked)
        self.assertEqual(state.popup_notes, [])
        self.assertNotIn("close", self.events)

    def state_on(self, html):
        page = self.open(html)
        return guide_walk.WalkState(page=page, timeout_s=2, interval_s=0.2, page_wait_s=2, page_poll_s=0.5)

    def test_cover_check_names_the_modal_over_a_visible_control(self):
        state = self.state_on(page_html())
        found = guide_walk._popup_over_control(state, save_step(), guide_walk._Intent("click", "Save"))
        self.assertEqual(found, guide_walk._modal_result(TOUR_TITLE))
        self.assertEqual(state.page.evaluate("window.clicked"), [])

    def test_cover_check_is_only_for_clicks(self):
        state = self.state_on(page_html())
        for kind in ("fill", "toggle", "observe"):
            with self.subTest(kind=kind):
                self.assertIsNone(guide_walk._popup_over_control(
                    state, save_step(), guide_walk._Intent(kind, "Save")))

    def test_cover_check_skips_a_dialog_that_is_not_modal(self):
        state = self.state_on(page_html(tour_modal=""))
        self.assertIsNone(guide_walk._popup_over_control(
            state, save_step(), guide_walk._Intent("click", "Save")))
        self.assertEqual(state.page.evaluate("window.clicked"), [])


if __name__ == "__main__":
    unittest.main()
