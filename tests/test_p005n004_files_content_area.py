"""P005N004: Files tab false PASS fix. Nav chip visible but content area not loaded
must be NEEDS_REVIEW, not PASS. Synthetic pages only."""
import unittest

from tests.test_click_polling import PollClock, clicks, walk
from tests.test_guide_walk import Page, step

SETUP = "https://example.lightning.force.com/lightning/setup/SomePage/home"
FILES_HOME = "https://example.lightning.force.com/lightning/o/ContentDocument/home"
NAV_CURRENT = "one-app-nav-bar [aria-current='page']"


def files_step():
    return step("f1", "Exercise 5: Files", "Task 1: Open files.", "1", "Click the Files tab.")


def files_page_with_content(nav_item_name="Files", content_heading_visible=True):
    """Setup page with Files tab; opening Files home makes nav item current and shows content."""
    page = Page(SETUP, body="Personal Information\nFiles")
    nav_item = ("navlink", nav_item_name)
    page.roles.add(nav_item)
    page.css = {NAV_CURRENT: []}

    # Add Files content area heading (role=heading outside the nav bar)
    content_heading = ("heading", "Files Home")
    page.roles.add(content_heading)
    if not content_heading_visible:
        page.hidden.add(content_heading)

    def opened(url):
        if "ContentDocument" in url:
            page.css[NAV_CURRENT] = [nav_item]

    page.on_goto = opened
    return page


class FilesContentAreaGuardTests(unittest.TestCase):
    def test_files_chip_visible_but_content_area_not_loaded_is_needs_review(self):
        """Guard: Files nav chip visible but no content area heading → NEEDS_REVIEW (not PASS)."""
        page = files_page_with_content(content_heading_visible=False)
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("f1", ()), ())
        self.assertIn("Clicked element:", outcome.notes["f1"])
        self.assertIn(("goto", FILES_HOME), page.events)
        self.assertEqual(clicks(page), [("click", "text", "Files")])

    def test_files_chip_visible_and_content_area_showing_passes(self):
        """Positive: Files nav chip visible and content area heading visible → PASS."""
        page = files_page_with_content(content_heading_visible=True)
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "PASS")
        self.assertEqual(outcome.evidence["f1"], ("f1:after-state:-files-nav-item-current",))
        self.assertIn(("goto", FILES_HOME), page.events)
        self.assertEqual(clicks(page), [("click", "text", "Files")])

    def test_nav_chip_visible_no_content_heading_at_all_is_needs_review(self):
        """Guard: Nav chip visible but no Files heading element in the page → NEEDS_REVIEW."""
        page = Page(SETUP, body="Personal Information\nFiles")
        nav_item = ("navlink", "Files")
        page.roles.add(nav_item)
        page.css = {NAV_CURRENT: []}
        # No content heading at all

        def opened(url):
            if "ContentDocument" in url:
                page.css[NAV_CURRENT] = [nav_item]

        page.on_goto = opened
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("f1", ()), ())

    def test_content_heading_with_files_in_compound_name_passes(self):
        """Positive: Content heading with 'files' as part of a longer name → PASS."""
        page = Page(SETUP, body="Personal Information\nFiles")
        nav_item = ("navlink", "Files")
        page.roles.add(nav_item)
        page.css = {NAV_CURRENT: []}

        # Content heading with "files" in the text
        content_heading = ("heading", "All Files")
        page.roles.add(content_heading)

        def opened(url):
            if "ContentDocument" in url:
                page.css[NAV_CURRENT] = [nav_item]

        page.on_goto = opened
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "PASS")
        self.assertEqual(outcome.evidence["f1"], ("f1:after-state:-files-nav-item-current",))


class FilesTabRegressionTests(unittest.TestCase):
    def test_files_nav_item_already_current_before_click_is_not_evidence(self):
        """Regression: Nav item already current before click → NEEDS_REVIEW (unchanged)."""
        page = files_page_with_content()
        # Make nav item current before the click
        page.css[NAV_CURRENT] = [("navlink", "Files")]
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
        self.assertIn("Clicked element:", outcome.notes["f1"])

    def test_files_nav_item_with_wrong_name_is_not_evidence(self):
        """Regression: Nav item shows wrong name → NEEDS_REVIEW (unchanged)."""
        page = files_page_with_content(nav_item_name="Home")
        outcome = walk(page, files_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")


if __name__ == "__main__":
    unittest.main()
