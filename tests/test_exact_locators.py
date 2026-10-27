import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.guide_walk import (
    click_role, _locator_for_name, _click_choice, _leave_copy_mode,
    _screen_item, _type_screen_search, _start_box, _open_add_element, _roots,
    _quick_find
)


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


if __name__ == "__main__":
    unittest.main()
