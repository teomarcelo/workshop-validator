"""Tests for profile panel detection when pre-click baseline is missing."""
import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.guide_walk import _PANEL_JS, _PANEL_SELECTOR, _profile_panels


# Scenario (a): Panel opens in iframe, contains Settings + Log Out, Settings also outside
HTML_PANEL_IN_IFRAME = '''
<button id="avatar">View profile</button>
<div id="outside-settings">Settings</div>
<div id="result"></div>
<script>
document.querySelector('#avatar').addEventListener('click', () => {
    const iframe = document.createElement('iframe');
    iframe.id = 'profile-frame';
    document.body.appendChild(iframe);
    const doc = iframe.contentDocument;
    doc.open();
    doc.write(`
        <div id="panel" style="width: 200px; height: 150px;">
            <a href="#settings" id="settings-link">Settings</a>
            <a href="#logout" id="logout-link">Log Out</a>
        </div>
    `);
    doc.close();
});
</script>
'''

# Scenario (b): Panel in iframe, no Log Out -> should not find
HTML_PANEL_NO_LOGOUT = '''
<button id="avatar">View profile</button>
<div id="result"></div>
<script>
document.querySelector('#avatar').addEventListener('click', () => {
    const iframe = document.createElement('iframe');
    iframe.id = 'profile-frame';
    document.body.appendChild(iframe);
    const doc = iframe.contentDocument;
    doc.open();
    doc.write(`
        <div id="panel" style="width: 200px; height: 150px;">
            <a href="#settings" id="settings-link">Settings</a>
        </div>
    `);
    doc.close();
});
</script>
'''

# Scenario (c): Baseline present (main frame) -> unchanged behavior
HTML_PANEL_WITH_BASELINE = '''
<div id="page-wrapper">
    <div id="outside">Outside content</div>
    <div id="panel" style="width: 200px; height: 150px;">
        <a href="#settings" id="settings-link">Settings</a>
        <a href="#logout" id="logout-link">Log Out</a>
    </div>
    <div id="result"></div>
</div>
<script>
// Baseline: mark the wrapper that existed before the panel appeared
window.__wvShownBeforeProfile = new Set();
window.__wvShownBeforeProfile.add(document.querySelector('#page-wrapper'));
</script>
'''

# Scenario (d): Smallest container also contains global nav -> not found
HTML_PANEL_WITH_GLOBAL_NAV = '''
<div id="combined-container" style="width: 800px; height: 600px;">
    <header role="banner" id="global-header">Global Header</header>
    <a href="#settings" id="settings-link">Settings</a>
    <a href="#logout" id="logout-link">Log Out</a>
</div>
<div id="result"></div>
'''


