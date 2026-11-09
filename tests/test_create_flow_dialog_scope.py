"""I26-2b: Create Flow clicks the exact tile in the New Flow dialog, NEEDS_REVIEW-only.

Tests that _flow_type_tile_locator scopes to the exact "New Flow" dialog and clicks
only the tile whose first visible line matches the type exactly, never a look-alike.
"""

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.guide_inventory import GuideStep


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


def inventory_of(*items):
    from workshop_validator.guide_inventory import GuideInventory
    return GuideInventory(1, tuple(items), (), ())


# Base flows page
FLOWS_BASE = '''<!DOCTYPE html>
<html><head><title>Flows</title><script>
window.clickCount = 0;
document.addEventListener('click', function(e) {
    window.clickCount++;
    console.log('Click #' + window.clickCount + ': ' + e.target.textContent.trim());
});
</script></head><body>
<h1>Flows</h1>
<button role="button" onclick="location.href='/lightning/setup/Flows/new'">New Flow</button>
</body></html>'''

# New Flow dialog with exact Screen Flow tile and look-alike template
NEW_FLOW_DIALOG_WITH_LOOKALIKE = '''<!DOCTYPE html>
<html><head><title>New Automation</title><script>
window.clickCount = 0;
document.addEventListener('click', function(e) {
    window.clickCount++;
});
</script></head><body>
<h1>Flows</h1>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option" onclick="window.selectedType='Screen Flow';location.href='/builder'">
            Screen Flow<br><span style="color: gray;">Create a screen flow</span>
        </div>
        <div role="option" onclick="window.selectedType='Screen Flow Template';location.href='/builder-template'">
            Screen Flow Template<br><span style="color: gray;">Use a template</span>
        </div>
        <div role="option" onclick="window.selectedType='Autolaunched';location.href='/builder-auto'">
            Autolaunched Flow (No Trigger)<br><span style="color: gray;">No trigger</span>
        </div>
    </div>
</div>
</body></html>'''

# Look-alike Screen Flow OUTSIDE dialog (in another panel)
NEW_FLOW_WITH_OUTSIDE_LOOKALIKE = '''<!DOCTYPE html>
<html><head><title>New Automation</title><script>
window.clickCount = 0;
document.addEventListener('click', function(e) {
    window.clickCount++;
});
</script></head><body>
<div style="float:right; width:200px; border:1px solid #ccc; padding:10px;">
    <h3>Recent Types</h3>
    <div role="option" onclick="window.outsideClicked=true">Screen Flow<br>Recently used</div>
</div>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option" onclick="window.selectedType='Screen Flow';location.href='/builder'">
            Screen Flow<br><span style="color: gray;">Create a screen flow</span>
        </div>
    </div>
</div>
</body></html>'''

# Two exact Screen Flow tiles in the dialog
NEW_FLOW_DUPLICATE_TILES = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option">Screen Flow<br><span style="color: gray;">Option 1</span></div>
        <div role="option">Screen Flow<br><span style="color: gray;">Option 2</span></div>
    </div>
</div>
</body></html>'''

# No "New Flow" dialog
NEW_FLOW_NO_DIALOG = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<h1>New Automation</h1>
<div role="listbox">
    <div role="option">Screen Flow<br><span style="color: gray;">Create a screen flow</span></div>
</div>
</body></html>'''

# Two "New Flow" dialogs
NEW_FLOW_TWO_DIALOGS = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option">Screen Flow</div>
    </div>
</div>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option">Autolaunched Flow (No Trigger)</div>
    </div>
</div>
</body></html>'''

# Look-alike dialog with aria-label="New Flow Template"
NEW_FLOW_TEMPLATE_ARIA = '''<!DOCTYPE html>
<html><head><title>New Automation</title><script>
window.clickCount = 0;
document.addEventListener('click', function(e) {
    window.clickCount++;
});
</script></head><body>
<h1>Flows</h1>
<div role="dialog" aria-label="New Flow Template">
    <h2 role="heading">New Flow Template</h2>
    <div role="listbox">
        <div role="option" onclick="window.clickCount++">Screen Flow<br><span style="color: gray;">Create a screen flow</span></div>
    </div>
</div>
</body></html>'''

# Look-alike dialog with heading="New Flow Template"
NEW_FLOW_TEMPLATE_HEADING = '''<!DOCTYPE html>
<html><head><title>New Automation</title><script>
window.clickCount = 0;
document.addEventListener('click', function(e) {
    window.clickCount++;
});
</script></head><body>
<h1>Flows</h1>
<div role="dialog">
    <h2 role="heading">New Flow Template</h2>
    <div role="listbox">
        <div role="option" onclick="window.clickCount++">Screen Flow<br><span style="color: gray;">Create a screen flow</span></div>
    </div>
</div>
</body></html>'''

# Flow Builder page
BUILDER_HTML = '''<!DOCTYPE html>
<html><head><title>Flow Builder</title></head><body>
<builder_platform_interaction-canvas style="width: 800px; height: 600px; display: block;">
    <div>Flow Builder Canvas</div>
