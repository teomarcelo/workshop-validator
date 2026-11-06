"""V44 / W-080: close an unrelated covering pop-up as soon as it blocks a control.

P004N001 run 20261106T114653Z-org7: a "Meet the new Guidance Center" dialog (Step 1 of 2)
covered the Save button. The walk waited about 30 s for Save while the dialog covered it,
until the step cap ran out. Then _popup_retry was called but its first check
(if _time_left(state) <= 0) returned before _close_unrelated_popup ever ran.

Fix: Close the popup early (while waiting for the control), not after the timeout expires.
And _popup_retry makes at least one close attempt even when time is up, if a covering
pop-up was the cause.
"""

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from tests.test_guide_walk import Clock, Page, step


def state_for(page):
    clock = Clock()
    return guide_walk.WalkState(
        page=page, timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        page_wait_s=10, page_poll_s=0.5,
    )


def save_step():
    return step("save", "Exercise 1: Sample", "Task 1: Sample.", "5",
                "Click Save, then read the popup and click OK")


def guidance_page(has_save=False, dialog=True, buttons=("Dismiss", "Next", "Close")):
    """A page with an optional Save button and an optional Guidance Center dialog."""
    page = Page("https://example.lightning.force.com/setup/Sample/home")
    if has_save:
        page.roles.add(("button", "Save"))
    if dialog:
        dialog_text = "Meet the new Guidance Center\nStep 1 of 2"
        page.roles.add(("dialog", dialog_text))
        inside = []
        for name in buttons:
            page.roles.add(("button", name))
            inside.append(("button", name))
        page.children[("dialog", dialog_text)] = inside
    return page


def clicks(page):
    return [event[1:] for event in page.events if event[0] == "click"]


GUIDANCE_DIALOG = "Meet the new Guidance Center\nStep 1 of 2"
CLOSED_NOTE = "Environment: closed a promotional pop-up the guide does not mention (owner rule W-080)."


class PopupCloseEarlyTests(unittest.TestCase):
    def test_popup_covering_save_at_start_is_closed_early_and_save_is_clicked(self):
        """The dialog is present before the step starts, blocking Save. It is closed
        right away (not after the full timeout) and Save is clicked."""
        page = guidance_page(has_save=False, dialog=True)

        def reveal(kind, name):
            if name in ("Close", "Dismiss"):
                page.roles.discard(("dialog", GUIDANCE_DIALOG))
            if name != "Next" and ("dialog", GUIDANCE_DIALOG) not in page.roles:
                page.roles.add(("button", "Save"))

        page.on_click = reveal
        state = state_for(page)
        state.step_deadline = state.clock() + 40  # 40 s cap
        result = guide_walk.attempt_step(state, save_step())
        # The close happens early: elapsed time should be much less than the cap.
        self.assertLess(state.clock(), 10, "popup should be closed early, not after full timeout")
        # A popup close note was added (the exact title may vary based on extraction)
        self.assertEqual(len(state.popup_notes), 1)
        self.assertIn("closed", state.popup_notes[0])
        self.assertIn("W-080", state.popup_notes[0])
        # Save should have been clicked
        self.assertIn(("button", "Save"), clicks(page))
        # Close or Dismiss was used, not Next
        close_clicks = [c for c in clicks(page) if c[1] in ("Close", "Dismiss")]
        self.assertEqual(len(close_clicks), 1)
        self.assertNotIn(("button", "Next"), clicks(page))

    def test_popup_appearing_during_wait_is_closed_and_save_is_clicked(self):
        """The dialog appears 2 s after the step starts, covering Save. It is closed
        and Save is clicked."""
        page = guidance_page(has_save=False, dialog=False)
        state = state_for(page)
        state.step_deadline = state.clock() + 40
        dialog_added = False

        def sleep_and_add(seconds):
            nonlocal dialog_added
            state.clock.sleep(seconds)
            if not dialog_added and state.clock() >= 2:
                dialog_added = True
                page.roles.add(("dialog", GUIDANCE_DIALOG))
                for name in ("Dismiss", "Next", "Close"):
                    page.roles.add(("button", name))
                page.children[("dialog", GUIDANCE_DIALOG)] = [
                    ("button", "Dismiss"), ("button", "Next"), ("button", "Close")
                ]

        def reveal(kind, name):
            if name in ("Close", "Dismiss"):
                page.roles.discard(("dialog", GUIDANCE_DIALOG))
            if name != "Next" and ("dialog", GUIDANCE_DIALOG) not in page.roles:
                page.roles.add(("button", "Save"))

        page.on_click = reveal
        state.sleeper = sleep_and_add
        result = guide_walk.attempt_step(state, save_step())
        self.assertLess(state.clock(), 15, "popup should be closed promptly after appearing")
        self.assertEqual(len(state.popup_notes), 1)
        self.assertIn("closed", state.popup_notes[0])
        self.assertIn("W-080", state.popup_notes[0])
        self.assertIn(("button", "Save"), clicks(page))

    def test_popup_found_after_deadline_still_gets_one_close_attempt(self):
        """The step deadline already passed when the miss is found. One close attempt
        still happens (_popup_retry always tries at least once)."""
        page = guidance_page(has_save=False, dialog=True)

        def reveal(kind, name):
            if name in ("Close", "Dismiss"):
                page.roles.discard(("dialog", GUIDANCE_DIALOG))
                page.roles.add(("button", "Save"))

        page.on_click = reveal
        state = state_for(page)
        # Set deadline in the past
        state.step_deadline = -1
        result = guide_walk.attempt_step(state, save_step())
        # At least one close attempt was made, even though time was up
        close_clicks = [c for c in clicks(page) if c[1] in ("Close", "Dismiss")]
        self.assertEqual(len(close_clicks), 1, "one close attempt should happen even when time is up")

    def test_guide_named_popup_is_never_closed_by_w080(self):
        """A dialog the guide names (the step's own popup with OK) is never closed
        by the W-080 closer. It is the guide's own dialog."""
        page = guidance_page(has_save=True, dialog=False)

        def reveal(kind, name):
            if name == "Save":
                # Save shows the popup with OK
                page.roles.add(("dialog", "Sample Popup\nOK"))
                page.roles.add(("button", "OK"))
                page.children[("dialog", "Sample Popup\nOK")] = [("button", "OK")]
            elif name == "OK":
                page.roles.discard(("dialog", "Sample Popup\nOK"))

        page.on_click = reveal
        state = state_for(page)
        result = guide_walk.attempt_step(state, save_step())
        # Both Save and OK should be clicked (Save first, then OK)
        self.assertIn(("button", "Save"), clicks(page))
        self.assertIn(("button", "OK"), clicks(page))
        # The guide's own dialog is never counted as a W-080 popup close
        # (it's opened by the walk, so it's recorded as a guide dialog)
        self.assertEqual(len([n for n in state.popup_notes if "W-080" in n]), 0)

    def test_popup_with_only_next_button_is_not_clicked_and_step_blocks(self):
        """A dialog with only a Next button and no dismiss control is not clicked.
        The step stops with ENV_BLOCKED and a clear note."""
        page = guidance_page(has_save=False, dialog=True, buttons=("Next",))
        page.on_key = lambda key: None  # Escape does not close
        state = state_for(page)
        result = guide_walk.attempt_step(state, save_step())
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("covered the page", result.note)
        # Next should never be clicked
        self.assertNotIn(("button", "Next"), clicks(page))
        # Escape should have been tried (may be multiple times as the walk polls)
        self.assertGreater(len(page.keys), 0)
        self.assertTrue(all(k == "Escape" for k in page.keys))