# Scenario (e): item name holds quote, double quote, backslash and newline.
# Text is set by the test at runtime; a near-miss copy sits outside the panel.
HTML_SPECIAL_NAME = '''
<div id="page-wrapper" style="width: 800px; height: 600px;">
    <div id="near-item" style="width: 100px; height: 20px;"></div>
    <div id="panel" style="width: 200px; height: 150px;">
        <span id="exact-item" style="display: block;"></span>
        <a href="#logout" id="logout-link">Log Out</a>
    </div>
</div>
'''


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1", "Browser smoke only")
class TestProfilePanelNoBaseline(unittest.TestCase):
    """Test _panel_js logic for finding panels without pre-click baseline."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get('CHROMIUM_PATH') or None
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_panel_in_iframe_without_baseline(self):
        """Panel opens in iframe with no baseline, Settings exists outside -> panel's Settings is clicked."""
        page = self.browser.new_page()
        page.set_content(HTML_PANEL_IN_IFRAME)

        # Click avatar to open panel in iframe
        page.click('#avatar')
        page.wait_for_selector('iframe#profile-frame')

        # Get iframe and find Log Out element
        iframe = page.frame('profile-frame')
        logout = iframe.locator('a:has-text("Log Out")').first

        # Run _PANEL_JS on the Log Out element with "Settings" as the item
        result = logout.evaluate(_PANEL_JS, 'Settings')

        self.assertTrue(result, "Should find panel in iframe without baseline")

        # Verify the panel was marked
        panel = iframe.locator('[data-wv-profile-panel="1"]')
        self.assertEqual(panel.count(), 1, "Should mark exactly one panel")

        # Verify Settings is inside the marked panel
        settings_in_panel = panel.locator('a:has-text("Settings")')
        self.assertEqual(settings_in_panel.count(), 1, "Settings should be in the marked panel")

        page.close()

    def test_panel_without_logout_not_found(self):
        """Panel in iframe with Settings but no Log Out -> not found."""
        page = self.browser.new_page()
        page.set_content(HTML_PANEL_NO_LOGOUT)

        # Click avatar to open panel
        page.click('#avatar')
        page.wait_for_selector('iframe#profile-frame')

        iframe = page.frame('profile-frame')
        logout = iframe.locator('a:has-text("Log Out")')
        self.assertEqual(logout.count(), 0, "Log Out should not exist")

        # Run the real panel lookup over the page and its frames
        self.assertEqual(_profile_panels(page), [], "No panel without Log Out")
        self.assertEqual(iframe.locator(_PANEL_SELECTOR).count(), 0)
        self.assertEqual(page.locator(_PANEL_SELECTOR).count(), 0)

        page.close()

    def test_panel_with_baseline_unchanged_behavior(self):
        """Baseline present (panel in main frame) -> unchanged behavior, panel's Settings clicked."""
        page = self.browser.new_page()
        page.set_content(HTML_PANEL_WITH_BASELINE)

        # Find Log Out element
        logout = page.locator('a:has-text("Log Out")').first

        # Run _PANEL_JS with baseline
        result = logout.evaluate(_PANEL_JS, 'Settings')

        self.assertTrue(result, "Should find panel with baseline")

        # Verify the panel was marked
        panel = page.locator('[data-wv-profile-panel="1"]')
        self.assertEqual(panel.count(), 1, "Should mark exactly one panel")

        page.close()

    def test_panel_with_global_nav_rejected(self):
        """Container holding both Settings and Log Out also contains global header -> not found."""
        page = self.browser.new_page()
        page.set_content(HTML_PANEL_WITH_GLOBAL_NAV)

        # Find Log Out element
        logout = page.locator('a:has-text("Log Out")').first

        # Run _PANEL_JS
        result = logout.evaluate(_PANEL_JS, 'Settings')

        self.assertFalse(result, "Should reject container with global nav")

        # Verify no panel was marked
        panel = page.locator('[data-wv-profile-panel="1"]')
        self.assertEqual(panel.count(), 0, "Should not mark any panel")

        page.close()

    def test_profile_panels_finds_iframe_panel(self):
        """Control for the not-found case: the same lookup finds the panel when Log Out is there."""
        page = self.browser.new_page()
        page.set_content(HTML_PANEL_IN_IFRAME)
        page.click('#avatar')
        page.wait_for_selector('iframe#profile-frame')

        self.assertEqual(len(_profile_panels(page)), 1)
        iframe = page.frame('profile-frame')
        self.assertEqual(iframe.locator(_PANEL_SELECTOR).count(), 1)
        self.assertEqual(iframe.locator('#panel[data-wv-profile-panel="1"]').count(), 1)
        page.close()

    def test_item_name_with_special_characters_matched_exactly(self):
        """Quote, double quote, backslash and newline in the name are matched exactly."""
        name = "Alpha's \"Beta\" \\ Gamma\nDelta"
        page = self.browser.new_page()
        page.set_content(HTML_SPECIAL_NAME)
        page.evaluate(
            """(name) => {
                document.querySelector('#exact-item').textContent = name;
                document.querySelector('#near-item').textContent = name.slice(0, -1);
            }""",
            name,
        )
        logout = page.locator('#logout-link')

        result = logout.evaluate(_PANEL_JS, name)

        self.assertTrue(result, "Exact special-character name should be found")
        self.assertEqual(page.locator('#panel[data-wv-profile-panel="1"]').count(), 1)
        self.assertEqual(page.locator(_PANEL_SELECTOR).count(), 1)

        # A name that differs by one character finds nothing and does not throw
        page.locator(_PANEL_SELECTOR).evaluate("(n) => n.removeAttribute('data-wv-profile-panel')")
        self.assertFalse(logout.evaluate(_PANEL_JS, name + "x"))
        self.assertEqual(page.locator(_PANEL_SELECTOR).count(), 0)
        page.close()


if __name__ == '__main__':
    unittest.main()
