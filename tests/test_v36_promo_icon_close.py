"""V36: when _promo_cleared finds a promo but cannot close it, append the V34 diagnostic
(CloseAttempt flags plus the allow-list-redacted aria_snapshot) to the ENV_BLOCKED note.

This includes:
- (a) promo with no closeable button (Escape-only path)
- (b) promo that closes successfully (no diagnostic)
- (c) promo that comes back after closing (came-back diagnostic)

The diagnostic redacts button names not in the dismiss allow-list; the glyph value
"×" may appear in the redacted tree if it's an allowed glyph value."""

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from tests.test_guide_walk import step


def sample_step():
    return step("sample", "Exercise 1: Setup", "Task 1: Test", "1",
                "Create a new item for testing.")


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class PromoDiagnosticBrowserTests(unittest.TestCase):
    """Real browser tests for promo path with diagnostic."""

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

    def test_promo_uncloseable_has_diagnostic_no_body_text(self):
        """Test (a): promo with X button (accessible name "×") not in the allow-list.

        Exercises the Escape-only path (click attempted no). On base 450a11d: note
        is bare PROMO_NOTE. With the change: note contains "Diagnostic:" and "tree:".
        """
        # Unique sentinel string in dialog body (should never appear in note)
        sentinel = "UniquePromoSentinel2026XYZ"
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="promo-dialog"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px; z-index:1000">
  <h2>Sample Promo Title</h2>
  <p>Go mobile with our new app!</p>
  <p>""" + sentinel + """</p>
  <button aria-label="" style="position:absolute; top:10px; right:10px; width:30px; height:30px">×</button>
</div>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto("about:blank")
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=30, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60

        # Call _promo_cleared directly
        result = guide_walk._promo_cleared(state, sample_step())

        # Should get ENV_BLOCKED with diagnostic
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("A promotional dialog covered the page", result.note)
        # Assert the exact diagnostic prefix from real browser
        self.assertIn("Diagnostic: promo on the active tab yes; click attempted no; Escape pressed yes; still visible yes; tree: - dialog", result.note)
        # This is not the came-back case
        self.assertNotIn("came back", result.note)
        # Sentinel body text must not appear in note (redaction works)
        self.assertNotIn(sentinel, result.note)
        self.assertNotIn("Sample Promo Title", result.note)

    def test_promo_closeable_unchanged_no_diagnostic(self):
        """Test (b): closable promo is unchanged.

        It closes successfully (returns None), PROMO_CLOSED_NOTE is recorded,
        and no diagnostic is added anywhere.
        """
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="promo-dialog"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px; z-index:1000">
  <h2>Sample Promo Title</h2>
  <p>Go mobile with our new app!</p>
  <button id="close-btn" onclick="document.getElementById('promo-dialog').remove()">Close</button>
</div>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto("about:blank")
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=30, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60

        # Call _promo_cleared directly
        result = guide_walk._promo_cleared(state, sample_step())

        # When promo closes successfully, _promo_cleared returns None (promo is gone)
        self.assertIsNone(result)
        # Promo close note should be recorded
        self.assertEqual(len(state.popup_notes), 1)
        self.assertIn("Environment: closed a promotional pop-up", state.popup_notes[0])
        # No diagnostic should appear in the first popup note
        self.assertNotIn("Diagnostic:", state.popup_notes[0])



    def test_promo_closes_then_comes_back_has_diagnostic(self):
        """Test (c): promo closes successfully but then comes back.

        The dialog has a Close button that removes the first dialog and adds a
        second one. The note should contain "came back after 1 close" and "tree:".
        Page body text (sentinel) must not appear in the note.
        """
        sentinel = "UniqueCameBackSentinel2026ABC"
        html = """<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" id="promo-dialog"
     style="position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px; z-index:1000">
  <h2>First Promo</h2>
  <p>Go mobile with our new app!</p>
  <p>""" + sentinel + """</p>
  <button id="close-btn" onclick="
    document.getElementById('promo-dialog').remove();
    let newDialog = document.createElement('div');
    newDialog.setAttribute('role', 'dialog');
    newDialog.setAttribute('aria-modal', 'true');
    newDialog.id = 'promo-dialog-2';
    newDialog.style = 'position:fixed; inset:0; background:rgba(255,255,255,0.95); padding:40px; z-index:1000';
    newDialog.innerHTML = '<h2>Second Promo</h2><p>Go mobile with our app!</p><p>""" + sentinel + """</p>';
    document.body.appendChild(newDialog);
  ">Close</button>
</div>
</body>
</html>"""

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.goto("about:blank")
        page.set_content(html)

        state = guide_walk.WalkState(page=page, timeout_s=30, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60

        # Call _promo_cleared directly
        result = guide_walk._promo_cleared(state, sample_step())

        # Should get ENV_BLOCKED with came-back diagnostic
        self.assertIsNotNone(result)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("A promotional dialog covered the page", result.note)
        self.assertIn("Diagnostic: the dialog came back after 1 close", result.note)
        # Tree should start with a real dialog line
        self.assertIn("tree: - dialog /", result.note)
        # Sentinel body text must not appear in note (redaction works)
        self.assertNotIn(sentinel, result.note)
        self.assertNotIn("First Promo", result.note)
        self.assertNotIn("Second Promo", result.note)


if __name__ == "__main__":
    unittest.main()
