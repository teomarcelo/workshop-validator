import os
import unittest
from types import SimpleNamespace
from workshop_validator import guide_walk
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import walk_guide

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


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

    def sleep(self, seconds):
        self.now += seconds


class Page:
    def __init__(self, url, html=""):
        self.url = url
        self.html = html
        self.events = []
        self.reads = 0

    def content(self):
        self.reads += 1
        return self.html

    @property
    def frames(self):
        return [self]


class ConfirmFlowActiveTests(unittest.TestCase):
    def walk(self, page, items):
        clock = Clock()
        return walk_guide(
            page, inventory_of(*items),
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )

    def test_flow_active_passes_with_already_set_evidence(self):
        """Flow row shows Active: PASS with already-set evidence."""
        page = Page("https://synthetic.example.com/lightning/setup/Flows/home")
        # Synthetic HTML with flow row showing Active status
        page.html = """
        <table>
          <thead><tr><th>Flow Label</th><th>Status</th></tr></thead>
          <tbody>
            <tr>
              <td>Sample Flow One</td>
              <td>Active</td>
            </tr>
          </tbody>
        </table>
        """
        item = step(
            "flow1", "Task 1: Flows", "Check flows", "1",
            'Confirm that flow "Sample Flow One" is activated.',
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["flow1"], "PASS")
        self.assertIn("already", outcome.notes["flow1"].lower())
        self.assertIn("active", outcome.notes["flow1"].lower())
        # No clicks should happen
        self.assertEqual(page.events, [])

    def test_flow_inactive_fails(self):
        """Flow row shows Inactive: FUNCTIONAL_FAIL."""
        page = Page("https://synthetic.example.com/lightning/setup/Flows/home")
        page.html = """
        <table>
          <tr><td>Sample Flow Two</td><td>Inactive</td></tr>
        </table>
        """
        item = step(
            "flow2", "Task 1: Flows", "Check flows", "1",
            'Confirm that flow "Sample Flow Two" is activated.',
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["flow2"], "FUNCTIONAL_FAIL")
        self.assertIn("not activated", outcome.notes["flow2"])
        # No clicks should happen
        self.assertEqual(page.events, [])

    def test_flow_draft_fails(self):
        """Flow row shows Draft: FUNCTIONAL_FAIL."""
        page = Page("https://synthetic.example.com/lightning/setup/Flows/home")
        page.html = """
        <table>
          <tr><td>Sample Flow Three</td><td>Draft</td></tr>
        </table>
        """
        item = step(
            "flow3", "Task 1: Flows", "Check flows", "1",
            'Confirm that flow "Sample Flow Three" is activated.',
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["flow3"], "FUNCTIONAL_FAIL")
        # No clicks should happen
        self.assertEqual(page.events, [])

    def test_duplicate_flow_names_needs_review(self):
        """Two rows with the same flow label: NEEDS_REVIEW."""
        page = Page("https://synthetic.example.com/lightning/setup/Flows/home")
        page.html = """
        <table>
          <tr><td>Duplicate Flow</td><td>Active</td></tr>
          <tr><td>Duplicate Flow</td><td>Inactive</td></tr>
        </table>
        """
        item = step(
            "flow4", "Task 1: Flows", "Check flows", "1",
            'Confirm that flow "Duplicate Flow" is activated.',
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["flow4"], "NEEDS_REVIEW")
        self.assertIn("multiple", outcome.notes["flow4"].lower())
        # No clicks should happen
        self.assertEqual(page.events, [])

    def test_flow_not_found_needs_review(self):
        """Flow not visible on the page: NEEDS_REVIEW."""
        page = Page("https://synthetic.example.com/lightning/setup/Flows/home")
        page.html = """
        <table>
          <tr><td>Different Flow</td><td>Active</td></tr>
        </table>
        """
        item = step(
            "flow5", "Task 1: Flows", "Check flows", "1",
            'Confirm that flow "Missing Flow" is activated.',
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["flow5"], "NEEDS_REVIEW")
        self.assertIn("not found", outcome.notes["flow5"])
        # No clicks should happen
        self.assertEqual(page.events, [])

    def test_active_word_in_header_does_not_count(self):
        """Word 'Active' in column header doesn't count as flow being active."""
        page = Page("https://synthetic.example.com/lightning/setup/Flows/home")
        page.html = """
        <table>
          <thead><tr><th>Flow Label</th><th>Active</th></tr></thead>
          <tbody>
            <tr><td>Header Test Flow</td><td>Inactive</td></tr>
          </tbody>
        </table>
        """
        item = step(
            "flow6", "Task 1: Flows", "Check flows", "1",
            'Confirm that flow "Header Test Flow" is activated.',
        )
        outcome = self.walk(page, (item,))
        # Should fail because the flow is actually Inactive
        self.assertEqual(outcome.statuses["flow6"], "FUNCTIONAL_FAIL")
        # No clicks should happen
        self.assertEqual(page.events, [])

    def test_nothing_clicked_in_any_case(self):
        """Verify that nothing is clicked regardless of the outcome."""
        page = Page("https://synthetic.example.com/lightning/setup/Flows/home")
        page.html = """
        <table>
          <tr><td>Test Flow</td><td>Active</td></tr>
        </table>
        """
        page.clicks = []

        # Mock click method to track clicks
        original_locator = page.locator if hasattr(page, 'locator') else None

        def track_locator(selector):
            loc = SimpleNamespace()
            loc.click = lambda **kwargs: page.clicks.append(selector)
            loc.count = lambda: 0
            return loc

        page.locator = track_locator

        item = step(
            "flow7", "Task 1: Flows", "Check flows", "1",
            'Confirm that flow "Test Flow" is activated.',
        )
        self.walk(page, (item,))
        # Verify no clicks happened
        self.assertEqual(page.clicks, [])


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class ConfirmFlowActiveBrowserTests(unittest.TestCase):
    def test_flow_active_in_real_browser(self):
        """Real browser test: flow active passes."""
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()

            # Serve synthetic HTML with proper structure
            html = """
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"></head>
            <body>
                <div>
                    <table>
                        <thead><tr><th>Flow Label</th><th>Status</th></tr></thead>
                        <tbody>
                            <tr>
                                <td>Browser Test Flow</td>
                                <td>Active</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </body>
            </html>
            """
            # Use a Salesforce-like URL
            page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")
            page.set_content(html)
            # Wait for content to be fully loaded
            page.wait_for_timeout(100)

            # Create step
            item = step(
                "browser1", "Task 1: Flows", "Check flows", "1",
                'Confirm that flow "Browser Test Flow" is activated.',
            )

            # Run the walk
            clock = Clock()
            board = walk_guide(
                page, inventory_of(item),
                timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
            )

            # Verify PASS
            self.assertEqual(board.statuses["browser1"], "PASS", f"Note: {board.notes.get('browser1', 'no note')}")
            self.assertIn("active", board.notes["browser1"].lower())

            context.close()
            browser.close()

    def test_duplicate_flow_in_real_browser(self):
        """Real browser test: duplicate flow names."""
        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()

            html = """
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"></head>
            <body>
                <div>
                    <table>
                        <tbody>
                            <tr>
                                <td>Duplicate Name</td>
                                <td>Active</td>
                            </tr>
                            <tr>
                                <td>Duplicate Name</td>
                                <td>Inactive</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
            </body>
            </html>
            """
            page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")
            page.set_content(html)
            page.wait_for_timeout(100)

            item = step(
                "browser2", "Task 1: Flows", "Check flows", "1",
                'Confirm that flow "Duplicate Name" is activated.',
            )

            clock = Clock()
            board = walk_guide(
                page, inventory_of(item),
                timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
            )

            # Verify NEEDS_REVIEW for duplicates
            self.assertEqual(board.statuses["browser2"], "NEEDS_REVIEW", f"Note: {board.notes.get('browser2', 'no note')}")
            self.assertIn("multiple", board.notes["browser2"].lower())

            context.close()
            browser.close()


if __name__ == "__main__":
    unittest.main()