POPUP_HTML = """
<button id="save-btn" onclick="window.clicked.push('Save')"
        style="display:none">Save</button>
<div role="dialog" aria-modal="true" id="guidance"
     style="position:fixed;inset:0;background:#fff;z-index:9999">
  <h2>Meet the new Guidance Center</h2>
  <p>Step 1 of 2</p>
  {buttons}
</div>
<script>
  window.clicked = [];
  document.addEventListener('click', e => {{
    const text = e.target.textContent;
    if (text === 'Dismiss' || text === 'X') {{
      document.getElementById('guidance').remove();
      document.getElementById('save-btn').style.display = 'block';
    }} else if (text === 'Next') {{
      // Next does nothing
    }}
  }});
</script>
"""


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class PopupCloseEarlyBrowserTests(unittest.TestCase):
    """Real browser tests for popup close early."""

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

    def test_popup_covering_save_is_closed_early_in_real_browser(self):
        """In a real browser, the popup covering Save is closed early (not after timeout),
        and Save is then clicked."""
        page = self.browser.new_page()
        self.addCleanup(page.close)
        buttons = '<button onclick="window.clicked.push(\'Dismiss\')">Dismiss</button>'
        buttons += '<button>Next</button>'
        buttons += '<button aria-label="Close" onclick="window.clicked.push(\'Close\')">X</button>'
        page.set_content(POPUP_HTML.format(buttons=buttons))

        clock = Clock()
        state = guide_walk.WalkState(
            page=page, timeout_s=2, interval_s=0.2, clock=clock, sleeper=clock.sleep,
            page_wait_s=10, page_poll_s=0.5,
        )
        state.step_deadline = clock() + 30

        result = guide_walk.attempt_step(state, save_step())

        # The popup should be closed early (well before the 30s cap)
        self.assertLess(clock(), 15, "popup should be closed early in real browser")
        self.assertEqual(len(state.popup_notes), 1)
        self.assertIn("W-080", state.popup_notes[0])
        clicked = page.evaluate("window.clicked")
        self.assertIn("Save", clicked)
        # Either Dismiss or Close was used, not Next
        self.assertTrue("Dismiss" in clicked or "Close" in clicked)
        self.assertNotIn("Next", clicked)

    def test_popup_delayed_appearance_is_handled_in_real_browser(self):
        """The popup appears after a delay, is detected and closed, then Save is clicked."""
        page = self.browser.new_page()
        self.addCleanup(page.close)
        buttons = '<button aria-label="Close" onclick="window.clicked.push(\'Close\')">X</button>'
        buttons += '<button>Next</button>'
        html = POPUP_HTML.format(buttons=buttons)
        # Start with the dialog hidden
        html = html.replace('id="guidance"', 'id="guidance" style="display:none"')
        page.set_content(html)

        # Show the dialog after 1 second
        page.evaluate("""
            setTimeout(() => {
                document.getElementById('guidance').style.display = 'block';
            }, 1000);
        """)

        clock = Clock()
        state = guide_walk.WalkState(
            page=page, timeout_s=2, interval_s=0.2, clock=clock, sleeper=clock.sleep,
            page_wait_s=10, page_poll_s=0.5,
        )
        state.step_deadline = clock() + 30

        result = guide_walk.attempt_step(state, save_step())

        clicked = page.evaluate("window.clicked")
        self.assertIn("Save", clicked)
        self.assertEqual(len(state.popup_notes), 1)
        self.assertIn("W-080", state.popup_notes[0])


if __name__ == "__main__":
    unittest.main()
