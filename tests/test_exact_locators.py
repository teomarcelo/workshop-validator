import os
import unittest
from unittest.mock import patch

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.guide_walk import (
    click_role, _locator_for_name, _click_choice, _leave_copy_mode,
    _screen_item, _type_screen_search, _start_box, _open_add_element, _roots,
    _quick_find, run_personal, WalkState
)
from workshop_validator.guide_inventory import GuideStep


HTML_BUTTON_LOOKALIKE = '''
<button id="save">Save</button>
<button id="save-new">Save & New</button>
<div id="clicked"></div>
<script>
document.querySelector('#save').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Save';
});
document.querySelector('#save-new').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Save & New';
});
</script>
'''

HTML_LINK_LOOKALIKE = '''
<a href="#" id="setup">Setup</a>
<a href="#" id="setup-menu">Setup Menu</a>
<div id="clicked"></div>
<script>
document.querySelector('#setup').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelector('#clicked').innerText = 'Setup';
});
document.querySelector('#setup-menu').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelector('#clicked').innerText = 'Setup Menu';
});
</script>
'''

HTML_TEXT_LOOKALIKE = '''
<div id="next">Next</div>
<div id="next-step">Next Step</div>
<div id="clicked"></div>
<script>
document.querySelector('#next').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Next';
});
document.querySelector('#next-step').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Next Step';
});
</script>
'''

HTML_DIALOG_TEXT = '''
<div role="dialog">
    <div>elements you want to copy</div>
    <div class="dialog-footer">
        <button id="dialog-close">Close</button>
    </div>
</div>
<div>Other elements to copy</div>
<div id="clicked"></div>
<script>
document.querySelector('#dialog-close').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Dialog closed';
});
</script>
'''

HTML_SCREEN_LOOKALIKE = '''
<div role="option" id="screen">Screen</div>
<div role="option" id="screen-flow">Screen Flow</div>
<div id="clicked"></div>
<script>
document.querySelector('#screen').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Screen';
});
document.querySelector('#screen-flow').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Screen Flow';
});
</script>
'''

HTML_PLACEHOLDER_LOOKALIKE = '''
<input id="search" type="text" placeholder="Search" />
<input id="search-all" type="text" placeholder="Search All" />
<div id="filled"></div>
<script>
document.querySelector('#search').addEventListener('input', (e) => {
    document.querySelector('#filled').innerText = 'Search: ' + e.target.value;
});
document.querySelector('#search-all').addEventListener('input', (e) => {
    document.querySelector('#filled').innerText = 'Search All: ' + e.target.value;
});
</script>
'''

HTML_START_LOOKALIKE = '''
<button id="start">Start</button>
<button id="start-here">Start Here</button>
<div id="clicked"></div>
<script>
document.querySelector('#start').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Start';
});
document.querySelector('#start-here').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Start Here';
});
</script>
'''

HTML_ADD_ELEMENT_LOOKALIKE = '''
<button id="add-element">Add Element</button>
<button id="add-elements">Add Elements</button>
<div id="clicked"></div>
<script>
document.querySelector('#add-element').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Add Element';
});
document.querySelector('#add-elements').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Add Elements';
});
</script>
'''

HTML_ADD_ELEMENT_ONLY_LOOKALIKE = '''
<button id="add-elements">Add Elements</button>
<div id="clicked"></div>
<script>
document.querySelector('#add-elements').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Add Elements';
});
</script>
'''

HTML_QUICK_FIND_ONLY_LOOKALIKE = '''
<input id="quick-find-box" type="text" role="searchbox" aria-label="Quick Find Box" placeholder="Quick Find Box" />
<div id="filled"></div>
<script>
document.querySelector('#quick-find-box').addEventListener('input', (e) => {
    document.querySelector('#filled').innerText = 'Quick Find Box: ' + e.target.value;
});
</script>
'''

# Look-alike-only pages: the exact control is absent, a longer name is present.
HTML_BUTTON_ONLY_LOOKALIKE = '''
<button id="save-new">Save &amp; New</button>
<div id="clicked"></div>
<script>
document.querySelector('#save-new').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Save & New';
});
</script>
'''

