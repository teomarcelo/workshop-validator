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
from tests.test_v32_promo_close import promo_page, flows_list, FLOWS_URL, flow_step, state_for


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

    def test_bullet_prefixed_name_redacted(self):
        html = '<div role="dialog"><button>• Jane Roe</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Jane", result)
        self.assertNotIn("Roe", result)

    def test_times_prefixed_name_redacted(self):
        html = '<div role="dialog"><button>× Foxtrot</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Foxtrot", result)

    def test_at_sign_email_redacted(self):
        html = '<div role="dialog"><p>@ jane@example.test</p></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("jane", result)
        self.assertNotIn("example", result)

    def test_one_character_cjk_name_redacted(self):
        html = '<div role="dialog"><button>李</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("李", result)
        self.assertIn("<text>", result)

    def test_non_english_text_redacted(self):
        html = '<div role="dialog"><h2>Добро пожаловать</h2><p>Bienvenido</p></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("Добро", result)
        self.assertNotIn("пожаловать", result)
        self.assertNotIn("Bienvenido", result)

    def test_non_allow_listed_attribute_dropped(self):
        html = '<div role="dialog"><button aria-label="Test" data-custom="value">Button</button></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn("data-custom", result)
        self.assertNotIn("aria-label", result)

    def test_non_allow_listed_role_name_redacted(self):
        # Chromium may not include invalid roles in accessibility tree, but verify name doesn't leak
        html = '<div role="dialog"><div role="customrole">Custom Content Here</div></div>'
        snapshot = self.get_snapshot(html)
        result = guide_walk._redact_aria_snapshot(snapshot)
        # The role name "customrole" should not appear
        self.assertNotIn("customrole", result)
        # The content should not appear
        self.assertNotIn("Custom Content Here", result)


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

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
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

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
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

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
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

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
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

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertIn("tree: skipped, step time nearly used", result.note)

    def test_production_wiring_through_popup_resume(self):
        # Reach diagnostic through real caller (_popup_resume) without explicit flag
        content = '<h2>Uncloseable Dialog</h2>'
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(HTML_TEMPLATE.format(content=content, script="", outside_content=""))
        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60

        # Simulate a click action that failed
        fallback = guide_walk.CheckResult("UI_MISMATCH", "Control not found.", (), freeze=True)
        result = guide_walk._popup_resume(state, accounts_step(), lambda: False, fallback)

        # Should get diagnostic through the production path
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("tree:", result.note)

    def test_close_click_works_script_reshows_dialog(self):
        # Brief case (d): Close click works, dialog removed, but script re-shows it
        # This happens on loop exhaustion: each close succeeds but rerun adds dialog back
        page = self.browser.new_page()
        self.addCleanup(page.close)
        content = '<button onclick="document.getElementById(\'test-dialog\').remove()">Close</button>'
        page.set_content(HTML_TEMPLATE.format(content=content, script="", outside_content=""))

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page; the guide does not mention it; the walk did not close it.", (), freeze=True)

        def add_dialog_back():
            page.evaluate("""
                const dialog = document.createElement('div');
                dialog.id = 'test-dialog';
                dialog.setAttribute('role', 'dialog');
                dialog.setAttribute('aria-modal', 'true');
                dialog.style = 'position:fixed;inset:0;background:rgba(0,0,0,0.8)';
                dialog.innerHTML = '<h2>Test Dialog</h2><button onclick="document.getElementById(\\'test-dialog\\').remove()">Close</button>';
                document.body.insertBefore(dialog, document.body.firstChild);
            """)

        def rerun():
            # Simulate: action still blocked, and dialog reappears
            add_dialog_back()
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        self.assertIn("the dialog came back after", result.note)
        # Last attempt had click (Close button was found and clicked)
        self.assertIn("click attempted yes", result.note)
        # But on the last check, dialog is gone (removed by last close)
        self.assertIn("still visible no", result.note)
        # Tree should be present with re-found dialog roles (not "dialog gone" stale handle)
        self.assertIn("tree:", result.note)
        self.assertNotIn("tree: dialog gone", result.note)
        self.assertIn("dialog", result.note)
        self.assertIn("heading", result.note)

    def test_still_visible_no_on_successful_final_close_in_loop(self):
        # Test "still visible no" when the final close in the loop succeeds but action still fails
        # Close removes dialog each time, but rerun keeps adding it back, exhausting the loop
        page = self.browser.new_page()
        self.addCleanup(page.close)
        content = '<button onclick="document.getElementById(\'test-dialog\').remove()">Close</button>'
        page.set_content(HTML_TEMPLATE.format(content=content, script="", outside_content=""))

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page; the guide does not mention it; the walk did not close it.", (), freeze=True)

        def add_dialog_back():
            page.evaluate("""
                const dialog = document.createElement('div');
                dialog.id = 'test-dialog';
                dialog.setAttribute('role', 'dialog');
                dialog.setAttribute('aria-modal', 'true');
                dialog.style = 'position:fixed;inset:0;background:rgba(0,0,0,0.8)';
                dialog.innerHTML = '<h2>Test Dialog</h2><button onclick="document.getElementById(\\'test-dialog\\').remove()">Close</button>';
                document.body.insertBefore(dialog, document.body.firstChild);
            """)

        close_count = [0]
        def rerun():
            close_count[0] += 1
            if close_count[0] < 3:  # Add dialog back for first 2 closes
                add_dialog_back()
            # Always return blocked to continue loop (even after final close)
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        self.assertEqual(result.status, "ENV_BLOCKED")
        # On the 3rd (final) close, dialog was removed and not re-added, so still_visible should be no
        self.assertIn("the dialog came back after 3 closes", result.note)
        self.assertIn("still visible no", result.note)

    def test_no_diagnostic_when_no_dialog_found(self):
        # When _unnamed_modal finds no dialog, do not add diagnostic (note stays as on main)
        page = self.browser.new_page()
        self.addCleanup(page.close)
        # No dialog in the page
        page.set_content('<button id="target" onclick="window.actionRan=true">Target</button><script>window.actionRan=false;</script>')

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertEqual(result.note, "modal covered the page.")
        self.assertNotIn("Diagnostic:", result.note)

    def test_valueless_attributes_kept(self):
        # Test that valueless attributes like [disabled], [checked] are kept
        content = '<button disabled>Close</button><input type="checkbox" checked />'
        snapshot_html = f'<div role="dialog">{content}</div>'
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(snapshot_html)
        dialog = page.locator("[role='dialog']").first
        snapshot = dialog.aria_snapshot()
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn("[disabled]", result)
        self.assertIn("[checked]", result)

    def test_600_char_cap_exact(self):
        # Test that the 600-char cap is exact including "..."
        # Create a long snapshot
        content = '<div>' + ''.join(f'<button>Button{i}</button>' for i in range(100)) + '</div>'
        snapshot_html = f'<div role="dialog">{content}</div>'
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(snapshot_html)
        dialog = page.locator("[role='dialog']").first
        snapshot = dialog.aria_snapshot()
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertEqual(len(result), 600)
        self.assertTrue(result.endswith("..."))

    def test_raising_click_not_treated_as_closed(self):
        # Test that a raising W-080 click is not treated as closed
        content = '''<button id="close-btn">Close</button>
        <script>
        document.getElementById('close-btn').addEventListener('click', function() {
            throw new Error('Click error without removal');
        });
        </script>'''
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(HTML_TEMPLATE.format(content=content, script="", outside_content=""))

        state = guide_walk.WalkState(page=page, timeout_s=10, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return None if page.evaluate("window.actionRan") else blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Diagnostic:", result.note)
        # Click raised, so it's recorded
        self.assertIn("click attempted yes", result.note)

    def test_is_visible_gone_check(self):
        # Test the is_visible gone check in _add_dialog_diagnostic
        content = '<button onclick="document.getElementById(\'test-dialog\').remove()">Close</button>'
        result, page = self.run_with_dialog(content, action_works=True)
        # Close worked, action succeeded, no diagnostic
        self.assertIsNone(result)
        self.assertTrue(page.evaluate("window.actionRan"))


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

    def test_click_raised_not_treated_as_closed(self):
        # M1: guide_walk.py:3332 `not attempt.click_raised` removed
        # A click that raises does NOT mean the dialog closed (even if it disappears)
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        close_button = ("button", "Close")
        dialog = ("dialog", "Sample Modal")
        page.roles.add(close_button)
        page.roles.add(dialog)
        page.children[dialog] = [close_button]

        def broken_click(kind, name):
            # Click raises AND dialog disappears (broken handler still removed it)
            page.roles.discard(dialog)
            raise RuntimeError("click error")

        page.on_click = broken_click
        state = self.popup_state(page)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertNotIn("Environment: closed a promotional pop-up", result.note)
        self.assertEqual(state.popup_notes, [])

    def test_escape_raised_at_3332_not_treated_as_closed(self):
        # M2: guide_walk.py:3332 escape_raised check for W-080
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = ("dialog", "Sample Modal")
        page.roles.add(dialog)

        def broken_press(self_kbd, key):
            # Escape raises AND dialog disappears (script removed it despite error)
            page.roles.discard(dialog)
            raise RuntimeError("keyboard error")

        page.keyboard = type('Keyboard', (), {'press': broken_press})()
        state = self.popup_state(page)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertNotIn("Environment: closed", result.note)
        self.assertEqual(state.popup_notes, [])

    def test_escape_raised_at_3395_promo_not_treated_as_closed(self):
        # M5: guide_walk.py:3395 escape_raised check for promo path
        from tests.test_v32_promo_close import PROMO
        page = flows_list(promo_page(FLOWS_URL, buttons=("Go Mobile",)))
        dialog = ("dialog", PROMO)

        def broken_press(self_kbd, key):
            # Escape raises AND promo disappears
            page.roles.discard(dialog)
            raise RuntimeError("keyboard error")

        page.keyboard = type('Keyboard', (), {'press': broken_press})()
        state = state_for(page)
        result = guide_walk.run_screen_flow(state, flow_step())
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertNotIn("Environment: closed", result.note)
        self.assertEqual(state.popup_notes, [])

    def test_escape_raised_flag_set_at_3364(self):
        # M26: guide_walk.py:3364 escape_raised is set when keyboard.press raises
        # This affects the success check, so a raise should not be treated as closed
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = ("dialog", "Sample Modal")
        page.roles.add(dialog)

        def broken_press(self_kbd, key):
            # Escape raises AND dialog disappears
            page.roles.discard(dialog)
            raise RuntimeError("keyboard error")

        page.keyboard = type('Keyboard', (), {'press': broken_press})()
        state = self.popup_state(page)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        def rerun():
            return blocked

        result = guide_walk._popup_retry(state, accounts_step(), blocked, rerun)
        # With escape_raised set correctly, the close is not treated as successful
        self.assertEqual(state.popup_notes, [])
        self.assertIn("Escape pressed no", result.note)

    def test_is_visible_false_shows_dialog_gone(self):
        # M11: guide_walk.py:3615 is_visible() False branch
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        # Create a handle that says not visible
        class FakeHandle:
            def is_visible(self):
                return False

        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True, handle=FakeHandle())
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertIn("tree: dialog gone", result.note)

    def test_is_visible_raises_shows_unavailable(self):
        # M11: guide_walk.py:3615 is_visible() raises
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)

        # Create a handle whose is_visible raises
        class FakeHandle:
            def is_visible(self):
                raise RuntimeError("element detached")

        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True, handle=FakeHandle())
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertIn("tree: unavailable", result.note)
        self.assertNotIn("tree: dialog gone", result.note)

    def test_freeze_preserved_in_diagnostic_result(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True)
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)

    def test_status_preserved_in_diagnostic_result(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True)
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertEqual(result.status, "ENV_BLOCKED")

    def test_handle_none_shows_unavailable(self):
        # No handle means element_handle or marking failed: say "unavailable" not "dialog gone"
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=False, handle=None)
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertIn("tree: unavailable", result.note)
        self.assertNotIn("tree: dialog gone", result.note)

    def test_threshold_below_two_seconds_skips(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        dialog = self.add_uncloseable_popup(page, "Sample Modal")
        state = self.popup_state(page, 1.9)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", '"Sample Modal" modal covered the page.', (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True, handle=object())
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertIn("tree: skipped, step time nearly used", result.note)

    def test_still_visible_default_none_shows_unknown(self):
        # When still_visible is None (default), should show "unknown"
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True)  # still_visible defaults to None
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertIn("still visible unknown", result.note)

    def test_still_visible_yes_mutant(self):
        # Kill the always-yes mutant: explicit True should show "yes"
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=True)
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertIn("still visible yes", result.note)
        self.assertNotIn("still visible no", result.note)

    def test_still_visible_no_mutant(self):
        # Kill the always-yes mutant: explicit False should show "no"
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        state = self.popup_state(page, 60)
        blocked = guide_walk.CheckResult("ENV_BLOCKED", "modal covered the page.", (), freeze=True)
        attempt = guide_walk.CloseAttempt(escape_pressed=True, still_visible=False, handle=None)
        result = guide_walk._add_dialog_diagnostic(state, accounts_step(), attempt, blocked)
        self.assertIn("still visible no", result.note)
        self.assertNotIn("still visible yes", result.note)


