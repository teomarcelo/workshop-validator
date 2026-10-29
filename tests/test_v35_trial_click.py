"""V35: when multiple unrelated dialogs are open, pick the one whose dismiss control
can actually be clicked (not covered), using Playwright's trial click."""

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from tests.test_guide_walk import Clock, Page, step


def sample_step():
    return step("sample-action", "Exercise 1: Setup", "Task 1: Test", "1",
                "Select Sample from the menu.")


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class TrialClickBrowserTests(unittest.TestCase):
    """Real browser tests with nested stacking contexts and multiple dialogs."""

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

    def test_a_base_note_with_covered_close_button(self):
        """Test (a): reproduce the exact live note on e640453.

        On e640453 the note is exactly: "modal covered the page. Diagnostic: click attempted no;
        Escape pressed yes; still visible yes; tree: - dialog "Close" /   - button "Close" /   - link "<text>"."

        After the fix: result is None, #fg is gone, #bg is still there, #bgc was never clicked.
        """
        html = """<!DOCTYPE html><html><body>
<div role="dialog" id="bg" aria-labelledby="bgc" style="position:fixed;top:60px;left:60px;width:500px;height:300px;z-index:100;background:#eee">
  <button id="bgc" style="width:0;height:0;padding:0;border:0;overflow:hidden" onclick="window.bgClickCount++">Close</button>
  <a href="#">Sample link</a>
</div>
<div role="dialog" id="fg" style="position:fixed;top:40px;left:40px;width:640px;height:420px;z-index:200;background:#fff">
  <p>Sample promo text</p>
  <button id="dis" onclick="document.getElementById('fg').remove()">Dismiss</button>
</div>
<button id="target" onclick="window.ran=true">Target</button>
<script>window.ran = false; window.bgClickCount = 0;</script>
</body></html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            if page.locator("#fg").count() == 0:
                # Dialog is gone, click the target
                try:
                    page.locator("#target").click(timeout=500)
                    if page.evaluate("window.ran"):
                        return None
                except Exception:
                    pass
            return blocked

        result = guide_walk._popup_retry(state, sample_step(), blocked, rerun)

        # After the fix: result is None, #fg is gone, #bg is still there, #bgc was never clicked
        self.assertIsNone(result)
        self.assertFalse(page.locator("#fg").is_visible())
        self.assertTrue(page.locator("#bg").is_visible())
        self.assertEqual(page.evaluate("window.bgClickCount"), 0)
        # Assert the action ran (cheap extra (a))
        self.assertTrue(page.evaluate("window.ran"))

    def test_b_nested_stacking_picks_covering_dialog(self):
        """Test (b): nested stacking with visible background "Close".

        DOM order alone picks wrong. The fix picks the covering dialog,
        and the background "Close" is never clicked (its click counter stays 0).
        """
        html = """<!DOCTYPE html>