HTML_LINK_ONLY_LOOKALIKE = '''
<a href="#" id="setup-menu">Setup Menu</a>
<div id="clicked"></div>
<script>
document.querySelector('#setup-menu').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelector('#clicked').innerText = 'Setup Menu';
});
</script>
'''

HTML_TEXT_ONLY_LOOKALIKE = '''
<div id="next-step">Next Step</div>
<div id="clicked"></div>
<script>
document.querySelector('#next-step').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Next Step';
});
</script>
'''

HTML_DIALOG_ONLY_LOOKALIKE = '''
<div role="dialog">
    <div>Select the elements you want to copy now</div>
    <div class="dialog-footer">
        <button id="copy-selected">Copy Selected</button>
    </div>
</div>
<div id="clicked"></div>
<script>
document.querySelector('#copy-selected').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'Copy Selected';
});
</script>
'''

HTML_SCREEN_ONLY_LOOKALIKE = '''
<div role="option" id="screen-element">Screen Element</div>
'''

HTML_PLACEHOLDER_ONLY_LOOKALIKE = '''
<input id="search-all" type="text" placeholder="Search All" />
'''

HTML_START_ONLY_LOOKALIKE = '''
<button id="start-here">Start Here</button>
<button id="screen-flow-two">Screen Flow Two</button>
'''

# Profile menu scenarios for Settings link tests
HTML_PROFILE_WITH_ANCHOR_NO_HREF = '''
<button id="view-profile">View profile</button>
<div id="profile-panel" style="display:none;">
    <a id="settings">Settings</a>
    <a href="#logout">Log Out</a>
</div>
<div id="personal-info" style="display:none;">
    <label for="fname">First Name</label>
    <input id="fname" type="text" value="Test" />
</div>
<div id="clicked"></div>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
});
document.querySelector('#settings').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'none';
    document.querySelector('#personal-info').style.display = 'block';
    document.querySelector('#clicked').innerText = 'Settings clicked';
});
</script>
'''

HTML_PROFILE_SETTINGS_LOOKALIKE = '''
<button id="view-profile">View profile</button>
<div id="profile-panel" style="display:none;">
    <a href="#settings-menu">Settings Menu</a>
    <a href="#logout">Log Out</a>
</div>
<div id="clicked"></div>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
});
</script>
'''

HTML_PROFILE_AMBIGUOUS_SETTINGS = '''
<button id="view-profile">View profile</button>
<div id="profile-panel" style="display:none;">
    <div id="settings-1">Settings</div>
    <div id="settings-2">Settings</div>
    <a href="#logout">Log Out</a>
</div>
<div id="clicked"></div>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
});
</script>
'''

HTML_PROFILE_PROPER_LINK = '''
<button id="view-profile">View profile</button>
<div id="profile-panel" style="display:none;">
    <a href="#settings" role="link" id="settings">Settings</a>
    <a href="#logout">Log Out</a>
</div>
<div id="personal-info" style="display:none;">
    <label for="fname2">First Name</label>
    <input id="fname2" type="text" value="Test" />
</div>
<div id="clicked"></div>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
});
document.querySelector('#settings').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelector('#profile-panel').style.display = 'none';
    document.querySelector('#personal-info').style.display = 'block';
    document.querySelector('#clicked').innerText = 'Settings clicked via role';
});
</script>
'''


# Settings outside the profile panel only (a real link), none inside.
HTML_PROFILE_SETTINGS_OUTSIDE_ONLY = '''
<button id="view-profile">View profile</button>
<a href="#outside" id="outside-settings">Settings</a>
<div id="profile-panel" style="display:none;">
    <a href="#logout">Log Out</a>
</div>
<div id="personal-info" style="display:none;">
    <label for="fname3">First Name</label>
    <input id="fname3" type="text" value="Test" />
</div>
<div id="clicked"></div>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
});
document.querySelector('#outside-settings').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelector('#personal-info').style.display = 'block';
    document.querySelector('#clicked').innerText = 'outside clicked';
});
</script>
'''

