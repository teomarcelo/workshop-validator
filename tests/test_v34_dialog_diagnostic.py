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


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class RedactionTests(unittest.TestCase):
    """Redaction tests using REAL Chromium aria_snapshot output."""

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

    def get_snapshot(self, html):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(html)
        dialog = page.locator("[role='dialog']").first
        return dialog.aria_snapshot()

    def test_dismiss_names_kept_case_insensitive(self):
        html = '<div role="dialog"><button>Close</button><button>DISMISS</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn('"Close"', result)
        self.assertIn('"DISMISS"', result)

    def test_non_dismiss_names_redacted(self):
        html = '<div role="dialog"><button>Custom Title</button><h2>Sample Heading</h2></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Custom Title", result)
        self.assertNotIn("Sample Heading", result)
        self.assertIn('<text>', result)

    def test_url_property_lines_dropped(self):
        html = '<div role="dialog"><a href="https://example.com?foo=bar&baz=qux">Home</a></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("https://example.com", result)
        self.assertNotIn("/url:", result)
        self.assertNotIn("foo=bar", result)

    def test_glyphs_kept(self):
        for glyph in ["×", "✕", "✖", "x", "X"]:
            html = f'<div role="dialog"><button aria-label="Close">{glyph}</button></div>'
            snapshot = self.get_snapshot(html)
            result = guide_walk._redact_aria_snapshot(snapshot)
            self.assertIn(glyph, result)

    def test_glyph_prefixed_values_redacted(self):
        html = '<div role="dialog"><button aria-label="Test">x Delta words</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Delta", result)
        self.assertNotIn("words", result)

    def test_quoted_values_redacted(self):
        html = '<div role="dialog"><div>- Zorblat Dash</div></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Zorblat", result)

    def test_emails_in_links_and_text_redacted(self):
        html = '<div role="dialog"><a href="mailto:jane@example.test">jane@example.test</a><p>Contact: admin@example.test</p></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("jane@example.test", result)
        self.assertNotIn("admin@example.test", result)

    def test_short_period_names_redacted(self):
        html = '<div role="dialog"><button>J.</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("J.", result)
        self.assertIn("<text>", result)

    def test_escaped_quotes_in_names_redacted(self):
        html = '<div role="dialog"><button>He said \\"hello\\"</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("hello", result)

    def test_text_before_and_after_quotes_dropped(self):
        html = '<div role="dialog"><p>Some text before "quoted" and after</p></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("before", result)
        self.assertNotIn("after", result)

    def test_no_made_up_page_values_survive(self):
        html = '<div role="dialog"><h2>Custom Modal Title</h2><p>Some page text here</p><button>Action Button</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Custom Modal Title", result)
        self.assertNotIn("Some page text here", result)
        self.assertNotIn("Action Button", result)