<html>
<body>
<!-- Background dialog with z-index 9999 inside parent with z-index 1 (smaller, doesn't cover target) -->
<div id="bg-container" style="position:fixed; top:0; left:0; width:300px; height:200px; z-index:1">
  <div role="dialog" aria-modal="true" id="bg-dialog"
       style="position:fixed; top:0; left:0; width:300px; height:200px; background:rgba(0,0,0,0.5); z-index:9999; padding:20px">
    <button id="bg-close" onclick="window.bgClickCount++; document.getElementById('bg-container').remove()">Close</button>
    <div>Background Dialog</div>
  </div>
</div>
<!-- Covering dialog with z-index 10 inside parent with z-index 2 (covers entire viewport) -->
<div id="covering-container" style="position:fixed; inset:0; z-index:2">
  <div role="dialog" aria-modal="true" id="covering-dialog"
       style="position:fixed; inset:0; background:rgba(255,255,255,0.95); z-index:10; padding:40px">
    <h2>Covering Dialog</h2>
    <button id="covering-dismiss" onclick="document.getElementById('covering-container').remove()">Dismiss</button>
  </div>
</div>
<button id="target" style="position:fixed; bottom:10px; right:10px" onclick="window.actionRan=true">Target Action</button>
<script>
  window.actionRan = false;
  window.bgClickCount = 0;
</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED",
            "modal covered the page; the guide does not mention it; the walk did not close it.",
            (), freeze=True)

        def rerun():
            try:
                # Only covering dialog needs to be gone for action to succeed
                if not page.locator("#covering-container").is_visible():
                    page.locator("#target").click(timeout=500)
                    if page.evaluate("window.actionRan"):
                        return None
            except Exception:
                pass
            return blocked

        result = guide_walk._popup_retry(state, sample_step(), blocked, rerun)

        # With the fix: covering dialog's "Dismiss" is clicked, action resumes, no diagnostic
        self.assertIsNone(result)
        self.assertTrue(page.evaluate("window.actionRan"))
        # Background "Close" was never clicked
        self.assertEqual(page.evaluate("window.bgClickCount"), 0)

    def test_c_one_dialog_no_trial_click(self):
        """Test (c): one dialog: no trial click is made and no count note."""
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="single-dialog"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px">
  <h2>Single Dialog</h2>
  <button onclick="document.getElementById('single-dialog').remove()">Close</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED",
            "modal covered the page; the guide does not mention it; the walk did not close it.",
            (), freeze=True)

        def rerun():
            try:
                if not page.locator("#single-dialog").is_visible():
                    page.locator("#target").click(timeout=500)
                    if page.evaluate("window.actionRan"):
                        return None
            except Exception:
                pass
            return blocked

        result = guide_walk._popup_retry(state, sample_step(), blocked, rerun)

        # Dialog is closed, action succeeds
        self.assertIsNone(result)
        self.assertTrue(page.evaluate("window.actionRan"))

    def test_c2_one_dialog_uncloseable_no_count_note(self):
        """Test for mutations 'one-candidate skip removed' and 'count note with one dialog'.

        With one uncloseable dialog, no trial click should be attempted and diagnostic
        should not contain "candidate dialogs" text.
        """
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="single-dialog"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px">
  <h2>Single Dialog</h2>
  <button id="close-btn" style="pointer-events:none">Close</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        # Monkeypatch to spy on trial clicks
        trial_click_count = [0]
        from unittest.mock import patch
        from playwright.sync_api import Locator

        original_click = Locator.click
        def spy_click(self, *args, **kwargs):
            if kwargs.get('trial') is True:
                trial_click_count[0] += 1
            return original_click(self, *args, **kwargs)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED",
            "modal covered the page; the guide does not mention it; the walk did not close it.",
            (), freeze=True)

        def rerun():
            return blocked

        with patch.object(Locator, 'click', spy_click):
            result = guide_walk._popup_retry(state, sample_step(), blocked, rerun)

        # Dialog cannot be closed, but diagnostic should not have candidate count
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        # Kill mutation: "count note with one dialog" - no count text with single dialog
        self.assertNotIn("candidate dialogs", result.note)
        # Kill mutation: "one-candidate skip removed" - no trial click with single dialog
        self.assertEqual(trial_click_count[0], 0)

    def test_d_after_shown_before_one_new_dialog_closed(self):
        """Test (d): after _mark_shown_before, one new unrelated dialog with "Dismiss" is closed."""
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="dialog1"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px">
  <h2>First Dialog</h2>
  <button onclick="document.getElementById('dialog1').remove()">Dismiss</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60

        # Mark the first dialog as shown
        found = guide_walk._unnamed_modal(page, "", state=state)
        self.assertIsNotNone(found)
        dialog, title, _count, _method = found
        guide_walk._mark_shown_before(dialog)

        # Now add a new dialog
        page.evaluate("""
            const dialog = document.createElement('div');
            dialog.id = 'dialog2';
            dialog.setAttribute('role', 'dialog');
            dialog.setAttribute('aria-modal', 'true');
            dialog.style = 'position:fixed;inset:0;background:rgba(200,200,200,0.95);padding:40px';
            dialog.innerHTML = '<h2>New Dialog</h2><button onclick="document.getElementById(\\'dialog2\\').remove()">Dismiss</button>';
            document.body.appendChild(dialog);
        """)

        blocked = guide_walk.CheckResult("ENV_BLOCKED",
            "modal covered the page; the guide does not mention it; the walk did not close it.",
            (), freeze=True)

        def rerun():
            try:
                if not page.locator("#dialog2").is_visible():
                    page.locator("#target").click(timeout=500)
                    if page.evaluate("window.actionRan"):
                        return None
            except Exception:
                pass
            return blocked

        result = guide_walk._popup_retry(state, sample_step(), blocked, rerun)

        # New dialog is closed, action succeeds
        self.assertIsNone(result)
        self.assertTrue(page.evaluate("window.actionRan"))

    def test_e_two_dialogs_neither_dismiss_passes(self):
        """Test (e): two dialogs where neither dismiss control passes.

        The first candidate is picked, and the note says "2 candidate dialogs; picked by order".
        """
        html = """<!DOCTYPE html>
<html>
<body>
<!-- Two dialogs with no working dismiss buttons -->
<div role="dialog" aria-modal="true" id="dialog1"
     style="position:fixed; inset:0; background:rgba(0,0,0,0.5); padding:20px">
  <h2>First Dialog</h2>
  <button style="pointer-events:none">Close</button>
</div>
<div role="dialog" aria-modal="true" id="dialog2"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px">
  <h2>Second Dialog</h2>
  <button style="pointer-events:none">Dismiss</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED",
            "modal covered the page; the guide does not mention it; the walk did not close it.",
            (), freeze=True)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, sample_step(), blocked, rerun)

        # Neither dismiss control works, first candidate is picked
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("2 candidate dialogs; picked by order", result.note)

    def test_f_guide_named_dialog_never_picked(self):
        """Test (f): a guide-named dialog is never picked or closed."""
        step_text = "Select Sample Settings from the menu."
        html = """<!DOCTYPE html>