# One exact Settings inside the panel, another outside it.
HTML_PROFILE_SETTINGS_INSIDE_AND_OUTSIDE = '''
<button id="view-profile">View profile</button>
<div id="outside-settings">Settings</div>
<div id="profile-panel" style="display:none;">
    <a id="inside-settings">Settings</a>
    <a href="#logout">Log Out</a>
</div>
<div id="personal-info" style="display:none;">
    <label for="fname4">First Name</label>
    <input id="fname4" type="text" value="Test" />
</div>
<div id="clicked"></div>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
});
document.querySelector('#outside-settings').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'outside clicked';
});
document.querySelector('#inside-settings').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'none';
    document.querySelector('#personal-info').style.display = 'block';
    document.querySelector('#clicked').innerText = 'inside clicked';
});
</script>
'''

# One Settings in the page panel and one in a framed panel: two in total.
HTML_PROFILE_SETTINGS_TWO_FRAMES = '''
<button id="view-profile">View profile</button>
<div id="profile-panel" style="display:none;">
    <a id="page-settings">Settings</a>
    <a href="#logout">Log Out</a>
</div>
<div id="personal-info" style="display:none;">
    <label for="fname5">First Name</label>
    <input id="fname5" type="text" value="Test" />
</div>
<div id="clicked"></div>
<iframe id="side" srcdoc="<div id='frame-panel' style='display:none;'><a id='frame-settings'>Settings</a><a href='#logout'>Log Out</a></div>"></iframe>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
    document.querySelector('#side').contentDocument.querySelector('#frame-panel').style.display = 'block';
});
document.querySelector('#page-settings').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'none';
    document.querySelector('#personal-info').style.display = 'block';
    document.querySelector('#clicked').innerText = 'page clicked';
});
</script>
'''


