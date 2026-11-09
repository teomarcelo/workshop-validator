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


# Flows list page
FLOWS_HTML = '''<!DOCTYPE html>
<html><head><title>Flows</title></head><body>
<h1>Flows</h1>
<button role="button" onclick="location.href='/lightning/setup/Flows/new'">New Flow</button>
</body></html>'''

# New Automation with exact Screen Flow and a look-alike, inside New Flow dialog
NEW_AUTOMATION_HTML = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<h1>Flows</h1>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option" onclick="location.href='/lightning/setup/Flows/create?type=screen'">Screen Flow<br><span style="color: gray;">Create a screen flow</span></div>
        <div role="option" onclick="location.href='/lightning/setup/Flows/create?type=template'">Screen Flow Template<br><span style="color: gray;">Use a template</span></div>
        <div role="option" onclick="location.href='/lightning/setup/Flows/create?type=auto'">Autolaunched Flow (No Trigger)<br><span style="color: gray;">Create an autolaunched flow</span></div>
    </div>
</div>
</body></html>'''

# Two exact Screen Flow tiles inside New Flow dialog
NEW_AUTOMATION_DUPLICATE = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<h1>Flows</h1>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option">Screen Flow<br><span style="color: gray;">First one</span></div>
        <div role="option">Screen Flow<br><span style="color: gray;">Second one</span></div>
    </div>
</div>
</body></html>'''

# Hidden Screen Flow tile (opacity 0, has innerText and bounding box but considered not visible by Playwright)
NEW_AUTOMATION_HIDDEN = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<h1>Flows</h1>
<div role="dialog" aria-label="New Flow">
    <h2 role="heading">New Flow</h2>
    <div role="listbox">
        <div role="option" style="opacity: 0;">Screen Flow<br>Hidden via opacity</div>
        <div role="option">Autolaunched Flow (No Trigger)<br>Visible</div>
    </div>
</div>
</body></html>'''

# Create button dialog
CREATE_DIALOG_HTML = '''<!DOCTYPE html>
<html><head><title>New Automation</title></head><body>
<h1>New Automation</h1>
<p>Screen Flow selected</p>
<button role="button" onclick="location.href='/builder_platform_interaction/flowBuilder.app'">Create</button>
</body></html>'''

# Flow Builder
BUILDER_HTML = '''<!DOCTYPE html>
<html><head><title>Flow Builder</title></head><body>
<builder_platform_interaction-canvas style="width: 800px; height: 600px; display: block;">
    <div>Flow Builder Canvas</div>
</builder_platform_interaction-canvas>
<button role="button">Toggle Toolbox</button>
</body></html>'''


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
                     "Browser smoke only (requires Chromium)")
class CreateFlowBrowserSmokeTests(unittest.TestCase):
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
        self.clicks = []

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

    def test_screen_flow_clicks_exact_tile_not_lookalike(self):
        """Screen Flow clicks the exact tile, never the look-alike template."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url:
                route.fulfill(status=200, body=NEW_AUTOMATION_HTML, content_type="text/html")
            elif "/lightning/setup/Flows/create" in url and "type=screen" in url:
                route.fulfill(status=200, body=CREATE_DIALOG_HTML, content_type="text/html")
            elif "/builder_platform_interaction/flowBuilder" in url:
                route.fulfill(status=200, body=BUILDER_HTML, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_HTML, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        # Should complete successfully (status NEEDS_REVIEW, clicked through to builder)
        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("manual verification", outcome.notes.get("s1", ""))
        # Verify we clicked the exact tile and reached the builder (not stuck at dialog)
        self.assertIn("flowBuilder", self.page.url)
        # Verify we didn't click the template tile
        self.assertNotIn("template", self.page.url.lower())

    def test_duplicate_exact_tiles_frozen_nothing_clicked(self):
        """Two exact Screen Flow tiles result in NEEDS_REVIEW frozen."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_AUTOMATION_DUPLICATE, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_HTML, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        self.assertEqual(outcome.statuses.get("s1"), "NEEDS_REVIEW")
        self.assertIn("Multiple tiles", outcome.notes.get("s1", ""))
        # Still on New Automation page, never clicked through
        self.assertIn("Flows", self.page.url)

    def test_hidden_exact_tile_not_clicked(self):
        """Hidden exact tile is not clicked."""
        def route_handler(route):
            url = route.request.url
            if "/lightning/setup/Flows/new" in url or "new" in url.lower():
                route.fulfill(status=200, body=NEW_AUTOMATION_HIDDEN, content_type="text/html")
            elif "/lightning/setup/Flows/home" in url or "/lightning/setup/Flows" in url:
                route.fulfill(status=200, body=FLOWS_HTML, content_type="text/html")
            else:
                route.fulfill(status=404, body="Not found")

        self.page.route("https://example.lightning.force.com/**", route_handler)
        self.page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        outcome = self.walk(self.page, (self.flow_step("Screen Flow"),))

        # Hidden tiles are not found by _option_lines (returns empty lines), so tile times out
        self.assertEqual(outcome.statuses.get("s1"), "UI_MISMATCH")
        self.assertIn("was not offered", outcome.notes.get("s1", ""))


if __name__ == "__main__":
    unittest.main()
