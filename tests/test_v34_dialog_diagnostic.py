"""V34: when W-080 cannot close a blocking dialog, the ENV_BLOCKED note includes
diagnostic info with the redacted accessibility tree computed by the browser."""

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from tests.test_guide_walk import Clock, Page, inventory_of, step, walk_guide


def accounts_step():
    return step("open-accounts", "Exercise 2: Data Model", "Task 1: Open a list", "1",
                "Select Accounts from the App Launcher.")


class RedactionTests(unittest.TestCase):
    """Pure function tests for aria snapshot redaction (no browser)."""

    def test_dismiss_names_are_kept(self):
        snapshot = 'button "Close"\nbutton "Dismiss"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn('"Close"', result)
        self.assertIn('"Dismiss"', result)

    def test_non_dismiss_names_are_redacted(self):
        snapshot = 'button "Custom Title"\nheading "Sample Heading"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Custom Title", result)
        self.assertNotIn("Sample Heading", result)
        self.assertIn('<text>', result)

    def test_url_lines_are_dropped(self):
        snapshot = 'link "Home"\n/url: https://example.com'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("https://example.com", result)
        self.assertNotIn("/url:", result)

    def test_inline_text_single_x_is_kept(self):
        snapshot = 'button "Close": x'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn(": x", result)

    def test_inline_text_non_alphanumeric_glyphs_kept(self):
        snapshot = 'button "Close": ×'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn(": ×", result)

    def test_inline_text_words_redacted(self):
        snapshot = 'button "Close": Close tab'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn(": <text>", result)
        self.assertNotIn("Close tab", result)

    def test_structure_preserved_with_nesting(self):
        snapshot = '- button "Close": x\n  - img'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn("button", result)
        self.assertIn("img", result)

    def test_output_truncated_at_600_chars(self):
        long_snapshot = "\n".join([f'button "Close"' for _ in range(100)])
        result = guide_walk._redact_aria_snapshot(long_snapshot)
        self.assertLessEqual(len(result), 603)
        if len(long_snapshot) > 600:
            self.assertTrue(result.endswith("..."))

    def test_dismiss_names_case_insensitive(self):
        snapshot = 'button "close"\nbutton "DISMISS"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn('"close"', result)
        self.assertIn('"DISMISS"', result)


class FakePageDiagnosticTests(unittest.TestCase):
    """Tests with the fake Page that dialog diagnostic is collected."""

    def popup_state(self, page, left=60):
        clock = Clock()
        state = guide_walk.WalkState(page, clock=clock, sleeper=clock.sleep)
        state.step_deadline = clock.now + left
        return state

    def add_uncloseable_popup(self, page, title):
        dialog = ("dialog", title)
        page.roles.add(dialog)
        return dialog

    def test_uncloseable_dialog_gets_diagnostic_note(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        result = guide_walk._popup_retry(state, accounts_step(), blocked, lambda: blocked)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("Escape pressed yes", result.note)
        self.assertIn("click attempted no", result.note)

    def test_time_nearly_used_skips_snapshot(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 1)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        result = guide_walk._popup_retry(state, accounts_step(), blocked, lambda: blocked)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("tree: skipped, step time nearly used", result.note)

    def test_promo_path_does_not_collect_diagnostic(self):
        page = Page("https://example.lightning.force.com/lightning/setup/Flows/home")
        page.roles.add(("dialog", "Sample Promo Heading\nGo Mobile\nClose"))
        page.roles.add(("button", "Close"))
        page.children[("dialog", "Sample Promo Heading\nGo Mobile\nClose")] = [("button", "Close")]
        state = self.popup_state(page, 60)

        def broken_close(kind, name):
            raise RuntimeError("close fails")

        page.on_click = broken_close
        result = guide_walk._promo_cleared(state, accounts_step())
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertNotIn("Diagnostic:", result.note)


HTML_TEMPLATE = """
<div role="dialog" aria-modal="true" id="test-dialog"
     style="position:fixed;inset:0;background:rgba(0,0,0,0.8);color:white;padding:20px">
  <h2>Test Dialog</h2>
  {content}
</div>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>
  window.actionRan = false;
  window.clickCount = 0;
  {script}
</script>
"""


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class DialogDiagnosticBrowserTests(unittest.TestCase):
    """Real browser tests through _popup_retry with synthetic HTML."""

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

    def run_with_dialog(self, content, script="", collect_diagnostic=True, action_works=False):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(HTML_TEMPLATE.format(content=content, script=script))
        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            # Check if dialog is gone
            try:
                dialog_visible = page.locator("#test-dialog").is_visible()
            except Exception:
                dialog_visible = False

            if not dialog_visible and action_works:
                # Dialog gone, try clicking the target button
                try:
                    page.locator("#target").click(timeout=500)
                    if page.evaluate("window.actionRan"):
                        return None
                except Exception:
                    pass

            if page.evaluate("window.actionRan"):
                return None
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun, collect_diagnostic=collect_diagnostic)
        return result, page

    def test_svg_button_no_escape_has_diagnostic_with_img(self):
        content = '<button aria-label=""><svg><title>Icon</title></svg></button>'
        result, page = self.run_with_dialog(content)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("click attempted no", result.note)
        self.assertIn("Escape pressed yes", result.note)
        self.assertIn("still visible yes", result.note)
        self.assertIn("img", result.note)
        self.assertNotIn("Icon", result.note)

    def test_close_outside_dialog_not_in_tree(self):
        outside_close = '<button id="outside-close" style="position:fixed;top:0;right:0" onclick="window.clickCount++">Close</button>'
        content = '<h2>Sample Title</h2>' + outside_close
        script = 'document.getElementById("outside-close").remove();'
        result, page = self.run_with_dialog(content, script)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertNotIn('"Close"', result.note)
        self.assertEqual(page.evaluate("window.clickCount"), 0)

    def test_dialog_in_iframe_has_tree(self):
        iframe_content = '<iframe id="frame" srcdoc="<div role=\'dialog\'><button>Close</button></div>"></iframe>'
        result, page = self.run_with_dialog(iframe_content)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("tree:", result.note)
        self.assertNotIn("tree: unavailable", result.note)

    def test_close_works_but_dialog_reappears(self):
        # Close button removes the dialog, but it gets re-added synchronously
        content = '''<button onclick="
            const d = document.getElementById('test-dialog');
            d.remove();
            document.body.insertBefore(d, document.body.firstChild);
        ">Close</button>'''
        result, page = self.run_with_dialog(content)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("click attempted yes", result.note)
        self.assertIn("still visible yes", result.note)

    def test_made_up_titles_not_in_note(self):
        content = '<h2>Custom Modal Title</h2><p>Some page text here that should never appear</p>'
        result, page = self.run_with_dialog(content)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertNotIn("Custom Modal Title", result.note)
        self.assertNotIn("Some page text here", result.note)
        self.assertIn("<text>", result.note)

    def test_successful_close_has_no_diagnostic(self):
        content = '<button onclick="document.getElementById(\'test-dialog\').remove()">Close</button>'
        result, page = self.run_with_dialog(content, action_works=True)
        self.assertIsNone(result)
        self.assertTrue(page.evaluate("window.actionRan"))


if __name__ == "__main__":
    unittest.main()
