"""V44 / W-080: close an unrelated covering pop-up as soon as it blocks the step's control.

A tour dialog (aria-modal, a title, "Step 1 of 2", buttons Dismiss, Next and an X named
Close) covered a visible Save button. The walk tried to click Save until the wait ran out,
and only then reached the pop-up retry, with no time left to close anything. Now a covered
visible control hands the miss to the retry at once, while the step still has time.

Real browser only, synthetic HTML. The step's own popup (opened by Save, with OK) is the
guide's and is never closed by the W-080 closer.
"""

import os
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from tests.test_guide_walk import step

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
PAGE_URL = "https://example.lightning.force.com/lightning/setup/SampleSettings/home"
TOUR_TITLE = "Welcome to the Sample Tour"
PAGE_WAIT_S = 20
STEP_CAP_S = 30
# Well inside the wait: the old code spends the whole wait on the covered Save.
EARLY_S = 12

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

    def walk(self, page):
        state = guide_walk.WalkState(
            page=page, timeout_s=2, interval_s=0.2, page_wait_s=PAGE_WAIT_S, page_poll_s=0.5,
        )
        start = time.monotonic()
        state.step_deadline = start + STEP_CAP_S
        result = guide_walk.attempt_step(state, save_step())
        return state, result, time.monotonic() - start, page.evaluate("window.clicked")

    def assert_closed_once(self, state, clicked):
        tour_clicks = [name for name in clicked if name.startswith("tour:")]
        self.assertEqual(len(tour_clicks), 1, clicked)
        self.assertIn(tour_clicks[0], ("tour:Dismiss", "tour:Close"), clicked)
        self.assertNotIn("tour:Next", clicked)
        self.assertEqual(state.popup_notes, [guide_walk._popup_note(TOUR_TITLE)])
        self.assertIn("W-080", state.popup_notes[0])

    def test_tour_present_before_step_is_closed_early_then_save_and_ok(self):
        page = self.open(page_html())
        state, result, elapsed, clicked = self.walk(page)
        self.assertLess(elapsed, EARLY_S, (elapsed, result.note))
        self.assert_closed_once(state, clicked)
        self.assertEqual(clicked[1:], ["Save", "stored:OK"], clicked)
        self.assertEqual(result.status, "PASS", result.note)
        # Closing is never evidence: the PASS rests on the clicks, not on the close.
        self.assertNotIn("W-080", result.note)

    def test_tour_that_appears_two_seconds_in_is_closed_early(self):
        setup = ("setTimeout(() => { document.getElementById('save').style.display = '';"
                 " document.getElementById('tour').style.cssText = TOUR_STYLE; }, 2000);")
        page = self.open(page_html(tour_style="display:none", save_style="display:none", setup=setup))
        state, result, elapsed, clicked = self.walk(page)
        self.assertLess(elapsed, EARLY_S, (elapsed, result.note))
        self.assert_closed_once(state, clicked)
        self.assertEqual(clicked[1:], ["Save", "stored:OK"], clicked)
        self.assertEqual(result.status, "PASS", result.note)

    def test_guide_popup_opened_by_save_is_never_closed_by_w080(self):
        # The step's own popup has an X named Close. Its OK shows a second later and stays
        # disabled three more seconds: neither a missing nor an unclickable OK closes the popup.
        page = self.open(page_html(ok_delay_ms=1000, ok_disabled_ms=3000))
        state, result, elapsed, clicked = self.walk(page)
        self.assertLess(elapsed, EARLY_S, (elapsed, result.note))
        self.assert_closed_once(state, clicked)
        self.assertNotIn("stored:Close", clicked)
        self.assertEqual(clicked[1:], ["Save", "stored:OK"], clicked)
        self.assertEqual(result.status, "PASS", result.note)

    def test_tour_with_only_next_is_never_clicked_and_stops_with_a_note(self):
        page = self.open(page_html(tour_buttons=NEXT_ONLY))
        state, result, elapsed, clicked = self.walk(page)
        self.assertEqual(clicked, [])
        self.assertEqual(state.popup_notes, [])
        self.assertEqual(result.status, "ENV_BLOCKED", result.note)
        self.assertTrue(result.freeze)
        self.assertIn(f'"{TOUR_TITLE}" modal covered the page', result.note)
        self.assertIn("Escape pressed yes", result.note)
        self.assertIn("still visible yes", result.note)
        self.assertLess(elapsed, EARLY_S, (elapsed, result.note))

    def state_on(self, html):
        page = self.open(html)
        return guide_walk.WalkState(page=page, timeout_s=2, interval_s=0.2, page_wait_s=2, page_poll_s=0.5)

    def test_cover_check_names_the_modal_over_a_visible_control(self):
        state = self.state_on(page_html())
        found = guide_walk._popup_over_control(state, save_step(), guide_walk._Intent("click", "Save"))
        self.assertEqual(found, TOUR_TITLE)
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
