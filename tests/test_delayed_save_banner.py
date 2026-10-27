"""Test polling for delayed save banners after clicking Save."""
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

    def get_attribute(self, name):
        return None


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


class TestDelayedSaveBanner(unittest.TestCase):
    def walk(self, page, items, timeout_s=90, interval_s=2):
        clock = Clock()

        def sleep(seconds):
            clock.now += seconds

        return walk_guide(
            page, inventory_of(*items),
            timeout_s=timeout_s, interval_s=interval_s, clock=clock,
            sleeper=sleep,
        )

    def test_delayed_banner_after_save_passes(self):
        """Banner appearing 3 seconds after Save click returns PASS after polling."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("button", "Save"))
        page.body = "Settings page"

        clock_ref = {"clock": None}

        def reveal(kind, name):
            if name == "Save":
                # Capture the clock from the page's walk
                if hasattr(page, "_walk_clock"):
                    clock_ref["clock"] = page._walk_clock
                # Banner appears after 3 seconds
                # The sleep function in walk will advance the clock

        page.on_click = reveal

        item = step(
            "save", "Exercise 1", "Task: Save settings", "1",
            "Click Save.",
        )

        # Run the walk with a custom clock that adds the banner after 3 seconds
        clock = Clock()
        banner_added = {"done": False}

        def sleep(seconds):
            clock.now += seconds
            # Add banner after clock reaches 3 seconds
            if clock.now >= 3 and not banner_added["done"]:
                elem = MockElement("Your changes have been successfully saved.")
                page._status_elements.append(elem)
                banner_added["done"] = True

        outcome = walk_guide(
            page, inventory_of(item),
            timeout_s=90, interval_s=2, clock=clock,
            sleeper=sleep,
        )
        self.assertEqual(outcome.statuses["save"], "PASS")
        # Check that evidence includes success message
        evidence = outcome.evidence.get("save", ())
        self.assertTrue(any("after-state" in str(e) and "success" in str(e) for e in evidence),
                        f"Expected after-state success evidence, got: {evidence}")

    def test_instant_banner_after_save_passes(self):
        """Banner appearing instantly after Save click returns PASS without polling."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("button", "Save"))
        page.body = "Settings page"

        def reveal(kind, name):
            if name == "Save":
                # Add banner immediately
                elem = MockElement("Settings were successfully saved.")
                page._status_elements.append(elem)

        page.on_click = reveal

        item = step(
            "save", "Exercise 1", "Task: Save settings", "1",
            "Click Save.",
        )
        outcome = self.walk(page, (item,), timeout_s=90, interval_s=2)
        self.assertEqual(outcome.statuses["save"], "PASS")
        # Check that evidence includes success message
        evidence = outcome.evidence.get("save", ())
        self.assertTrue(any("after-state" in str(e) and "success" in str(e) for e in evidence),
                        f"Expected after-state success evidence, got: {evidence}")

    def test_existing_banner_no_new_message_needs_review(self):
        """Banner already present before click with no new message returns NEEDS_REVIEW."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("button", "Save"))
        # Include "successfully saved" in body so old code's _words_visible would find it
        page.body = "Settings page. Previous changes were successfully saved."

        # Add banner BEFORE the click
        existing_elem = MockElement("Previous changes were successfully saved.")
        page._status_elements.append(existing_elem)

        # No new banner appears after clicking
        page.on_click = lambda kind, name: None

        item = step(
            "save", "Exercise 1", "Task: Save settings", "1",
            "Click Save.",
        )
        # Use short timeout for faster test
        outcome = self.walk(page, (item,), timeout_s=5, interval_s=1)
        self.assertEqual(outcome.statuses["save"], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