<html>
<body>
<!-- Guide-named dialog (title mentioned in step) -->
<div role="dialog" aria-modal="true" id="guide-dialog"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px">
  <h2>Sample Settings</h2>
  <button id="guide-close" onclick="document.getElementById('guide-dialog').remove()">Close</button>
</div>
<!-- Unrelated dialog -->
<div role="dialog" aria-modal="true" id="unrelated-dialog"
     style="position:fixed; inset:0; background:rgba(200,200,200,0.95); padding:40px; z-index:10">
  <h2>Unrelated Dialog</h2>
  <button onclick="window.outsideClickCount++">Outside</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>
  window.actionRan = false;
  window.outsideClickCount = 0;
</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED",
            "modal covered the page; the guide does not mention it; the walk did not close it.",
            (), freeze=True)

        step_with_guide_name = step("sample-action", "Exercise 1: Setup", "Task 1: Test", "1", step_text)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, step_with_guide_name, blocked, rerun)

        # Only unrelated dialog is found and attempted
        self.assertEqual(result.status, "ENV_BLOCKED")
        # Outside button was never clicked
        self.assertEqual(page.evaluate("window.outsideClickCount"), 0)
        # Guide-named dialog is still visible
        self.assertTrue(page.locator("#guide-dialog").is_visible())

    def test_g_time_cutoff_no_trial_click(self):
        """Test (g): with step_deadline < 2s away, no trial click is attempted.

        With 1.9s left, time check breaks the loop immediately (picked by order).
        """
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="dialog1"
     style="position:fixed; top:0; left:0; width:300px; height:200px; background:#eee">
  <h2>First Dialog</h2>
  <button style="pointer-events:none">Close</button>
