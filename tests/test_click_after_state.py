"""Test click after-state evidence requirements."""

import unittest
from types import SimpleNamespace
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import _act_click, WalkState


def _step(text):
    return GuideStep(
        "test-1", 1, "Exercise 1", "Task 1", "1", text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


class MockPage:
    def __init__(self, url="https://example.lightning.force.com/page"):
        self.url = url
        self.body = ""
        self.roles = set()  # For backward compatibility, keep as set for simple checks
        self._roles_list = []  # List for counting duplicates
        self.aria_states = {}
        self.frames = [self]
        self._click_callbacks = {}

    def locator(self, selector):
        # Support role locators for counting
        import re
        role_match = re.search(r"\[role='(\w+)'\]", selector)
        if role_match:
            role = role_match.group(1)
            return _MockMultiLoc(self, role)
        return MockLoc(self)

    def get_by_role(self, role, name=None, exact=True):
        on_click = self._click_callbacks.get((role, name))
        if (role, name) in self.roles:
            return MockLoc(self, role, name, exists=True, on_click=on_click)
        return MockLoc(self, role, name, exists=False, on_click=on_click)

    def title(self):
        return getattr(self, "_title", "")


class _MockMultiLoc:
    """Locator that can return multiple items for a role."""
    def __init__(self, page, role):
        self.page = page
        self.role = role

    def count(self):
        return len([r for r in self.page._roles_list if r[0] == self.role])

    def nth(self, index):
        items = [r for r in self.page._roles_list if r[0] == self.role]
        if 0 <= index < len(items):
            return MockLoc(self.page, items[index][0], items[index][1], exists=True)
        return MockLoc(self.page, self.role, "", exists=False)


class MockLoc:
    def __init__(self, page, role="", name="", exists=True, on_click=None):
        self.page = page
        self.role = role
        self.name = name
        self.exists = exists
        self.on_click = on_click

    def count(self):
        return 1 if self.exists else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.exists

    def click(self, timeout=None):
        if self.on_click:
            self.on_click()

    def get_attribute(self, attr):
        return self.page.aria_states.get((self.role, self.name, attr))

    def inner_text(self, timeout=None):
        return self.name if self.name else ""

    def nth(self, index):
        return self


class TestClickAfterState(unittest.TestCase):
    def test_error_page_url_change_returns_needs_review(self):
        """URL change to an error page is not evidence."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "Submit"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/error"
            page.body = "Error: Insufficient privileges to perform this action"

        page._click_callbacks[("button", "Submit")] = on_click

        state = WalkState(page=page)
        step = _step("Click Submit.")

        result = _act_click(state, step, "Submit")

        # Should be NEEDS_REVIEW because error page is not valid evidence
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_unrelated_same_host_url_change_without_target_returns_needs_review(self):
        """URL change on same host without named target in URL or heading is not evidence."""
        page = MockPage("https://example.lightning.force.com/page1")
        page.roles.add(("button", "Next"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/unrelated"
            page.body = "Some page content"
            page._title = "Unrelated Page"

        page._click_callbacks[("button", "Next")] = on_click

        state = WalkState(page=page)
        step = _step("Click Next.")

        result = _act_click(state, step, "Next")

        # Should be NEEDS_REVIEW because URL and heading don't contain "next"
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_click_save_to_list_view_without_target_returns_needs_review(self):
        """Click Save → same-host /list-view (no target in URL/heading) → NEEDS_REVIEW."""
        page = MockPage("https://example.lightning.force.com/edit")
        page.roles.add(("button", "Save"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/list-view"
            page.body = "List View"
            page._title = "All Items"

        page._click_callbacks[("button", "Save")] = on_click

        state = WalkState(page=page)
        step = _step("Click Save.")

        result = _act_click(state, step, "Save")

        # Should be NEEDS_REVIEW because "save" is not in URL or heading
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_click_setup_to_setup_url_passes_with_suspect_flag(self):
        """Click Setup → /lightning/setup/... → PASS with url-only-evidence flag."""
        page = MockPage("https://example.lightning.force.com/home")
        page.roles.add(("button", "Setup"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/lightning/setup/SetupOneHome/home"
            page.body = "Setup Home"
            page._title = "Setup Home"

        page._click_callbacks[("button", "Setup")] = on_click

        state = WalkState(page=page)
        step = _step("Click Setup.")

        result = _act_click(state, step, "Setup")

        # Should PASS because "setup" is in URL
        self.assertEqual(result.status, "PASS")
        self.assertIn("url-only-evidence", result.suspect)

    def test_target_in_heading_passes(self):
        """Target only in the new heading → PASS."""
        page = MockPage("https://example.lightning.force.com/page1")
        page.roles.add(("button", "Accounts"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/page2"
            page.body = "Account list"
            page._title = "Accounts Home"

        page._click_callbacks[("button", "Accounts")] = on_click

        state = WalkState(page=page)
        step = _step("Click Accounts.")

        result = _act_click(state, step, "Accounts")

        # Should PASS because "accounts" is in heading
        self.assertEqual(result.status, "PASS")
        self.assertIn("url-only-evidence", result.suspect)

    def test_query_param_only_change_returns_needs_review(self):
        """Query-param-only change without target → NEEDS_REVIEW."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "Filter"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/page?view=compact"
            page.body = "Filtered view"
            page._title = "Same Page"

        page._click_callbacks[("button", "Filter")] = on_click

        state = WalkState(page=page)
        step = _step("Click Filter.")

        result = _act_click(state, step, "Filter")

        # Should be NEEDS_REVIEW because "filter" is not in URL or heading
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_error_page_with_target_in_url_returns_needs_review(self):
        """Error page with target in URL → NEEDS_REVIEW (error overrides target match)."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "Submit"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/submit/error"
            page.body = "Error: Access denied to this resource"
            page._title = "Error Page"

        page._click_callbacks[("button", "Submit")] = on_click

        state = WalkState(page=page)
        step = _step("Click Submit.")

        result = _act_click(state, step, "Submit")

        # Should be NEEDS_REVIEW because error indicator trumps URL match
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_sales_with_salesforce_title_returns_needs_review(self):
        """Click Sales with title 'Home | Salesforce' → NEEDS_REVIEW (ignores 'salesforce' token)."""
        page = MockPage("https://example.lightning.force.com/home")
        page.roles.add(("button", "Sales"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/home"
            page.body = "Home"
            page._title = "Home | Salesforce"

        page._click_callbacks[("button", "Sales")] = on_click

        state = WalkState(page=page)
        step = _step("Click Sales.")

        result = _act_click(state, step, "Sales")

        # Should be NEEDS_REVIEW because "salesforce" is ignored and "sales" is not in title
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_ok_short_target_to_checkout_returns_needs_review(self):
        """Click OK → /checkout → NEEDS_REVIEW (targets < 3 chars never use URL evidence)."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "OK"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/checkout"
            page.body = "Checkout page"
            page._title = "Checkout"

        page._click_callbacks[("button", "OK")] = on_click

        state = WalkState(page=page)
        step = _step("Click OK.")

        result = _act_click(state, step, "OK")

        # Should be NEEDS_REVIEW because "ok" is < 3 chars
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_new_to_renewal_returns_needs_review(self):
        """Click New → /renewal → NEEDS_REVIEW (substring not whole token match)."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "New"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/renewal"
            page.body = "Renewal page"
            page._title = "Renewal"

        page._click_callbacks[("button", "New")] = on_click

        state = WalkState(page=page)
        step = _step("Click New.")

        result = _act_click(state, step, "New")

        # Should be NEEDS_REVIEW because "new" is substring of "renewal", not whole token
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_sales_with_sales_in_title_passes(self):
        """Click Sales with title 'Home | Sales | Salesforce' → PASS + url-only-evidence."""
        page = MockPage("https://example.lightning.force.com/home")
        page.roles.add(("button", "Sales"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/sales/home"
            page.body = "Sales home"
            page._title = "Home | Sales | Salesforce"

        page._click_callbacks[("button", "Sales")] = on_click

        state = WalkState(page=page)
        step = _step("Click Sales.")

        result = _act_click(state, step, "Sales")

        # Should PASS because "sales" is a whole token in title and URL
        self.assertEqual(result.status, "PASS")
        self.assertIn("url-only-evidence", result.suspect)

    def test_generic_word_newly_appearing_is_not_evidence(self):
        """Generic word newly appearing ('bottom') is NOT evidence → NEEDS_REVIEW."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "Save"))
        page._title = ""

        def on_click():
            page.url = "https://example.lightning.force.com/page"
            page.body = "Settings saved"
            page._title = "Settings"
            # Add a control with a generic/position word that's excluded
            page.roles.add(("link", "bottom"))

        page._click_callbacks[("button", "Save")] = on_click

        state = WalkState(page=page)
        step = _step("Click Save at the bottom.")

        result = _act_click(state, step, "Save")

        # Should be NEEDS_REVIEW because "bottom" is excluded from target_names
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)

    def test_target_already_in_palette_and_newly_in_canvas_passes(self):
        """Target already in palette and newly in canvas → PASS (occurrence count increased)."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "Name"))
        page._roles_list.append(("button", "Name"))
        page._title = ""

        def on_click():
            # Name appears second time as textbox (already exists as button)
            page.roles.add(("textbox", "Name"))
            page._roles_list.append(("textbox", "Name"))

        page._click_callbacks[("button", "Name")] = on_click

        state = WalkState(page=page)
        step = _step("Click the Name field to place it.")

        result = _act_click(state, step, "Name")

        # Should PASS because "Name" occurrence count increased from 1 to 2
        self.assertEqual(result.status, "PASS")
        # Check evidence contains "appeared"
        self.assertTrue(any("appeared" in eid for eid in result.evidence_ids))

    def test_target_already_visible_nothing_new_in_canvas_returns_needs_review(self):
        """Target already visible and nothing new in canvas → NEEDS_REVIEW."""
        page = MockPage("https://example.lightning.force.com/page")
        page.roles.add(("button", "Name"))
        page._title = ""

        def on_click():
            # Click doesn't add anything new
            pass

        page._click_callbacks[("button", "Name")] = on_click

        state = WalkState(page=page)
        step = _step("Click Name.")

        result = _act_click(state, step, "Name")

        # Should be NEEDS_REVIEW because nothing changed
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("but no change could be read", result.note)


if __name__ == "__main__":
    unittest.main()
