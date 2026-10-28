"""V29 / W-092: when one row's text matches twice, use the innermost row only when it
shows the exact name and no outer row shows another one; else refuse and describe the matches."""

import os
import re
import unittest
from unittest import mock

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.guide_walk import _row_menu_scan, walk_guide
from tests.test_guide_walk import Clock, Loc, Page, _MultiLoc, inventory_of, step

URL = "https://example.lightning.force.com/lightning/o/ContentDocument/home"
TARGET = "Report Alpha"
LOOK_ALIKE = "Report Alpha v2"
STEP_TEXT = f"Click the dropdown arrow to the far right of {TARGET} and click Download."


class SyntheticTimeout(Exception):
    """Stands in for Playwright's TimeoutError."""


def _norm(text):
    return " ".join(str(text or "").split())


class FakeHandle:
    def __init__(self, dom, key):
        self.dom = dom
        self.key = key

    def dispose(self):
        self.dom.disposed.append(self.key)


class FakeDom:
    """Adds what the shared fake lacks: element handles, Element.contains, exact visible
    text, aria-rowindex and bounding_box.

    page.children holds every descendant of an element, as Playwright's locator() does.
    Like the DOM, an element contains itself, so el.contains(el) is true. The contains
    read honours the direction the script names (a.contains(b)).
    """

    def __init__(self, page):
        self.page = page
        self.attrs = {}  # (kind, name, attribute) -> value
        self.boxes = {}  # (kind, name) -> {"width": .., "height": ..}
        self.contains_calls = 0
        self.broken_contains = False
        self.broken_rows = set()  # row names whose contains read fails
        self.slow_rows = set()  # row names whose contains read times out
        self.empty_rows = set()  # row names whose contains read returns nothing
        self.broken_exact = set()  # row names whose exact-text read fails
        self.broken_handles = set()  # row names whose element_handle fails
        self.handles = []
        self.disposed = []
        self.timeouts = []  # (read, timeout) for every row read

    def contains(self, outer, inner):
        return outer == inner or inner in self.page.children.get(outer, ())

    def exact(self, own, target, skip):
        page = self.page
        for node in [own, *page.children.get(own, ())]:
            if node not in page.roles or node in page.hidden:
                continue
            if skip is not None and (self.contains(skip, node) or self.contains(node, skip)):
                continue
            if _norm(node[1]) == target:
                return True
        return False

    def patches(self):
        dom = self
        evaluate, get_attribute, handle = Loc.evaluate, Loc.get_attribute, Loc.element_handle

        def text_filter(loc, has_text=None):
            # Like Playwright: a case-insensitive, whitespace-normalized substring of the
            # row's text, in document order (the order add_row made them).
            if has_text is None:
                return loc
            needle = _norm(has_text).lower()
            items = [
                item for item in loc.items
                if any(needle in _norm(node[1]).lower()
                       for node in [item, *dom.page.children.get(item, ())])
            ]
            order = getattr(dom.page, "row_order", [])
            result = _MultiLoc.__new__(_MultiLoc)
            result.page, result.role = loc.page, loc.role
            result.items = sorted(items, key=lambda item: order.index(item) if item in order else -1)
            return result

        def fake_handle(loc, timeout=None):
            if loc.kind != "row":
                return handle(loc, timeout)
            dom.timeouts.append(("element_handle", timeout))
            if loc.name in dom.broken_handles:
                raise RuntimeError("synthetic read failure")
            made = FakeHandle(dom, (loc.kind, loc.name))
            dom.handles.append(made)
            return made

        def fake_evaluate(loc, script, arg=None, timeout=None):
            own = (loc.kind, loc.name)
            if loc.kind == "row":
                dom.timeouts.append(("evaluate", timeout))
            if "innerText" in script:
                if loc.name in dom.broken_exact:
                    raise RuntimeError("synthetic read failure")
                target, skip = arg
                return dom.exact(own, target, None if skip is None else skip.key)
            if ".contains(" in script:
                dom.contains_calls += 1
                if dom.broken_contains is True or loc.name in dom.broken_rows:
                    raise RuntimeError("synthetic read failure")
                if loc.name in dom.slow_rows:
                    raise SyntheticTimeout("Timeout 1000ms exceeded.")
                if loc.name in dom.empty_rows:
                    return None
                # (el, others) => others.map(other => a === b ? "same" : c.contains(d) ? "holds" : "")
                el_name = re.match(r"\s*\((\w+),", script).group(1)
                each_name = re.search(r"\.map\(\s*(\w+)\s*=>", script).group(1)
                (same_a, same_op, same_b), = re.findall(r"(\w+)\s*(===|!==)\s*(\w+)", script)
                (left, right), = re.findall(r"(\w+)\.contains\((\w+)\)", script)
                found = []
                for other in arg:
                    names = {el_name: own, each_name: other.key}
                    same = names[same_a] == names[same_b]
                    if same == (same_op == "==="):
                        found.append("same")
                    elif dom.contains(names[left], names[right]):
                        found.append("holds")
                    else:
                        found.append("")
                return found
            return evaluate(loc, script, arg)

        def fake_get_attribute(loc, name, timeout=None):
            if loc.kind == "row":
                dom.timeouts.append(("get_attribute", timeout))
            key = (loc.kind, loc.name, name)
            if key in dom.attrs:
                return dom.attrs[key]
            return get_attribute(loc, name, timeout)

        def fake_box(loc, timeout=None):
            dom.timeouts.append(("bounding_box", timeout))
            return dom.boxes.get((loc.kind, loc.name))

        return [
            mock.patch.object(Loc, "evaluate", fake_evaluate),
            mock.patch.object(Loc, "element_handle", fake_handle),
            mock.patch.object(Loc, "get_attribute", fake_get_attribute),
            mock.patch.object(Loc, "bounding_box", fake_box, create=True),
            mock.patch.object(_MultiLoc, "filter", text_filter),
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


def wrapper_with_exact_inner(page):
    """An outer row with no text of its own, around the one row that shows TARGET."""
    add_row(page, "outer-row", [("button", "Outer Menu")])
    add_row(page, "inner-row", [("link", TARGET), ("button", "Inner Menu")], inside_of=["outer-row"])
    page.has_popup.add(("button", "Outer Menu"))
    menu_on(page, "Inner Menu")


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

    def assert_used_inner(self, page, dom, button="Inner Menu"):
        status, note, clicks = self.walk(page, dom)
        self.assertEqual(clicks, [("click", "button", button), ("click", "menuitem", "Download")])
        self.assertEqual(status, "NEEDS_REVIEW")

    def assert_refused(self, page, dom):
        status, note, clicks = self.walk(page, dom)
        self.assertEqual(clicks, [])
        self.assertEqual(status, "UI_MISMATCH")
        self.assertIn(f"The dropdown in the {TARGET} row was not found", note)
        return note

    # Nested rows whose inner row shows the exact name: the inner row is used.

    def test_nested_rows_use_the_inner_rows_button(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        self.assert_used_inner(page, FakeDom(page))

    def test_inner_row_listed_first_is_still_used(self):
        page = Page(URL)
        add_row(page, "inner-row", [("link", TARGET), ("button", "Inner Menu")])
        add_row(page, "outer-row", [("button", "Outer Menu")])
        page.children[("row", "outer-row")].extend([("row", "inner-row"), ("link", TARGET), ("button", "Inner Menu")])
        self.assertEqual(page.row_order[0], ("row", "inner-row"))
        page.has_popup.add(("button", "Outer Menu"))
        menu_on(page, "Inner Menu")
        self.assert_used_inner(page, FakeDom(page))

    def test_three_nested_rows_use_the_innermost(self):
        page = Page(URL)
        add_row(page, "row-a", [("button", "Menu A")])
        add_row(page, "row-b", [("button", "Menu B")], inside_of=["row-a"])
        add_row(page, "row-c", [("link", TARGET), ("button", "Menu C")], inside_of=["row-a", "row-b"])
        page.has_popup.update({("button", "Menu A"), ("button", "Menu B")})
        menu_on(page, "Menu C")
        self.assert_used_inner(page, FakeDom(page), button="Menu C")

    def test_target_whitespace_is_normalized(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        dom = FakeDom(page)
        patches = dom.patches()
        for patch in patches:
            patch.start()
        try:
            button, note = _row_menu_scan(page, f"  Report \n  Alpha ")
        finally:
            for patch in patches:
                patch.stop()
        self.assertEqual((button.kind, button.name, note), ("button", "Inner Menu", ""))

    # A real page and its main frame both find every row, so one row matches twice.

    def test_one_row_found_by_page_and_main_frame_is_used(self):
        page = Page(URL)
        page.frames = [page, page]
        add_row(page, "doc-id-1", [("link", TARGET), ("button", "Menu One")])
        menu_on(page, "Menu One")
        self.assert_used_inner(page, FakeDom(page), button="Menu One")

    def test_look_alike_row_found_twice_is_refused(self):
        page = Page(URL)
        page.frames = [page, page]
        add_row(page, "doc-id-1", [("link", LOOK_ALIKE), ("button", "Menu One")])
        menu_on(page, "Menu One")
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("Rows matched: 2.", note)
        self.assertIn("The innermost row was not used", note)

    def test_sibling_rows_found_twice_are_refused(self):
        page = Page(URL)
        page.frames = [page, page]
        add_row(page, "doc-id-1", [("link", TARGET), ("button", "Menu One")])
        add_row(page, "doc-id-2", [("link", TARGET), ("button", "Menu Two")])
        menu_on(page, "Menu One")
        page.has_popup.add(("button", "Menu Two"))
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("Rows matched: 4.", note)
        self.assertNotIn("The innermost row was not used", note)

    def test_nested_rows_found_twice_use_the_inner_row(self):
        page = Page(URL)
        page.frames = [page, page]
        wrapper_with_exact_inner(page)
        self.assert_used_inner(page, FakeDom(page))

    # W-092: the innermost row is used only when it alone shows the exact name.

    def test_look_alike_inner_row_is_refused(self):
        page = Page(URL)
        add_row(page, "outer-row", [("button", "Outer Menu")])
        add_row(page, "inner-row", [("link", LOOK_ALIKE), ("button", "Inner Menu")], inside_of=["outer-row"])
        page.has_popup.add(("button", "Outer Menu"))
        menu_on(page, "Inner Menu")
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("Rows matched: 2.", note)
        self.assertIn("The innermost row was not used", note)
        self.assertNotIn(LOOK_ALIKE, note)

    def test_exact_name_only_in_the_outer_row_is_refused(self):
        page = Page(URL)
        add_row(page, "outer-row", [("link", TARGET), ("button", "Outer Menu")])
        add_row(page, "inner-row", [("link", LOOK_ALIKE), ("button", "Inner Menu")], inside_of=["outer-row"])
        page.has_popup.add(("button", "Outer Menu"))
        menu_on(page, "Inner Menu")
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("The innermost row was not used", note)

    def test_exact_name_in_the_outer_row_too_is_refused(self):
        page = Page(URL)
        # The fake keys elements by (kind, name); the outer row's own copy differs only
        # in whitespace, so it is a separate element showing the same exact name.
        add_row(page, "outer-row", [("link", "Report  Alpha"), ("button", "Outer Menu")])
        add_row(page, "inner-row", [("link", TARGET), ("button", "Inner Menu")], inside_of=["outer-row"])
        page.has_popup.add(("button", "Outer Menu"))
        menu_on(page, "Inner Menu")
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("The innermost row was not used", note)

    def test_failed_exact_read_of_the_inner_row_refuses(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        dom = FakeDom(page)
        dom.broken_exact = {"inner-row"}
        self.assert_refused(page, dom)

    def test_failed_exact_read_of_the_outer_row_refuses(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        dom = FakeDom(page)
        dom.broken_exact = {"outer-row"}
        self.assert_refused(page, dom)

    # Bounded reads.

    def test_row_reads_are_bounded_and_handles_disposed(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        dom = FakeDom(page)
        self.assert_used_inner(page, dom)
        self.assertTrue(dom.timeouts)
        self.assertTrue(all(t is not None and 0 < t <= 1000 for _, t in dom.timeouts), dom.timeouts)
        self.assertTrue(dom.handles)
        self.assertEqual(sorted(dom.disposed), sorted(h.key for h in dom.handles))

    def test_handles_are_disposed_when_a_handle_read_fails(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        dom = FakeDom(page)
        dom.broken_handles = {"inner-row"}
        self.assert_refused(page, dom)
        self.assertTrue(dom.handles)
        self.assertEqual(sorted(dom.disposed), sorted(h.key for h in dom.handles))

    def test_shape_reads_are_bounded(self):
        page = Page(URL)
        add_row(page, "doc-id-1", [("link", TARGET)])
        add_row(page, "doc-id-2", [("link", TARGET)])
        dom = FakeDom(page)
        self.assert_refused(page, dom)
        reads = {read for read, _ in dom.timeouts}
        self.assertTrue({"bounding_box", "get_attribute", "evaluate"} <= reads, reads)
        self.assertTrue(all(t is not None and 0 < t <= 1000 for _, t in dom.timeouts), dom.timeouts)

    def test_timed_out_contains_read_keeps_the_outer_row(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        dom = FakeDom(page)
        dom.slow_rows = {"outer-row"}
        note = self.assert_refused(page, dom)
        self.assertIn("Rows matched: 2.", note)
        self.assertIn("Row 1: tag unknown, role none, rowindex none, size unknown, contains a match: no.", note)

    def test_empty_contains_read_keeps_the_outer_row(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
        dom = FakeDom(page)
        dom.empty_rows = {"outer-row"}
        note = self.assert_refused(page, dom)
        self.assertIn("Rows matched: 2.", note)

    # Refusals and their details.

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
        self.assertNotIn("The innermost row was not used", note)

    def test_outer_row_with_two_inner_rows_is_refused(self):
        page = Page(URL)
        add_row(page, "outer-row", [("button", "Outer Menu")])
        add_row(page, "inner-1", [("link", TARGET), ("button", "Menu One")], inside_of=["outer-row"])
        add_row(page, "inner-2", [("link", TARGET), ("button", "Menu Two")], inside_of=["outer-row"])
        menu_on(page, "Menu One")
        page.has_popup.add(("button", "Menu Two"))
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("Rows matched: 3.", note)
        shape = "tag unknown, role none, rowindex none, size unknown"
        self.assertIn(f"Row 1: {shape}, contains a match: yes.", note)
        self.assertIn(f"Row 2: {shape}, contains a match: no.", note)
        self.assertIn(f"Row 3: {shape}, contains a match: no.", note)

    def test_rows_that_hold_each_other_are_refused_with_details(self):
        # No innermost row at all: the refusal still lists the matches.
        page = Page(URL)
        add_row(page, "doc-id-1", [("link", TARGET), ("button", "Menu One")])
        add_row(page, "doc-id-2", [("link", TARGET), ("button", "Menu Two")])
        page.children[("row", "doc-id-1")].append(("row", "doc-id-2"))
        page.children[("row", "doc-id-2")].append(("row", "doc-id-1"))
        menu_on(page, "Menu One")
        note = self.assert_refused(page, FakeDom(page))
        self.assertIn("Rows matched: 2.", note)
        self.assertIn("Row 1: tag unknown, role none, rowindex none, size unknown, contains a match: yes.", note)
        self.assertIn("Row 2: tag unknown, role none, rowindex none, size unknown, contains a match: yes.", note)

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

    def test_tag_must_fully_match_and_role_is_read_lowercase(self):
        page = Page(URL)
        add_row(page, "doc-id-1", [("link", TARGET)])
        add_row(page, "doc-id-2", [("link", TARGET)])
        page.tags = {("row", "doc-id-1"): "div onclick", ("row", "doc-id-2"): "DIV"}
        dom = FakeDom(page)
        dom.attrs = {("row", "doc-id-1", "role"): "ROW", ("row", "doc-id-2", "role"): "Row"}
        note = self.assert_refused(page, dom)
        self.assertIn("Row 1: tag unknown, role row,", note)
        self.assertIn("Row 2: tag div, role row,", note)
        self.assertNotIn("onclick", note)

    def test_failed_contains_read_refuses_nested_rows(self):
        page = Page(URL)
        wrapper_with_exact_inner(page)
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
        self.assertEqual(dom.handles, [])


def _row(inner_html, outer=True):
    menu = '<button aria-haspopup="true" aria-label="Show actions"></button>'
    inner = f'<div role="row" id="inner">{inner_html}{menu}</div>'
    return f'<div role="grid"><div role="row" id="outer">{inner}</div></div>' if outer else inner


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class NestedRowBrowserTests(unittest.TestCase):
    """The row reads run in a real browser, so the scripts are checked, not only the fake."""

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

    def scan(self, html):
        page = self.browser.new_page()
        try:
            page.set_content(html)
            button, note = _row_menu_scan(page, TARGET)
            owner = None if button is None else button.evaluate("el => el.closest('[role=row]').id")
            return owner, note
        finally:
            page.close()

    def test_one_exact_row_is_used(self):
        owner, note = self.scan(f'<div role="grid">{_row(f"<a href=\'#\'>{TARGET}</a>", outer=False)}</div>')
        self.assertEqual((owner, note), ("inner", ""))

    def test_exact_name_deep_in_a_cell_is_found(self):
        cell = f"<div role='gridcell'><span><a href='#'>{TARGET}</a> <span>2 KB</span></span></div>"
        owner, note = self.scan(f'<div role="grid">{_row(cell, outer=False)}</div>')
        self.assertEqual((owner, note), ("inner", ""))

    def test_row_whose_own_text_is_the_exact_name_is_used(self):
        owner, note = self.scan(f'<div role="grid">{_row(TARGET, outer=False)}</div>')
        self.assertEqual((owner, note), ("inner", ""))

    def test_one_look_alike_row_is_refused(self):
        owner, note = self.scan(f'<div role="grid">{_row(f"<a href=\'#\'>{LOOK_ALIKE}</a>", outer=False)}</div>')
        self.assertIsNone(owner)
        self.assertIn("The innermost row was not used", note)

    def test_wrapper_around_the_exact_row_uses_the_inner_row(self):
        owner, note = self.scan(_row(f"<a href='#'>{TARGET}</a>"))
        self.assertEqual((owner, note), ("inner", ""))

    def test_whitespace_in_the_page_text_is_normalized(self):
        owner, note = self.scan(_row("<a href='#'>  Report \n   Alpha </a>"))
        self.assertEqual((owner, note), ("inner", ""))

    def test_hidden_copy_in_the_outer_row_is_not_counted(self):
        html = _row(f"<a href='#'>{TARGET}</a>").replace(
            '<div role="row" id="inner">',
            f'<span style="display:none">{TARGET}</span><div role="row" id="inner">',
        )
        owner, note = self.scan(html)
        self.assertEqual((owner, note), ("inner", ""))

    def test_look_alike_inner_row_is_refused(self):
        owner, note = self.scan(_row(f"<a href='#'>{LOOK_ALIKE}</a>"))
        self.assertIsNone(owner)
        # The page and its main frame each find both rows.
        self.assertIn("Rows matched: 4.", note)
        self.assertIn("contains a match: yes", note)
        self.assertIn("The innermost row was not used", note)

    def test_exact_name_in_the_outer_row_too_is_refused(self):
        html = _row(f"<a href='#'>{TARGET}</a>").replace(
            '<div role="row" id="inner">', f'<span>{TARGET}</span><div role="row" id="inner">',
        )
        owner, note = self.scan(html)
        self.assertIsNone(owner)
        self.assertIn("The innermost row was not used", note)

    def test_sibling_rows_are_refused(self):
        row = _row(f"<a href='#'>{TARGET}</a>", outer=False)
        owner, note = self.scan(f'<div role="grid">{row}{row.replace("inner", "second")}</div>')
        self.assertIsNone(owner)
        self.assertIn("Rows matched: 4.", note)
        self.assertIn("contains a match: no", note)
        self.assertNotIn("The innermost row was not used", note)


if __name__ == "__main__":
    unittest.main()