</div>
<div role="dialog" aria-modal="true" id="dialog2"
     style="position:fixed; top:220px; left:0; width:300px; height:200px; background:#ddd">
  <h2>Second Dialog</h2>
  <button id="working-dismiss" onclick="document.getElementById('dialog2').remove()">Dismiss</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        # Only 1.9 seconds left (cutoff is 2.0, so trial loop should break immediately)
        state.step_deadline = state.clock() + 1.9

        found = guide_walk._unnamed_modal(page, "", state=state)
        self.assertIsNotNone(found)
        dialog, title, count, method = found
        self.assertEqual(count, 2)
        # Should pick by order since time check broke the loop
        self.assertEqual(method, "order")
        # Time should still be >= 1.0s (we didn't waste time on trial clicks)
        time_left_after = guide_walk._time_left(state)
        self.assertGreaterEqual(time_left_after, 1.0,
            f"Selection left {time_left_after:.2f}s, expected >= 1.0s (time check should have broken loop)")

    def test_h_no_page_writes_dedup(self):
        """Test for de-dup mutation: no data-wv-dedup* attributes after scans, one pop-up counted once."""
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="dialog1"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px">
  <h2>Dialog Title</h2>
  <button onclick="document.getElementById('dialog1').remove()">Close</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60

        # Scan multiple times
        for _ in range(3):
            found = guide_walk._unnamed_modal(page, "", state=state)
            self.assertIsNotNone(found)
            dialog, title, count, method = found
            self.assertEqual(count, 1)  # One pop-up counted once

        # Check no data-wv-dedup* attributes exist
        dedup_attrs = page.evaluate("""
            () => {
                const allElements = document.querySelectorAll('*');
                for (let el of allElements) {
                    for (let attr of el.attributes) {
                        if (attr.name.startsWith('data-wv-dedup')) {
                            return attr.name;
                        }
                    }
                }
                return null;
            }
        """)
        self.assertIsNone(dedup_attrs, f"Found deduplication attribute: {dedup_attrs}")

    def test_i_time_check_per_candidate(self):
        """Test for time-cutoff-per-candidate mutation: check time before EACH trial click.

        With 3 covered candidates and ~2.5s left, selection must leave at least 1s of step time.
        """
        html = """<!DOCTYPE html>
<html>
<body>
<!-- Three covered dialogs (pointer-events:none) -->
<div role="dialog" aria-modal="true" id="dialog1"
     style="position:fixed; top:0; left:0; width:300px; height:200px; background:#eee">
  <h2>Dialog 1</h2>
  <button style="pointer-events:none">Close</button>
</div>
<div role="dialog" aria-modal="true" id="dialog2"
     style="position:fixed; top:220px; left:0; width:300px; height:200px; background:#ddd">
  <h2>Dialog 2</h2>
  <button style="pointer-events:none">Close</button>
</div>
<div role="dialog" aria-modal="true" id="dialog3"
     style="position:fixed; top:440px; left:0; width:300px; height:200px; background:#ccc">
  <h2>Dialog 3</h2>
  <button style="pointer-events:none">Dismiss</button>
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        # About 2.5 seconds left - enough to try one candidate, then time check breaks the loop
        state.step_deadline = state.clock() + 2.5

        found = guide_walk._unnamed_modal(page, "", state=state)
        self.assertIsNotNone(found)
        dialog, title, count, method = found
        self.assertEqual(count, 3)
        # Should pick by order since no trial clicks succeeded and time ran low
        self.assertEqual(method, "order")

        # Assert at least 1 second of step time left (time check stops before exhausting time)
        time_left_after = guide_walk._time_left(state)
        self.assertGreaterEqual(time_left_after, 1.0,
            f"Selection left {time_left_after:.2f}s, expected >= 1.0s (per-candidate time check must preserve step time)")
