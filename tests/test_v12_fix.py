import unittest
from types import SimpleNamespace
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import walk_guide
from workshop_validator.step_board import build_board


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


def inventory_of(*items):
    from workshop_validator.guide_inventory import GuideInventory
    return GuideInventory(1, tuple(items), (), ())


class Page:
    def __init__(self, url, body="", roles=(), values=None):
        self.url = url
        self.body = body
        self.roles = set(roles)
        self.values = dict(values or {})
        self.enabled = {}
        self.events = []
        self.page_title = ""
        self.spinning = False
        self.html = ""
        self.checked = {}
        self.aria_states = {}
        self.in_popup = set()
        self.children = {}
        self.has_popup = set()
        self.state_reads = 0
        self.reads = 0
        self.snapshots = []
        self.reveal_state_after = 1
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
        return Loc(self, "css", selector)

    def get_by_role(self, role, name=None, exact=True):
        if name:
            return Loc(self, role, name)
        return _MultiLoc(self, role)

    def content(self):
        self.reads += 1
        return self.body or ""

    def goto(self, url, wait_until=None, timeout=None):
        self.events.append(("goto", url))
        self.url = url


class Loc:
    def __init__(self, page, kind, name=""):
        self.page = page
        self.kind = kind
        self.name = name

    def count(self):
        if self.kind == "css":
            return 0
        if (self.kind, self.name) in self.page.roles:
            return 1
        return 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.count() > 0

    def click(self, timeout=None):
        self.page.events.append(("click", self.kind, self.name))
        on_click = getattr(self.page, "on_click", None)
        if on_click:
            on_click(self.kind, self.name)

    def nth(self, index):
        return self

    def locator(self, selector):
        """Controls inside this element, from page.children."""
        inside = self.page.children.get((self.kind, self.name), ())
        inside = [item for item in inside if item in self.page.roles]
        if "aria-haspopup" in selector:
            inside = [item for item in inside if item in self.page.has_popup]
        return _ListLoc(self.page, inside)


class _ListLoc:
    def __init__(self, page, items):
        self.page = page
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        if index < len(self.items):
            kind, name = self.items[index]
            return Loc(self.page, kind, name)
        return Loc(self.page, "css", "")

    @property
    def first(self):
        return self.nth(0) if self.items else Loc(self.page, "css", "")


class _MultiLoc:
    """Locator that can return multiple items for a role."""
    def __init__(self, page, role, selector=""):
        self.page = page
        self.role = role
        self.items = [(kind, name) for kind, name in page.roles if kind == role]
        # A :not([role='listbox'] *) selector leaves out controls inside a popup.
        if ":not(" in selector:
            self.items = [item for item in self.items if item not in page.in_popup]

    def count(self):
        return len(self.items)

    def nth(self, index):
        if index < len(self.items):
            kind, name = self.items[index]
            return Loc(self.page, kind, name)
        return Loc(self.page, self.role, "")

    def filter(self, has_text=None):
        """Filter items by text content."""
        if has_text is None:
            return self
        filtered = []
        for item in self.items:
            # Check if the item's children contain the text
            children = self.page.children.get(item, ())
            # Check the item's name or any child name/text
            if has_text == item[1]:  # Exact match on the item name
                filtered.append(item)
            elif any(has_text == child[1] for child in children):  # Exact match in children
                filtered.append(item)
        result = _MultiLoc.__new__(_MultiLoc)
        result.page = self.page
        result.role = self.role
        result.items = filtered
        return result

    @property
    def first(self):
        return self.nth(0)


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class RowDropdownMultiRootTests(unittest.TestCase):
    def walk(self, page, items):
        clock = Clock()
        return walk_guide(
            page, inventory_of(*items), checks=None,
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )

    def test_row_dropdown_ambiguous_when_text_match_in_two_roots(self):
        """Two roots each with one row matching by text -> not found, nothing clicked.

        OLD BUG: stops at first root with a text match, clicks frame1 button.
        FIXED: collects matches from all roots, detects ambiguity, clicks nothing.
        """
        page = Page("https://example.lightning.force.com/lightning/o/ContentDocument/home")
        frame1 = Page("https://example.lightning.force.com/frame1")
        frame2 = Page("https://example.lightning.force.com/frame2")
        page.frames = [frame1, frame2]

        # Frame 1 has one row with "Sample File" text (no accessible name match)
        frame1.roles.add(("row", "file-id-1"))
        frame1.roles.add(("link", "Sample File"))
        frame1.roles.add(("button", "Actions Frame 1"))
        frame1.children[("row", "file-id-1")] = [("link", "Sample File"), ("button", "Actions Frame 1")]
        frame1.has_popup.add(("button", "Actions Frame 1"))

        # Frame 2 has one row with "Sample File" text (no accessible name match)
        frame2.roles.add(("row", "file-id-2"))
        frame2.roles.add(("link", "Sample File"))
        frame2.roles.add(("button", "Actions Frame 2"))
        frame2.children[("row", "file-id-2")] = [("link", "Sample File"), ("button", "Actions Frame 2")]
        frame2.has_popup.add(("button", "Actions Frame 2"))

        item = step(
            "file", "Exercise 1: Get Started", "Task 2: Files.", "4",
            "Click the dropdown arrow to the far right of Sample File and click Download.",
        )
        outcome = self.walk(page, (item,))

        # With the fix: zero clicks because ambiguous match detected
        # Without the fix: clicks frame1 button (wrong - misses ambiguity)
        clicks = [event for event in page.events + frame1.events + frame2.events if event[0] == "click"]
        self.assertEqual(clicks, [])
        self.assertEqual(outcome.statuses["file"], "UI_MISMATCH")

    def test_row_dropdown_found_in_second_root_only(self):
        """One text match in the second root only -> found and clicked."""
        page = Page("https://example.lightning.force.com/lightning/o/ContentDocument/home")
        frame1 = Page("https://example.lightning.force.com/frame1")
        frame2 = Page("https://example.lightning.force.com/frame2")
        page.frames = [frame1, frame2]

        # Frame 1 has no matching rows
        frame1.roles.add(("row", "other-file-id"))
        frame1.roles.add(("link", "Other File"))
        frame1.roles.add(("button", "Other Actions"))
        frame1.children[("row", "other-file-id")] = [("link", "Other File"), ("button", "Other Actions")]
        frame1.has_popup.add(("button", "Other Actions"))

        # Frame 2 has one row with "Target File" text (no accessible name match)
        frame2.roles.add(("row", "file-id-2"))
        frame2.roles.add(("link", "Target File"))
        frame2.roles.add(("button", "Target Actions"))
        frame2.children[("row", "file-id-2")] = [("link", "Target File"), ("button", "Target Actions")]
        frame2.has_popup.add(("button", "Target Actions"))

        def reveal(kind, name):
            if name == "Target Actions":
                frame2.roles.add(("menuitem", "Download"))

        frame2.on_click = reveal

        item = step(
            "file", "Exercise 1: Get Started", "Task 2: Files.", "4",
            "Click the dropdown arrow to the far right of Target File and click Download.",
        )
        outcome = self.walk(page, (item,))

        # Should find and click in frame2
        clicks = [event for event in frame2.events if event[0] == "click"]
        self.assertEqual(
            clicks, [("click", "button", "Target Actions"), ("click", "menuitem", "Download")],
        )
        self.assertEqual(outcome.statuses["file"], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