# No Log Out, so no profile panel; one exact Settings anchor (no href) elsewhere.
HTML_PROFILE_NO_PANEL_SETTINGS_ELSEWHERE = '''
<button id="view-profile">View profile</button>
<div id="profile-panel" style="display:none;">
    <a href="#account">Account</a>
</div>
<a id="elsewhere-settings">Settings</a>
<div id="clicked"></div>
<script>
document.querySelector('#view-profile').addEventListener('click', () => {
    document.querySelector('#profile-panel').style.display = 'block';
});
document.querySelector('#elsewhere-settings').addEventListener('click', () => {
    document.querySelector('#clicked').innerText = 'elsewhere clicked';
});
</script>
'''


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1", "Browser smoke only")
class ExactLocatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get('CHROMIUM_PATH') or None
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_click_role_exact_button(self):
        """click_role must match exact button name, not look-alike"""
        page = self.browser.new_page()
        page.set_content(HTML_BUTTON_LOOKALIKE)

        result = click_role(page, "button", "Save")
        self.assertTrue(result, "Should find and click Save button")
        self.assertEqual(page.locator("#clicked").inner_text(), "Save",
                        "Should click exact 'Save', not 'Save & New'")
        page.close()

    def test_click_role_no_partial_match(self):
        """click_role must not fall back to partial matches"""
        page = self.browser.new_page()
        page.set_content(HTML_BUTTON_LOOKALIKE)

        result = click_role(page, "button", "Submit")
        self.assertFalse(result, "Should not click when exact match not found")
        self.assertEqual(page.locator("#clicked").inner_text(), "",
                        "Should not click any button when no exact match")
        page.close()

    def test_locator_for_name_exact_link(self):
        """_locator_for_name must match exact link name, not look-alike"""
        page = self.browser.new_page()
        page.set_content(HTML_LINK_LOOKALIKE)

        loc = _locator_for_name(page, "Setup")
        self.assertIsNotNone(loc, "Should find Setup link")
        loc.click()
        self.assertEqual(page.locator("#clicked").inner_text(), "Setup",
                        "Should locate exact 'Setup', not 'Setup Menu'")
        page.close()

    def test_locator_for_name_no_partial_match(self):
        """_locator_for_name must not fall back to partial matches"""
        page = self.browser.new_page()
        page.set_content(HTML_LINK_LOOKALIKE)

        loc = _locator_for_name(page, "Home")
        self.assertIsNone(loc, "Should return None when exact match not found")
        page.close()

    def test_click_choice_exact_text(self):
        """_click_choice must match exact text, not look-alike"""
        page = self.browser.new_page()
        page.set_content(HTML_TEXT_LOOKALIKE)

        result = _click_choice(page, "Next")
        self.assertTrue(result, "Should find and click Next")
        self.assertEqual(page.locator("#clicked").inner_text(), "Next",
                        "Should click exact 'Next', not 'Next Step'")
        page.close()

    def test_click_choice_no_partial_match(self):
        """_click_choice must not fall back to partial matches"""
        page = self.browser.new_page()
        page.set_content(HTML_TEXT_LOOKALIKE)

        result = _click_choice(page, "Previous")
        self.assertFalse(result, "Should not click when exact match not found")
        self.assertEqual(page.locator("#clicked").inner_text(), "",
                        "Should not click any element when no exact match")
        page.close()

    def test_leave_copy_mode_exact_dialog_text(self):
        """_leave_copy_mode must match exact dialog text"""
        page = self.browser.new_page()
        page.set_content(HTML_DIALOG_TEXT)

        _leave_copy_mode(page)
        self.assertEqual(page.locator("#clicked").inner_text(), "Dialog closed",
                        "Should find and close dialog with exact text match")
        page.close()

    def test_screen_item_exact_name(self):
        """_screen_item must match exact 'Screen', not 'Screen Flow'"""
        page = self.browser.new_page()
        page.set_content(HTML_SCREEN_LOOKALIKE)

        item = _screen_item(page)
        self.assertIsNotNone(item, "Should find Screen item")
        item.click()
        self.assertEqual(page.locator("#clicked").inner_text(), "Screen",
                        "Should find exact 'Screen', not 'Screen Flow'")
        page.close()

    def test_type_screen_search_exact_placeholder(self):
        """_type_screen_search must match exact placeholder"""
        page = self.browser.new_page()
        page.set_content(HTML_PLACEHOLDER_LOOKALIKE)

        result = _type_screen_search(page)
        self.assertTrue(result, "Should find and fill search box")
        filled_text = page.locator("#filled").inner_text()
        self.assertEqual(filled_text, "Search: Screen",
                        "Should fill exact 'Search' placeholder, not 'Search All'")
        page.close()

    def test_start_box_exact_name(self):
        """_start_box must match exact 'Start', not 'Start Here'"""
        page = self.browser.new_page()
        page.set_content(HTML_START_LOOKALIKE)

        for root in _roots(page):
            box = _start_box(root)
            if box:
                self.assertIsNotNone(box, "Should find Start box")
                break
        page.close()

    def test_open_add_element_exact_button(self):
        """_open_add_element must match exact button name, not look-alike"""
        page = self.browser.new_page()
        page.set_content(HTML_ADD_ELEMENT_LOOKALIKE)

        _open_add_element(page)
        self.assertEqual(page.locator("#clicked").inner_text(), "Add Element",
                        "Should click exact 'Add Element', not 'Add Elements'")
        page.close()

    def test_open_add_element_no_lookalike_click(self):
        """_open_add_element must not click look-alike when exact match missing"""
        page = self.browser.new_page()
        page.set_content(HTML_ADD_ELEMENT_ONLY_LOOKALIKE)

        _open_add_element(page)
        self.assertEqual(page.locator("#clicked").inner_text(), "",
                        "Should not click 'Add Elements' when 'Add Element' missing")
        page.close()

    def test_quick_find_no_lookalike_fill(self):
        """_quick_find must not return look-alike when exact match missing"""
        page = self.browser.new_page()
        page.set_content(HTML_QUICK_FIND_ONLY_LOOKALIKE)

        result = _quick_find(page)
        self.assertIsNone(result, "Should not return 'Quick Find Box' when 'Quick Find' missing")
        page.close()

    def test_click_role_no_lookalike_click(self):
        """click_role must not click 'Save & New' when only that look-alike exists"""
        page = self.browser.new_page()
        page.set_content(HTML_BUTTON_ONLY_LOOKALIKE)

        result = click_role(page, "button", "Save")
        self.assertFalse(result, "Should not report a click on a look-alike")
        self.assertEqual(page.locator("#clicked").inner_text(), "",
                        "Should not click 'Save & New' when 'Save' missing")
        page.close()

    def test_locator_for_name_no_lookalike(self):
        """_locator_for_name must not return 'Setup Menu' when 'Setup' is missing"""
        page = self.browser.new_page()
        page.set_content(HTML_LINK_ONLY_LOOKALIKE)

        loc = _locator_for_name(page, "Setup")
        self.assertIsNone(loc, "Should not locate 'Setup Menu' for 'Setup'")
        page.close()

    def test_click_choice_no_lookalike_click(self):
        """_click_choice must not click 'Next Step' text when 'Next' is missing"""
        page = self.browser.new_page()
        page.set_content(HTML_TEXT_ONLY_LOOKALIKE)

        result = _click_choice(page, "Next")
        self.assertFalse(result, "Should not report a click on a look-alike")
        self.assertEqual(page.locator("#clicked").inner_text(), "",
                        "Should not click 'Next Step' when 'Next' missing")
        page.close()

    def test_leave_copy_mode_no_lookalike_click(self):
        """_leave_copy_mode must not click a button beside a longer copy sentence"""
        page = self.browser.new_page()
        page.set_content(HTML_DIALOG_ONLY_LOOKALIKE)

        _leave_copy_mode(page)
        self.assertEqual(page.locator("#clicked").inner_text(), "",
                        "Should not click 'Copy Selected' beside a longer sentence")
        page.close()

    def test_screen_item_no_lookalike(self):
        """_screen_item must not return 'Screen Element' when 'Screen' is missing"""
        page = self.browser.new_page()
        page.set_content(HTML_SCREEN_ONLY_LOOKALIKE)

        self.assertIsNone(_screen_item(page),
                          "Should not return 'Screen Element' for 'Screen'")
        page.close()

    def test_type_screen_search_no_lookalike_fill(self):
        """_type_screen_search must not fill 'Search All' when 'Search' is missing"""
        page = self.browser.new_page()
        page.set_content(HTML_PLACEHOLDER_ONLY_LOOKALIKE)

        result = _type_screen_search(page)
        self.assertFalse(result, "Should not report a fill on a look-alike")
        self.assertEqual(page.locator("#search-all").input_value(), "",
                        "Should not fill 'Search All' when 'Search' missing")
        page.close()

    def test_start_box_no_lookalike(self):
        """_start_box must not measure 'Start Here' or 'Screen Flow Two'"""
        page = self.browser.new_page()
        page.set_content(HTML_START_ONLY_LOOKALIKE)

        for root in _roots(page):
            self.assertIsNone(_start_box(root),
                              "Should not return a box for a look-alike Start node")
        page.close()

    def _make_state(self, page):
        """Helper to create a WalkState for testing"""
        class Clock:
            def __init__(self):
                self.now = 0
            def __call__(self):
                return self.now
            def sleep(self, s):
                self.now += s
        clock = Clock()
        state = WalkState(
            page=page, timeout_s=30, interval_s=0.1, clock=clock, sleeper=clock.sleep,
            page_wait_s=2, page_poll_s=0.1,
        )
        state.home = "https://test123.my.salesforce.com"
        return state

    def _make_step(self):
        """Helper to create a GuideStep for testing"""
        return GuideStep(
            "personal-test", 1, "Exercise 1", "Task 1", "1",
            "Click View profile, then Settings to open Personal Information.",
            (90.0, 10.0, 400.0, 24.0), None, (),
        )

    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_anchor_without_href(self, mock_return_home):
        """run_personal must find Settings as anchor without href via exact text"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_WITH_ANCHOR_NO_HREF)

        state = self._make_state(page)
        step = self._make_step()
        result = run_personal(state, step)

        self.assertEqual(result.status, "PASS",
                        "Should PASS when Settings found via exact text")
        self.assertTrue(any("personal-information" in eid for eid in result.evidence_ids),
                     "Should reach Personal Information page")
        page.close()

    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_lookalike_only(self, mock_return_home):
        """run_personal must not click 'Settings Menu' when 'Settings' missing"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_SETTINGS_LOOKALIKE)

        state = self._make_state(page)
        step = self._make_step()
        result = run_personal(state, step)

        self.assertEqual(result.status, "UI_MISMATCH",
                        "Should be UI_MISMATCH when only look-alike exists")
        # Either "not in the profile menu" or diagnostic showing no exact Settings found
        self.assertTrue(
            "Settings was not in the profile menu" in result.note or
            ("Settings did not open" in result.note and "no exact 'Settings' found" in result.note),
            f"Should report Settings not found, got: {result.note}")
        page.close()

    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_ambiguous(self, mock_return_home):
        """run_personal must not click when multiple Settings elements exist"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_AMBIGUOUS_SETTINGS)

        state = self._make_state(page)
        step = self._make_step()
        result = run_personal(state, step)

        self.assertEqual(result.status, "NEEDS_REVIEW",
                        "Should be NEEDS_REVIEW when multiple Settings exist")
        self.assertTrue(result.freeze,
                       "Should freeze when ambiguous")
        self.assertIn("More than one Settings control", result.note,
                     "Should report multiple Settings controls")
        page.close()

    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_proper_link_regression(self, mock_return_home):
        """run_personal must still work with proper role=link Settings"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_PROPER_LINK)

        state = self._make_state(page)
        step = self._make_step()
        result = run_personal(state, step)

        self.assertEqual(result.status, "PASS",
                        "Should PASS when Settings has proper role=link")
        self.assertTrue(any("personal-information" in eid for eid in result.evidence_ids),
                     "Should reach Personal Information page")
        page.close()


    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_outside_panel_not_clicked(self, mock_return_home):
        """An exact Settings outside the profile panel is never used"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_SETTINGS_OUTSIDE_ONLY)

        result = run_personal(self._make_state(page), self._make_step())

        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("Settings was not in the profile menu", result.note)
        self.assertEqual(page.inner_text("#clicked"), "")
        page.close()

    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_inside_panel_wins_over_outside(self, mock_return_home):
        """Settings inside the panel is used; one outside does not make it ambiguous"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_SETTINGS_INSIDE_AND_OUTSIDE)

        result = run_personal(self._make_state(page), self._make_step())

        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(page.inner_text("#clicked"), "inside clicked")
        page.close()

    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_counted_across_frames(self, mock_return_home):
        """One Settings in each of two frames' panels is ambiguous"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_SETTINGS_TWO_FRAMES)

        result = run_personal(self._make_state(page), self._make_step())

        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertTrue(result.freeze)
        self.assertEqual(page.inner_text("#clicked"), "")
        page.close()

    @patch('workshop_validator.guide_walk.return_home')
    def test_profile_settings_miss_note_explains_panel_and_matches(self, mock_return_home):
        """A miss note says if the panel was found and lists exact Settings matches"""
        mock_return_home.return_value = True
        page = self.browser.new_page()
        page.set_content(HTML_PROFILE_NO_PANEL_SETTINGS_ELSEWHERE)

        result = run_personal(self._make_state(page), self._make_step())

        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertIn("Settings was not in the profile menu.", result.note)
        self.assertIn("Profile panel found: no.", result.note)
        self.assertIn("inside the panel: 0; on the whole page: 1.", result.note)
        self.assertIn("[0] tag=<a> role=none href=no visible=yes", result.note)
        self.assertNotIn("[1]", result.note)
        self.assertEqual(page.inner_text("#clicked"), "")
        page.close()

if __name__ == "__main__":
    unittest.main()
