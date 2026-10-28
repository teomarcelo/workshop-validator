"""V29: when one row's text matches twice, use the innermost row; else refuse and describe the matches."""

import unittest
from unittest import mock

from workshop_validator.guide_walk import walk_guide
from tests.test_guide_walk import Clock, Loc, Page, _MultiLoc, inventory_of, step

URL = "https://example.lightning.force.com/lightning/o/ContentDocument/home"
TARGET = "Report Alpha"
STEP_TEXT = f"Click the dropdown arrow to the far right of {TARGET} and click Download."


class FakeDom:
    """Adds what the shared fake lacks: Element.contains, aria-rowindex and bounding_box.

    page.children holds every descendant of an element, as Playwright's locator() does.
    Like the DOM, an element contains itself, so el.contains(el) is true.
    """

    def __init__(self, page):
        self.page = page
        self.attrs = {}  # (kind, name, attribute) -> value
        self.boxes = {}  # (kind, name) -> {"width": .., "height": ..}
        self.contains_calls = 0
        self.broken_contains = False

    def patches(self):
        dom = self
        evaluate, get_attribute, text_filter = Loc.evaluate, Loc.get_attribute, _MultiLoc.filter

        def ordered_filter(loc, has_text=None):
            # Rows come back in the order add_row made them (document order), not set order.
            result = text_filter(loc, has_text=has_text)
            order = getattr(dom.page, "row_order", [])
            result.items = sorted(result.items, key=lambda item: order.index(item) if item in order else -1)
            return result

        def fake_evaluate(loc, script, arg=None):
            if "contains" in script:
                dom.contains_calls += 1
                if dom.broken_contains is True or loc.name in getattr(dom, "broken_rows", ()):
                    raise RuntimeError("synthetic read failure")
                own, other = (loc.kind, loc.name), (arg.kind, arg.name)
                if own == other:
                    return "el !== other" not in script
                return other in loc.page.children.get(own, ())
            return evaluate(loc, script, arg)

        def fake_get_attribute(loc, name, timeout=None):
            key = (loc.kind, loc.name, name)
            if key in dom.attrs:
                return dom.attrs[key]
            return get_attribute(loc, name, timeout)

        def fake_box(loc, timeout=None):
            return dom.boxes.get((loc.kind, loc.name))

        return [
            mock.patch.object(Loc, "evaluate", fake_evaluate),
            mock.patch.object(Loc, "get_attribute", fake_get_attribute),
            mock.patch.object(Loc, "bounding_box", fake_box, create=True),
            mock.patch.object(_MultiLoc, "filter", ordered_filter),
        ]


def add_row(page, name, own, inside_of=()):
    """A row named NAME whose own controls are OWN; it and OWN are also listed inside each of INSIDE_OF."""
    key = ("row", name)
    page.roles.add(key)
    page.children.setdefault(key, [])
    if not hasattr(page, "row_order"):
        page.row_order = []
    page.row_order.append(key)
    for item in own:
        page.roles.add(item)
        page.children[key].append(item)
    for outer in inside_of:
        page.children[("row", outer)].append(key)
        page.children[("row", outer)].extend(own)


def menu_on(page, button):
    page.has_popup.add(("button", button))

    def reveal(kind, name):
        if kind == "button" and name == button:
            page.roles.add(("menuitem", "Download"))

    page.on_click = reveal


