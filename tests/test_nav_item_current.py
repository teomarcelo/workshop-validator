"""V26: a nav item turning current after a click, the Files home nav read, and the
NEEDS_REVIEW note on the clicked element. Synthetic pages only."""
import unittest

from tests.test_click_polling import PollClock, clicks, walk
from tests.test_guide_walk import Page, inventory_of, step
from workshop_validator.guide_walk import walk_guide

HOME = "https://example.lightning.force.com/lightning/page/home"
SETUP = "https://example.lightning.force.com/lightning/setup/SomePage/home"
NAV_CURRENT = "one-app-nav-bar [aria-current='page']"


def nav_page():
    """A Widgets span inside its own nav item, next to another nav item."""
    page = Page(HOME, body="Widgets\nGadgets")
    own, other = ("navlink", "Widgets nav"), ("navlink", "Gadgets nav")
    page.roles.update({own, other})
    page.tab_of[("text", "Widgets")] = own
    page.item_of = {("text", "Widgets"): ("listitem", "Widgets item")}
    page.roles.add(("listitem", "Widgets item"))
    return page, own, other


def nav_step():
    return step("t2", "Exercise 9: Widgets", "Task 2: Open widgets.", "1", "Click Widgets.")


def files_page(current_text, before=False, hidden=False):
    """A Setup page with Files text; opening the Files home makes CURRENT_TEXT the current nav item."""
    page = Page(SETUP, body="Personal Information\nFiles")
    item = ("navlink", current_text)
    page.roles.add(item)
    if hidden:
        page.hidden.add(item)
    page.css = {NAV_CURRENT: [item] if before else []}

    def opened(url):
        page.css[NAV_CURRENT] = [item]

    page.on_goto = opened
    return page


def files_step():
    return step("f1", "Exercise 5: Files", "Task 1: Open files.", "1", "Click the Files tab.")


class NavItemCurrentTests(unittest.TestCase):
    def test_own_nav_item_turning_current_at_the_click_passes(self):
        page, own, _ = nav_page()

        def react(kind, name):
            if name == "Widgets":
                page.aria_states[(*own, "aria-current")] = "page"

        page.on_click = react
        clock = PollClock()
        outcome = walk(page, nav_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "PASS")
        self.assertEqual(outcome.evidence["t2"], ("t2:after-state:-Widgets-nav-item-selected",))
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])

    def test_own_nav_item_turning_current_on_a_later_poll_passes(self):
        page, own, _ = nav_page()

        def on_sleep(n):
            if n == 2:
                page.aria_states[(*own, "aria-current")] = "page"

        clock = PollClock(on_sleep)
        outcome = walk(page, nav_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "PASS")
        self.assertEqual(outcome.evidence["t2"], ("t2:after-state:-Widgets-nav-item-selected",))
        self.assertEqual(clock.sleeps, [2, 2])

    def test_nav_item_already_current_before_the_click_is_not_evidence(self):
        page, own, _ = nav_page()
        page.aria_states[(*own, "aria-current")] = "page"
        page.aria_states[("listitem", "Widgets item", "aria-current")] = "page"
        clock = PollClock()
        outcome = walk(page, nav_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("t2", ()), ())
        self.assertIn("nearest tab or list item: - role=- aria-selected=- aria-current=page", outcome.notes["t2"])
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])

    def test_another_nav_item_turning_current_is_not_evidence(self):
        page, own, other = nav_page()

        def react(kind, name):
            if name == "Widgets":
                page.aria_states[(*other, "aria-current")] = "page"

        page.on_click = react
        clock = PollClock()
        outcome = walk(page, nav_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("t2", ()), ())
        self.assertIn("Clicked element:", outcome.notes["t2"])

    def test_aria_current_other_than_page_is_not_evidence(self):
        page, own, _ = nav_page()

        def react(kind, name):
            if name == "Widgets":
                page.aria_states[(*own, "aria-current")] = "location"

        page.on_click = react
        outcome = walk(page, nav_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("t2", ()), ())
        self.assertIn("Clicked element:", outcome.notes["t2"])


class FilesHomeNavTests(unittest.TestCase):
    def test_current_nav_item_files_after_opening_the_files_home_passes(self):
        page = files_page("Files")
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "PASS")
        self.assertEqual(outcome.evidence["f1"], ("f1:after-state:-files-nav-item-current",))
        self.assertIn(("goto", "https://example.lightning.force.com/lightning/o/ContentDocument/home"), page.events)
        self.assertEqual(clicks(page), [("click", "text", "Files")])

    def test_current_nav_item_with_another_name_is_not_evidence(self):
        for text in ("Home", "Recent Files", "Files List"):
            with self.subTest(text=text):
                page = files_page(text)
                outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
                self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
                self.assertEqual(outcome.evidence.get("f1", ()), ())
                self.assertIn("Clicked element:", outcome.notes["f1"])

    def test_hidden_current_nav_item_is_not_evidence(self):
        page = files_page("Files", hidden=True)
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertIn("Clicked element:", outcome.notes["f1"])

    def test_files_nav_item_already_current_before_the_click_is_not_evidence(self):
        page = files_page("Files", before=True)
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertIn("Clicked element:", outcome.notes["f1"])

    def test_current_nav_item_is_not_read_after_an_ordinary_click(self):
        page, _, _ = nav_page()
        item = ("navlink", "Widgets")
        page.roles.add(item)
        page.css = {NAV_CURRENT: []}

        def react(kind, name):
            if name == "Widgets":
                page.css[NAV_CURRENT] = [item]

        page.on_click = react
        outcome = walk(page, nav_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("t2", ()), ())
        self.assertIn("Clicked element:", outcome.notes["t2"])


class ClickedElementNoteTests(unittest.TestCase):
    def test_needs_review_note_describes_the_clicked_element_without_urls(self):
        page, own, _ = nav_page()
        page.tags = {("text", "Widgets"): "a", ("listitem", "Widgets item"): "li"}
        clicked = ("text", "Widgets")
        page.aria_states[(*clicked, "role")] = "https://example.lightning.force.com/x"
        page.aria_states[(*clicked, "aria-expanded")] = "false"
        page.aria_states[(*clicked, "class")] = "navItem slds-is-active"
        page.aria_states[("listitem", "Widgets item", "role")] = "listitem"
        outcome = walk(page, nav_step(), PollClock(), page_wait_s=10)
        note = outcome.notes["t2"]
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertIn(
            "Clicked element: a role=other aria-selected=- aria-current=- aria-pressed=- "
            "aria-expanded=false slds-is-active=yes; nearest tab or list item: li role=listitem "
            "aria-selected=- aria-current=- aria-pressed=- aria-expanded=- slds-is-active=no.",
            note,
        )
        self.assertNotIn("http", note)
        self.assertNotIn("example", note)

    def test_needs_review_without_a_poll_also_describes_the_clicked_element(self):
        page, _, _ = nav_page()
        clock = PollClock()
        outcome = walk_guide(
            page, inventory_of(nav_step()), checks=(),
            timeout_s=90, interval_s=2, clock=clock, sleeper=None, page_wait_s=10,
        )
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [])
        self.assertIn("Clicked element: - role=- aria-selected=-", outcome.notes["t2"])

    def test_note_without_a_list_item_around_the_element_says_so(self):
        page, _, _ = nav_page()
        page.item_of = {}
        outcome = walk(page, nav_step(), PollClock(), page_wait_s=10)
        self.assertIn("; no tab or list item around it.", outcome.notes["t2"])


if __name__ == "__main__":
    unittest.main()
