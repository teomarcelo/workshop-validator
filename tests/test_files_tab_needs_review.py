"""F-P005N004-NR: Files tab from Setup never passes, only NEEDS_REVIEW. Synthetic pages only."""
import unittest

from tests.test_click_polling import PollClock, clicks, walk
from tests.test_guide_walk import Page, step

SETUP = "https://example.lightning.force.com/lightning/setup/SomePage/home"
FILES_HOME = "https://example.lightning.force.com/lightning/o/ContentDocument/home"
NAV_CURRENT = "one-app-nav-bar [aria-current='page']"


def files_step():
    return step("f1", "Exercise 5: Files", "Task 1: Open files.", "1", "Click the Files tab.")


def setup_page_with_files():
    """A Setup page with Files text."""
    page = Page(SETUP, body="Personal Information\nFiles")
    page.roles.add(("text", "Files"))
    return page


class FilesTabNeedsReviewTests(unittest.TestCase):
    """Files clicked from Setup opens Files home. No evidence path can prove Files content opened,
    so the step is frozen NEEDS_REVIEW for step-verifier. The walk continues past the frozen step."""

    def test_files_nav_item_current_after_goto_is_needs_review(self):
        """Case (a): nav item with aria-current="page" after the goto."""
        page = setup_page_with_files()
        item = ("navlink", "Files")
        page.roles.add(item)
        page.css = {NAV_CURRENT: []}

        def opened(url):
            if url == FILES_HOME:
                page.css[NAV_CURRENT] = [item]

        page.on_goto = opened
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertIn("Opened the Files home", outcome.notes["f1"])
        self.assertIn("step-verifier judges this step", outcome.notes["f1"])
        self.assertEqual(outcome.evidence.get("f1", ()), ())
        self.assertIn(("goto", FILES_HOME), page.events)

    def test_files_with_new_dialog_is_needs_review(self):
        """Case (b): a new role=dialog appears after the goto."""
        page = setup_page_with_files()
        dialog = ("dialog", "Files Settings")
        page.roles.add(dialog)
        page.hidden = {dialog}

        def opened(url):
            if url == FILES_HOME:
                page.hidden = set()

        page.on_goto = opened
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertIn("Opened the Files home", outcome.notes["f1"])
        self.assertIn("step-verifier judges this step", outcome.notes["f1"])
        self.assertEqual(outcome.evidence.get("f1", ()), ())

    def test_files_with_heading_is_needs_review(self):
        """Case (c): a Files heading appears after the goto."""
        page = setup_page_with_files()

        def opened(url):
            if url == FILES_HOME:
                page.body = "Files\nRecent Files\nMy Files"
                page.roles.add(("heading", "Files"))

        page.on_goto = opened
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertIn("Opened the Files home", outcome.notes["f1"])
        self.assertEqual(outcome.evidence.get("f1", ()), ())

    def test_files_with_url_change_is_needs_review(self):
        """Case (d): URL changes to include 'files' after the goto."""
        page = setup_page_with_files()

        def opened(url):
            if url == FILES_HOME:
                page.url = FILES_HOME
                page.page_title = "Files | Salesforce"

        page.on_goto = opened
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertIn("Opened the Files home", outcome.notes["f1"])
        self.assertEqual(outcome.evidence.get("f1", ()), ())

    def test_other_nav_click_still_passes(self):
        """A non-Files nav item turning current still passes."""
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Widgets")
        own = ("navlink", "Widgets nav")
        page.roles.add(own)
        page.tab_of[("text", "Widgets")] = own

        def react(kind, name):
            if name == "Widgets":
                page.aria_states[(*own, "aria-current")] = "page"

        page.on_click = react
        nav_step = step("t2", "Exercise 9: Widgets", "Task 2: Open widgets.", "1", "Click Widgets.")
        outcome = walk(page, nav_step, PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "PASS")
        self.assertEqual(outcome.evidence["t2"], ("t2:after-state:-Widgets-nav-item-selected",))


if __name__ == "__main__":
    unittest.main()