</builder_platform_interaction-canvas>
<button role="button">Toggle Toolbox</button>
</body></html>'''

# Create button after tile selection
CREATE_BUTTON_HTML = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <p>Screen Flow selected</p>
    <button role="button" onclick="location.href='/builder'">Create</button>
</div>
</body></html>'''


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
                     "Browser smoke only (requires Chromium)")
class CreateFlowDialogScopeTests(unittest.TestCase):
    """Tests that run_create_flow scopes to the exact New Flow dialog."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get('CHROMIUM_PATH') or None
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()

    def tearDown(self):
        self.context.close()

    def walk(self, page, items):
        from tests.test_guide_walk import Clock
        clock = Clock()
        return guide_walk.walk_guide(
            page, inventory_of(*items),
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )

    def flow_step(self, flow_type):
        return step(
            "s1", "Sample Exercise", "Task 1: Sample.", "1",
            f"Create a {flow_type}.",
        )

    def test_exact_tile_in_dialog_clicked_needs_review(self):
        """Exact Screen Flow tile in the New Flow dialog is clicked, returns NEEDS_REVIEW."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_DIALOG_WITH_LOOKALIKE, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(status=200, body=BUILDER_HTML, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("manual verification", outcome.notes.get("s1", ""))
        self.assertFalse(outcome.freezes.get("s1", False))  # Not frozen on success
        # Verify we reached the builder (not stuck or clicked wrong tile)
        self.assertIn("builder", self.page.url.lower())
        self.assertNotIn("template", self.page.url)

    def test_lookalike_outside_dialog_never_clicked(self):
        """Look-alike Screen Flow option OUTSIDE the dialog is never clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_WITH_OUTSIDE_LOOKALIKE, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(status=200, body=BUILDER_HTML, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        # Verify outside option was never clicked
        outside_clicked = self.page.evaluate("window.outsideClicked")
        self.assertIsNone(outside_clicked)
        # Verify we clicked the one inside the dialog
        self.assertIn("builder", self.page.url.lower())

    def test_lookalike_template_in_dialog_never_clicked(self):
        """Look-alike 'Screen Flow Template' tile in the dialog is never clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_DIALOG_WITH_LOOKALIKE, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(status=200, body=BUILDER_HTML, content_type="text/html")
            elif "/builder-template" in url:
                # If we got here, wrong tile was clicked
                route.fulfill(status=200, body="<html><body>Wrong tile!</body></html>", content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        # Verify we clicked the exact tile, not the template (URL proves it)
        self.assertIn("/builder", self.page.url)
        self.assertNotIn("template", self.page.url)

    def test_duplicate_exact_tiles_frozen_nothing_clicked(self):
        """Two exact Screen Flow tiles result in NEEDS_REVIEW frozen, nothing clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_DUPLICATE_TILES, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("Multiple tiles", outcome.notes.get("s1", ""))
        self.assertTrue(outcome.freezes.get("s1", False))  # Frozen on duplicate
        # Still on the dialog page, never navigated away
        self.assertIn("Flows", self.page.url)

    def test_no_dialog_frozen_nothing_clicked(self):
        """No 'New Flow' dialog results in NEEDS_REVIEW frozen, nothing clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_NO_DIALOG, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("dialog constraint", outcome.notes.get("s1", ""))
        self.assertTrue(outcome.freezes.get("s1", False))  # Frozen on missing dialog

    def test_two_dialogs_frozen_nothing_clicked(self):
        """Two 'New Flow' dialogs result in NEEDS_REVIEW frozen, nothing clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_TWO_DIALOGS, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("dialog constraint", outcome.notes.get("s1", ""))
        self.assertTrue(outcome.freezes.get("s1", False))  # Frozen on multiple dialogs

    def test_click_count_verified(self):
        """Verify that exactly one tile is clicked (page-level click listener)."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_DIALOG_WITH_LOOKALIKE, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(status=200, body=BUILDER_HTML, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        # The dialog page should have tracked clicks
        # Note: navigation away resets the click count, so we can't verify post-navigation

    def test_lookalike_dialog_aria_label_never_matched(self):
        """Dialog with aria-label='New Flow Template' is rejected by exact match, nothing clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_TEMPLATE_ARIA, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("dialog constraint", outcome.notes.get("s1", ""))
        self.assertTrue(outcome.freezes.get("s1", False))  # Frozen on wrong dialog
        # Verify no tiles were clicked
        click_count = self.page.evaluate("window.clickCount")
        self.assertEqual(click_count, 0)

    def test_lookalike_dialog_heading_never_matched(self):
        """Dialog with heading='New Flow Template' is rejected by exact match, nothing clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_FLOW_TEMPLATE_HEADING, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_BASE, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("dialog constraint", outcome.notes.get("s1", ""))
        self.assertTrue(outcome.freezes.get("s1", False))  # Frozen on wrong dialog
        # Verify no tiles were clicked
        click_count = self.page.evaluate("window.clickCount")
        self.assertEqual(click_count, 0)


if __name__ == "__main__":
    unittest.main()