class SnapshotRedactionUnitTests(unittest.TestCase):
    """Hand-written snapshot redaction tests without browser (M16/M20 + leak coverage)."""

    def test_allowed_attributes_kept(self):
        # M16: guide_walk.py:3560 _ALLOWED_ATTRS filter
        snapshot = '- button "Close" [level=2] [checked] [expanded=true] [disabled]'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn('[level=2]', result)
        self.assertIn('[checked]', result)
        self.assertIn('[expanded=true]', result)
        self.assertIn('[disabled]', result)

    def test_disallowed_attributes_dropped(self):
        # M16: attributes not in _ALLOWED_ATTRS are dropped, allowed ones kept
        snapshot = '- button "Close" [checked] [ref=e5] [cursor=pointer]'
        result = guide_walk._redact_aria_snapshot(snapshot)
        # Allowed attribute must be kept
        self.assertIn('[checked]', result)
        # Disallowed attributes must be dropped
        self.assertNotIn('[ref=', result)
        self.assertNotIn('[cursor=', result)
        self.assertIn('button', result)
        self.assertIn('"Close"', result)

    def test_unknown_role_becomes_generic(self):
        # M20: guide_walk.py:3546 roles not in _ARIA_ROLES become "<role>"
        snapshot = '- customrole "Jane Roe"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('customrole', result)
        self.assertNotIn('Jane', result)
        self.assertNotIn('Roe', result)
        self.assertIn('<role>', result)

    def test_value_with_colon_redacted(self):
        # M20: values that don't match glyphs become "<text>"
        snapshot = '- paragraph "Sample": Jane Roe'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('Jane', result)
        self.assertNotIn('Roe', result)
        self.assertIn(': <text>', result)

    def test_escaped_quote_in_name_redacted(self):
        # Leak case: escaped quotes should not survive
        snapshot = '- button "He said \\"hello\\""'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('hello', result)
        self.assertIn('"<text>"', result)

    def test_glyph_prefix_value_redacted(self):
        # Leak case: glyph-prefixed values (x followed by text is not a glyph)
        snapshot = '- button "Test": x Delta words'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('Delta', result)
        self.assertNotIn('words', result)
        self.assertIn(': <text>', result)

    def test_url_line_dropped(self):
        # Leak case: /url: lines are dropped entirely
        snapshot = '- link "Home"\n  /url: https://example.com?foo=bar'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('/url:', result)
        self.assertNotIn('https:', result)
        self.assertNotIn('example.com', result)
        self.assertNotIn('foo=bar', result)

    def test_bullet_prefixed_name_redacted(self):
        # Leak case: bullet-prefixed names
        snapshot = '- button "• Jane Roe"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('Jane', result)
        self.assertNotIn('Roe', result)

    def test_at_sign_email_redacted(self):
        # Leak case: @ email patterns
        snapshot = '- paragraph "@ jane@example.test"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('jane', result)
        self.assertNotIn('example', result)

    def test_cjk_character_redacted(self):
        # Leak case: CJK characters
        snapshot = '- button "李"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('李', result)
        self.assertIn('"<text>"', result)

    def test_non_english_text_redacted(self):
        # Leak case: non-English text
        snapshot = '- heading "Добро пожаловать"\n- paragraph "Bienvenido"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertNotIn('Добро', result)
        self.assertNotIn('пожаловать', result)
        self.assertNotIn('Bienvenido', result)

    def test_dismiss_names_kept_case_insensitive_unit(self):
        # Allowed dismiss names are kept (case-insensitive)
        snapshot = '- button "Close"\n- button "DISMISS"\n- button "got it"'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn('"Close"', result)
        self.assertIn('"DISMISS"', result)
        self.assertIn('"got it"', result)

    def test_allowed_glyphs_kept_unit(self):
        # Allowed glyphs are kept as exact values
        snapshot = '- button "Close": ×\n- button "Test": ✕\n- button "Sample": x'
        result = guide_walk._redact_aria_snapshot(snapshot)
        self.assertIn(': ×', result)
        self.assertIn(': ✕', result)
        # Single "x" is an allowed glyph
        self.assertIn(': x', result)


if __name__ == "__main__":
    unittest.main()