HTML_TEMPLATE = """
<div role="dialog" aria-modal="true" id="test-dialog"
     style="position:fixed;inset:0;background:rgba(0,0,0,0.8);color:white;padding:20px">
  <h2>Test Dialog</h2>
  {content}
</div>
{outside_content}
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>
  window.actionRan = false;
  window.outsideClickCount = 0;
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

    def run_with_dialog(self, content, script="", outside_content="", action_works=False):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(HTML_TEMPLATE.format(content=content, script=script, outside_content=outside_content))
        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            try:
                dialog_visible = page.locator("#test-dialog").is_visible()
            except Exception:
                dialog_visible = False

            if not dialog_visible and action_works:
                try:
                    page.locator("#target").click(timeout=500)
                    if page.evaluate("window.actionRan"):
                        return None
                except Exception:
                    pass

            if page.evaluate("window.actionRan"):
                return None
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun, collect_diagnostic=True)
        return result, page

    def test_svg_button_no_escape_has_diagnostic_with_img(self):
        content = '<button aria-label=""><svg><title>Icon</title></svg></button>'
        result, page = self.run_with_dialog(content)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("click attempted no", result.note)
        self.assertIn("Escape pressed yes", result.note)
        self.assertIn("still visible yes", result.note)
        self.assertIn("img", result.note)
        self.assertNotIn("Icon", result.note)

    def test_close_outside_dialog_not_in_tree(self):
        content = '<h2>Inside Dialog</h2>'
        outside_content = '<button id="outside-close" onclick="window.outsideClickCount++" style="position:fixed;top:0;right:0;z-index:9999">Close</button>'
        result, page = self.run_with_dialog(content, outside_content=outside_content)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("Diagnostic:", result.note)
        # Outside button should not be in tree
        snapshot_part = result.note.split("tree: ")[1] if "tree: " in result.note else ""
        self.assertNotIn('"Close"', snapshot_part)
        # Outside button was never clicked
        self.assertEqual(page.evaluate("window.outsideClickCount"), 0)

    def test_dialog_with_nested_content_has_tree(self):
        # Test that nested content in dialog is captured in tree
        content = '<div><h2>Nested Title</h2><div><button>Nested Button</button></div></div>'
        result, page = self.run_with_dialog(content)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("tree:", result.note)
        self.assertNotIn("tree: unavailable", result.note)
        self.assertNotIn("tree: dialog gone", result.note)
        # Verify structure is preserved
        self.assertIn("heading", result.note)
        self.assertIn("button", result.note)

    def test_dialog_in_iframe_has_tree(self):
        # The blocking dialog lives INSIDE a srcdoc iframe, has no dismiss control, Escape does not close it
        iframe_html = '''<!DOCTYPE html>
<html>
<body>
<div role="dialog" aria-modal="true" style="position:fixed;inset:0;background:rgba(0,0,0,0.8);color:white;padding:20px">
  <h2>Dialog in iframe</h2>
  <button>No dismiss</button>
</div>
</body>
</html>'''

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(f'''<!DOCTYPE html>
<html>
<body>
<iframe id="frame" srcdoc="{iframe_html.replace('"', '&quot;')}"></iframe>
<button id="target" onclick="window.actionRan=true">Target Action</button>
<script>window.actionRan = false;</script>
</body>
</html>''')

        # Wait for iframe to load
        page.wait_for_timeout(500)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return None if page.evaluate("window.actionRan") else blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun, collect_diagnostic=True)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("click attempted no", result.note)
        self.assertIn("Escape pressed yes", result.note)
        self.assertIn("still visible yes", result.note)
        # Tree should come from the iframe dialog, containing its own roles
        self.assertIn("tree:", result.note)
        self.assertNotIn("tree: unavailable", result.note)
        self.assertNotIn("tree: dialog gone", result.note)
        # Should have the dialog's own content, not just "- iframe"
        self.assertIn("dialog", result.note)
        self.assertIn("heading", result.note)
        self.assertIn("button", result.note)
        # The dialog text should be redacted
        self.assertNotIn("Dialog in iframe", result.note)
        self.assertNotIn("No dismiss", result.note)

    def test_loop_exhausted_shows_came_back_message(self):
        # Test that after closing 3 times (loop limit), the message says "came back after N closes"
        # Close button removes dialog, rerun adds it back so the loop continues
        close_count = [0]
        content = f'''<button id="close-btn" onclick="
            document.getElementById('test-dialog').remove();
            window.closeCount = (window.closeCount || 0) + 1;
        ">Close</button>
        <script>window.closeCount = 0;</script>'''

        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(HTML_TEMPLATE.format(content=content, script="", outside_content=""))

        def add_dialog_back():
            # Re-add dialog after it was removed
            page.evaluate("""
                const dialog = document.createElement('div');
                dialog.id = 'test-dialog';
                dialog.setAttribute('role', 'dialog');
                dialog.setAttribute('aria-modal', 'true');
                dialog.style = 'position:fixed;inset:0;background:rgba(0,0,0,0.8);color:white;padding:20px';
                dialog.innerHTML = '<h2>Test Dialog</h2><button id="close-btn" onclick="document.getElementById(\\'test-dialog\\').remove();window.closeCount++;">Close</button>';
                document.body.insertBefore(dialog, document.body.firstChild);
            """)

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page; the guide does not mention it; the walk did not close it.", (), freeze=True)

        def rerun():
            # After each close, add the dialog back
            add_dialog_back()
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun, collect_diagnostic=True)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("the dialog came back after 3 closes", result.note)
        self.assertIn("click attempted yes", result.note)

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

    def test_escape_raising_shows_not_pressed(self):
        # Dialog with no Close button, test when keyboard.press raises by wrapping page
        content = '<button>Go Mobile</button>'
        real_page = self.browser.new_page()
        self.addCleanup(real_page.close)
        real_page.set_content(HTML_TEMPLATE.format(content=content, script="", outside_content=""))

        # Wrap page to override keyboard
        class PageWrapper:
            def __init__(self, page):
                self._page = page
                self.keyboard = type('Keyboard', (), {'press': lambda self, key: (_ for _ in ()).throw(RuntimeError("Keyboard error"))})()

            def __getattr__(self, name):
                return getattr(self._page, name)

        page = PageWrapper(real_page)
        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun, collect_diagnostic=True)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Escape pressed no", result.note)

    def test_step_near_time_cap_stays_env_blocked_with_freeze(self):
        content = '<button>No dismiss</button>'
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(HTML_TEMPLATE.format(content=content, script="", outside_content=""))
        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 1.5
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun, collect_diagnostic=True)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("tree: skipped, step time nearly used", result.note)


class FakePageDiagnosticTests(unittest.TestCase):
    """Tests with the fake Page for mutation killing."""

    def popup_state(self, page, left=60):
        clock = Clock()
        state = guide_walk.WalkState(page, clock=clock, sleeper=clock.sleep)
        state.step_deadline = clock.now + left
        return state

    def add_uncloseable_popup(self, page, title):
        dialog = ("dialog", title)
        page.roles.add(dialog)
        return dialog

    def test_freeze_preserved_in_diagnostic_result(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True)
        result = guide_walk._add_dialog_diagnostic(state, attempt, blocked)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)

    def test_status_preserved_in_diagnostic_result(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True)
        result = guide_walk._add_dialog_diagnostic(state, attempt, blocked)
        self.assertEqual(result.status, "ENV_BLOCKED")

    def test_handle_none_shows_dialog_gone(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=False, handle=None)
        result = guide_walk._add_dialog_diagnostic(state, attempt, blocked)
        self.assertIn("tree: dialog gone", result.note)

    def test_threshold_below_two_seconds_skips(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 1.9)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True, handle=object())
        result = guide_walk._add_dialog_diagnostic(state, attempt, blocked)
        self.assertIn("tree: skipped, step time nearly used", result.note)


if __name__ == "__main__":
    unittest.main()