class NestedRowTests(unittest.TestCase):
    def walk(self, page, dom):
        clock = Clock()
        item = step("file", "Exercise 1: Files", "Task 2: Download", "1", STEP_TEXT)
        patches = dom.patches()
        for patch in patches:
            patch.start()
        try:
            outcome = walk_guide(
                page, inventory_of(item), checks=None,
                timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
            )
        finally:
            for patch in patches:
                patch.stop()
        clicks = [event for event in page.events if event[0] == "click"]
        return outcome.statuses["file"], outcome.notes["file"], clicks

    def assert_refused(self, page, dom):
        status, note, clicks = self.walk(page, dom)
        self.assertEqual(clicks, [])
        self.assertEqual(status, "UI_MISMATCH")
        self.assertIn(f"The dropdown in the {TARGET} row was not found", note)
        return note

    def test_nested_rows_use_the_inner_rows_button(self):
        page = Page(URL)
        add_row(page, "outer-row", [("link", TARGET), ("button", "Outer Menu")])
        add_row(page, "inner-row", [("link", TARGET), ("button", "Inner Menu")], inside_of=["outer-row"])
        page.has_popup.add(("button", "Outer Menu"))
        menu_on(page, "Inner Menu")
        status, note, clicks = self.walk(page, FakeDom(page))
        self.assertEqual(clicks, [("click", "button", "Inner Menu"), ("click", "menuitem", "Download")])
        self.assertEqual(status, "NEEDS_REVIEW")

    def test_inner_row_listed_first_is_still_used(self):
        page = Page(URL)
        add_row(page, "inner-row", [("link", TARGET), ("button", "Inner Menu")])
        add_row(page, "outer-row", [("link", TARGET), ("button", "Outer Menu")])
        page.children[("row", "outer-row")].extend([("row", "inner-row"), ("button", "Inner Menu")])
        self.assertEqual(page.row_order[0], ("row", "inner-row"))
        page.has_popup.add(("button", "Outer Menu"))
        menu_on(page, "Inner Menu")
        status, note, clicks = self.walk(page, FakeDom(page))
        self.assertEqual(clicks, [("click", "button", "Inner Menu"), ("click", "menuitem", "Download")])
        self.assertEqual(status, "NEEDS_REVIEW")

    def test_three_nested_rows_use_the_innermost(self):
        page = Page(URL)
        add_row(page, "row-a", [("link", TARGET), ("button", "Menu A")])
        add_row(page, "row-b", [("link", TARGET), ("button", "Menu B")], inside_of=["row-a"])
        add_row(page, "row-c", [("link", TARGET), ("button", "Menu C")], inside_of=["row-a", "row-b"])
        page.has_popup.update({("button", "Menu A"), ("button", "Menu B")})
        menu_on(page, "Menu C")
        status, note, clicks = self.walk(page, FakeDom(page))
        self.assertEqual(clicks, [("click", "button", "Menu C"), ("click", "menuitem", "Download")])
        self.assertEqual(status, "NEEDS_REVIEW")

    def test_two_sibling_rows_are_refused_with_row_details(self):
        page = Page(URL)
        add_row(page, "doc-id-1", [("link", TARGET), ("button", "Menu One")])
        add_row(page, "doc-id-2", [("link", TARGET), ("button", "Menu Two")])
        menu_on(page, "Menu One")
        page.has_popup.add(("button", "Menu Two"))
        dom = FakeDom(page)
        page.tags = {("row", "doc-id-1"): "TR", ("row", "doc-id-2"): "div"}
        dom.attrs = {
            ("row", "doc-id-1", "role"): "row", ("row", "doc-id-1", "aria-rowindex"): "2",
            ("row", "doc-id-2", "role"): "row", ("row", "doc-id-2", "aria-rowindex"): "3",
        }
        dom.boxes = {
            ("row", "doc-id-1"): {"x": 0, "y": 0, "width": 799.6, "height": 40.2},
            ("row", "doc-id-2"): {"x": 0, "y": 40, "width": 12.4, "height": 0},
        }
        note = self.assert_refused(page, dom)
        self.assertIn("Rows matched: 2.", note)
        details = [
            "Row 1: tag tr, role row, rowindex 2, size 800x40, contains a match: no.",
            "Row 2: tag div, role row, rowindex 3, size 12x0, contains a match: no.",
        ]
        self.assertEqual([part for part in details if part in note], details)
        self.assertNotIn("doc-id", note)

    def test_outer_row_with_two_inner_rows_is_refused(self):
        page = Page(URL)
        add_row(page, "outer-row", [("button", "Outer Menu")])
        add_row(page, "inner-1", [("link", TARGET), ("button", "Menu One")], inside_of=["outer-row"])
        add_row(page, "inner-2", [("link", TARGET), ("button", "Menu Two")], inside_of=["outer-row"])
        menu_on(page, "Menu One")
        page.has_popup.add(("button", "Menu Two"))
        # The fake's text filter reads direct listings only, so the outer row lists the text too.
        page.children[("row", "outer-row")].append(("link", TARGET))
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("Rows matched: 3.", note)
        shape = "tag unknown, role none, rowindex none, size unknown"
        self.assertIn(f"Row 1: {shape}, contains a match: yes.", note)
        self.assertIn(f"Row 2: {shape}, contains a match: no.", note)
        self.assertIn(f"Row 3: {shape}, contains a match: no.", note)

    def test_details_list_at_most_three_rows(self):
        page = Page(URL)
        for i in range(1, 5):
            add_row(page, f"doc-id-{i}", [("link", TARGET)])
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("Rows matched: 4.", note)
        self.assertIn("Row 3:", note)
        self.assertNotIn("Row 4:", note)

    def test_unsafe_attribute_values_are_not_shown(self):
        page = Page(URL)
        add_row(page, "doc-id-1", [("link", TARGET)])
        add_row(page, "doc-id-2", [("link", TARGET)])
        page.tags = {("row", "doc-id-1"): "<img src=https://example.test/x>", ("row", "doc-id-2"): "<img src=https://example.test/x>"}
        dom = FakeDom(page)
        for row in ("doc-id-1", "doc-id-2"):
            dom.attrs[("row", row, "role")] = "row jane-smith"
            dom.attrs[("row", row, "aria-rowindex")] = "2; Jane"
        note = self.assert_refused(page, dom)
        self.assertIn("Row 1: tag unknown, role none, rowindex none, size unknown", note)
        for leaked in ("example.test", "img", "jane", "Jane"):
            self.assertNotIn(leaked, note)

    def test_failed_contains_read_refuses_nested_rows(self):
        page = Page(URL)
        add_row(page, "outer-row", [("link", TARGET), ("button", "Outer Menu")])
        add_row(page, "inner-row", [("link", TARGET), ("button", "Inner Menu")], inside_of=["outer-row"])
        menu_on(page, "Inner Menu")
        dom = FakeDom(page)
        dom.broken_contains = True
        note = self.assert_refused(page, dom)
        self.assertIn("Rows matched: 2.", note)

    def test_unreadable_row_is_kept_so_siblings_are_refused(self):
        # A row whose contains read fails is never dropped; dropping it would leave the
        # other sibling as a guess.
        page = Page(URL)
        add_row(page, "doc-id-1", [("link", TARGET), ("button", "Menu One")])
        add_row(page, "doc-id-2", [("link", TARGET), ("button", "Menu Two")])
        menu_on(page, "Menu Two")
        page.has_popup.add(("button", "Menu One"))
        dom = FakeDom(page)
        dom.broken_rows = {"doc-id-1"}
        note = self.assert_refused(page, dom)
        self.assertIn("Rows matched: 2.", note)
        self.assertIn("Row 1: tag unknown, role none, rowindex none, size unknown, contains a match: no.", note)

    def test_one_matching_row_is_unchanged(self):
        page = Page(URL)
        add_row(page, "doc-id-1", [("link", TARGET), ("button", "Menu One")])
        add_row(page, "doc-id-2", [("link", "Report Beta"), ("button", "Menu Two")])
        menu_on(page, "Menu One")
        page.has_popup.add(("button", "Menu Two"))
        dom = FakeDom(page)
        status, note, clicks = self.walk(page, dom)
        self.assertEqual(clicks, [("click", "button", "Menu One"), ("click", "menuitem", "Download")])
        self.assertEqual(status, "NEEDS_REVIEW")
        self.assertEqual(dom.contains_calls, 0)


if __name__ == "__main__":
    unittest.main()
