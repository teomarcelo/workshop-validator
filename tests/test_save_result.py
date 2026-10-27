"""Test reading save result messages on Personal Information page."""
import unittest

from workshop_validator.guide_walk import walk_guide
from workshop_validator.guide_inventory import GuideInventory, GuideStep


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


def inventory_of(*items):
    return GuideInventory(1, tuple(items), (), ())


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class Page:
    def __init__(self, url, body="", values=None):
        self.url = url
        self.body = body
        self.roles = set()
        self.values = dict(values or {})
        self.enabled = {}
        self.events = []
        self.page_title = ""
        self.spinning = False
        self.html = ""
        self._status_elements = []
        self._alert_elements = []
        self._frames = None

    @property
    def frames(self):
        if self._frames is not None:
            return self._frames
        return [self]

    @frames.setter
    def frames(self, value):
        self._frames = value

    def title(self):
        return self.page_title

    def locator(self, selector):
        if selector == "body":
            return Loc(self, "body")
        if "spinner" in selector or "progressbar" in selector:
            return Loc(self, "spinner")
        # Handle [role='...'] selectors
        if "[role='status']" in selector:
            return _MultiLoc(self, self._status_elements)
        if "[role='alert']" in selector:
            return _MultiLoc(self, self._alert_elements)
        return Loc(self, "css", selector)

    def get_by_role(self, role, name=None, exact=True):
        return Loc(self, role, name or "")

    def get_by_label(self, name):
        return Loc(self, "label", name)

    def content(self):
        return self.body or ""


class Loc:
    def __init__(self, page, kind, name=""):
        self.page = page
        self.kind = kind
        self.name = name

    def count(self):
        if self.kind == "body":
            return 1
        if self.kind == "spinner":
            return 1 if self.page.spinning else 0
        if self.kind == "label":
            return 1 if self.name in self.page.values else 0
        return 1 if (self.kind, self.name) in self.page.roles else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.count() > 0

    def is_enabled(self):
        return self.page.enabled.get((self.kind, self.name), True)

    def click(self, timeout=None):
        self.page.events.append(("click", self.kind, self.name))
        reveal = getattr(self.page, "on_click", None)
        if reveal is not None:
            reveal(self.kind, self.name)

    def fill(self, value, timeout=None):
        self.page.values[self.name] = value
        self.page.events.append(("fill", self.name, value))

    def input_value(self, timeout=None):
        return self.page.values.get(self.name, "")

    def inner_text(self, timeout=None):
        if self.kind == "body":
            return self.page.body
        return self.name if self.name else ""


class _MultiLoc:
    """Locator that can return multiple elements."""
    def __init__(self, page, elements):
        self.page = page
        self.elements = list(elements)

    def all(self):
        return self.elements

    def count(self):
        return len(self.elements)


class MockElement:
    """Mock element with role and text."""
    def __init__(self, text, visible=True):
        self._text = text
        self._visible = visible

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text


class TestSaveResultMessage(unittest.TestCase):
    def walk(self, page, items):
        clock = Clock()

        def sleep(seconds):
            clock.now += seconds

        return walk_guide(
            page, inventory_of(*items),
            timeout_s=0, interval_s=1, clock=clock,
            sleeper=sleep,
        )

    def test_new_success_banner_after_save_passes(self):
        """New success banner after clicking Save returns PASS."""
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            values={
                "Company Name": "Acme Widgets",
                "Email": "person@example.com",
            },
        )
        page.roles.add(("button", "Save"))

        def reveal(kind, name):
            if name == "Save":
                # Add a new success banner after clicking Save
                elem = MockElement("Your settings have been successfully saved.")
                page._status_elements.append(elem)

        page.on_click = reveal
        item = step(
            "save", "Set Up Sample Environment", "Task 3: Update your user information.", "4",
            "Select Save at the bottom of the screen.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["save"], "PASS")
        self.assertIn("saved", outcome.notes["save"].lower())

    def test_unsaved_changes_text_no_new_message_is_needs_review(self):
        """'Unsaved changes' text on page with no new message is NEEDS_REVIEW."""
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            body="Unsaved changes detected",
            values={
                "Company Name": "Acme Widgets",
                "Email": "person@example.com",
            },
        )
        page.roles.add(("button", "Save"))

        # No new message appears after clicking Save
        page.on_click = lambda kind, name: None

        item = step(
            "save", "Set Up Sample Environment", "Task 3: Update your user information.", "4",
            "Select Save at the bottom of the screen.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["save"], "NEEDS_REVIEW")

    def test_success_banner_already_visible_no_new_message_is_needs_review(self):
        """Success banner visible before click with nothing new is NEEDS_REVIEW."""
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            values={
                "Company Name": "Acme Widgets",
                "Email": "person@example.com",
            },
        )
        page.roles.add(("button", "Save"))

        # Banner already exists before click
        elem = MockElement("Your settings have been successfully saved.")
        page._status_elements.append(elem)

        # No new message appears after clicking Save
        page.on_click = lambda kind, name: None

        item = step(
            "save", "Set Up Sample Environment", "Task 3: Update your user information.", "4",
            "Select Save at the bottom of the screen.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["save"], "NEEDS_REVIEW")

    def test_error_console_link_no_new_alert_not_functional_fail(self):
        """'Error Console' link present with no new alert is not FUNCTIONAL_FAIL."""
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            body="Error Console link present",
            values={
                "Company Name": "Acme Widgets",
                "Email": "person@example.com",
            },
        )
        page.roles.add(("button", "Save"))

        # No new message appears after clicking Save
        page.on_click = lambda kind, name: None

        item = step(
            "save", "Set Up Sample Environment", "Task 3: Update your user information.", "4",
            "Select Save at the bottom of the screen.",
        )
        outcome = self.walk(page, (item,))
        # Should be NEEDS_REVIEW, not FUNCTIONAL_FAIL
        self.assertEqual(outcome.statuses["save"], "NEEDS_REVIEW")

    def test_new_error_alert_is_functional_fail(self):
        """New error alert after clicking Save returns FUNCTIONAL_FAIL."""
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            values={
                "Company Name": "Acme Widgets",
                "Email": "person@example.com",
            },
        )
        page.roles.add(("button", "Save"))

        def reveal(kind, name):
            if name == "Save":
                # Add a new error alert after clicking Save
                elem = MockElement("Error: Unable to save your settings")
                page._alert_elements.append(elem)

        page.on_click = reveal
        item = step(
            "save", "Set Up Sample Environment", "Task 3: Update your user information.", "4",
            "Select Save at the bottom of the screen.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["save"], "FUNCTIONAL_FAIL")
        self.assertIn("error", outcome.notes["save"].lower())


if __name__ == "__main__":
    unittest.main()
