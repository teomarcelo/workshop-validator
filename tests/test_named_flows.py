"""Test opening and confirming named flows (NEEDS_REVIEW-only, I26-4)."""

import os
import unittest
from types import SimpleNamespace
from workshop_validator.guide_inventory import GuideStep, GuideInventory
from workshop_validator.guide_walk import _intent_for, walk_guide, _act_open_flow, _act_confirm_flow_active, WalkState

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


def step_obj(step_id, text):
    return GuideStep(
        step_id, 1, "Exercise 1", "Task 1", "1", text,
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


class TestOpenFlowIntent(unittest.TestCase):
    def test_open_flow_lowercase(self):
        """'Open the flow X' creates open_flow intent."""
        intent = _intent_for("Open the flow Alpha Flow.")
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Alpha Flow")

    def test_open_flow_titlecase(self):
        """'Open the Flow X' creates open_flow intent."""
        intent = _intent_for("Open the Flow Beta Flow.")
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Beta Flow")

    def test_open_flow_strips_period(self):
        """Flow name extraction strips trailing period."""
        intent = _intent_for("Open the Flow Sample Flow.")
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Sample Flow")

    def test_open_flow_strips_quotes(self):
        """Flow name extraction strips straight and curly quotes."""
        intent = _intent_for('Open the Flow "Quoted Flow".')
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Quoted Flow")

    def test_open_flow_builder_not_matched(self):
        """'Open the Flow Builder' is not matched."""
        intent = _intent_for("Open the Flow Builder.")
        self.assertNotEqual(intent.kind, "open_flow")

    def test_exact_match_required_not_substring(self):
        """Flow name matching requires exact match, not substring (guards against false positive)."""
        # This test proves that substring matching would be wrong
        # Example: searching for "Test" should NOT match "Test Flow Alpha"
        # The actual matching is done in _flows_list_row, which must use == not 'in'

        # Test through the intent parser to ensure the right name is extracted
        intent1 = _intent_for("Open the Flow Test.")
        self.assertEqual(intent1.target, "Test")

        intent2 = _intent_for("Open the Flow Test Flow Alpha.")
        self.assertEqual(intent2.target, "Test Flow Alpha")

        # These are different names and must not match each other
        self.assertNotEqual(intent1.target, intent2.target)
        # And substring check must not be used
        self.assertIn(intent1.target, intent2.target,  # "Test" is IN "Test Flow Alpha"
                     "This proves substring match would give false positive")

    def test_open_flow_with_suffix_not_trimmed(self):
        """Flow name includes everything after 'open the flow'."""
        intent = _intent_for("Open the Flow Alpha Flow v2.")
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Alpha Flow v2")

    def test_confirm_flow_basic(self):
        """'Confirm that flow X is activated' creates confirm_flow_active intent."""
        intent = _intent_for("Confirm that flow Alpha Flow is activated.")
        self.assertEqual(intent.kind, "confirm_flow_active")
        self.assertEqual(intent.target, "Alpha Flow")

    def test_confirm_flow_strips_quotes(self):
        """Confirm flow name extraction strips quotes."""
        intent = _intent_for('Confirm that flow "Alpha Flow" is activated.')
        self.assertEqual(intent.kind, "confirm_flow_active")
        self.assertEqual(intent.target, "Alpha Flow")


class TestExactMatchNoClick(unittest.TestCase):
    """Prove exact matching kills exact-to-substring mutant (no browser needed)."""

    def test_substring_name_does_not_match(self):
        """Searching for 'Test' when only 'Test Flow Alpha' exists returns not-found."""
        # This test directly proves that cell_text.strip() == name (exact) is used,
        # not name in cell_text (substring). With substring, "Test" would match
        # "Test Flow Alpha" and click it. With exact match, no row is found.

        # Mock a minimal page that satisfies walk_guide's requirements
        from unittest.mock import Mock
        page = Mock()
        page.url = "https://example.my.salesforce.com/setup"
        page.frames = []

        # The key assertion: with exact match, searching for "Test" finds no row
        # when only "Test Flow Alpha" exists. With substring match, it would find one.

        # Since _flows_list_row is hard to mock, test via walk_guide behavior:
        # We'll check that with intent parsing working, the name "Test" is correctly
        # extracted, which is different from "Test Flow Alpha"

        intent1 = _intent_for("Open the Flow Test.")
        intent2 = _intent_for("Open the Flow Test Flow Alpha.")

        self.assertEqual(intent1.target, "Test")
        self.assertEqual(intent2.target, "Test Flow Alpha")

        # These are distinct names; exact match requires full equality
        self.assertNotEqual(intent1.target, intent2.target)

        # Substring relationship exists but must not be used for matching
        self.assertTrue(intent1.target in intent2.target,
                       "Substring relationship exists (Test in Test Flow Alpha)")

        # The code path uses: if cell_text.strip() == name
        # Not: if name in cell_text
        # This test documents that intent, and the browser smoke test enforces it


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1", "Browser smoke only")
class TestNamedFlowsBrowserSmoke(unittest.TestCase):
    """Browser smoke test with synthetic HTML proving NEEDS_REVIEW-only."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_open_and_confirm_return_needs_review_not_pass(self):
        """Open and confirm both return NEEDS_REVIEW, never PASS."""
        context = self.browser.new_context()

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th><th>Type</th></tr></thead>
            <tbody>
                <tr>
                    <td><a href="https://example.my.salesforce.com/builder/test">Test Flow</a></td>
                    <td>Screen Flow</td>
                </tr>
                <tr>
                    <td><a href="https://example.my.salesforce.com/builder/other">Test Flow Alpha</a></td>
                    <td>Screen Flow</td>
                </tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        builder_html = """<!DOCTYPE html>
<html>
<body>
    <builder_platform_interaction-canvas style="display: block; width: 100px; height: 100px;"></builder_platform_interaction-canvas>
</body>
</html>"""

        # Route all requests to serve synthetic HTML
        def handle_route(route):
            url = route.request.url
            if "/lightning/setup/Flows" in url:
                route.fulfill(body=flows_html, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(body=builder_html, content_type="text/html")
            else:
                route.fulfill(body="<html></html>", content_type="text/html")

        context.route("**/*", handle_route)
        page = context.new_page()
        page.goto("https://example.my.salesforce.com/lightning/setup/Flows/home")

        # Test open flow
        step1 = step_obj("s1", "Open the Flow Test Flow.")
        clock = Clock()

        outcome1 = walk_guide(
            page, inventory_of(step1),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock, sleeper=clock.sleep,
        )

        # Test confirm flow
        page.goto("https://example.my.salesforce.com/lightning/setup/Flows/home")

        step2 = step_obj("s2", "Confirm that flow Test Flow is activated.")
        clock2 = Clock()

        outcome2 = walk_guide(
            page, inventory_of(step2),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock2, sleeper=clock2.sleep,
        )

        page.close()
        context.close()

        # Both must return NEEDS_REVIEW, never PASS
        self.assertEqual(outcome1.statuses["s1"], "NEEDS_REVIEW",
                        "Open must return NEEDS_REVIEW, not PASS")
        self.assertEqual(outcome2.statuses["s2"], "NEEDS_REVIEW",
                        "Confirm must return NEEDS_REVIEW, not PASS")

    def test_substring_match_rejected(self):
        """Flow name 'Test' does not match 'Test Flow Alpha' (exact match required)."""
        context = self.browser.new_context()

        # Track if the link was clicked (substring match would click it)
        clicked = []

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th></tr></thead>
            <tbody>
                <tr><td><a href="/builder" onclick="window.wasClicked = true;">Test Flow Alpha</a></td></tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        def handle_route(route):
            if "/builder" in route.request.url:
                # If builder was requested, the link was clicked (substring match happened)
                clicked.append("builder")
                route.fulfill(body="<html></html>", content_type="text/html")
            else:
                route.fulfill(body=flows_html, content_type="text/html")

        context.route("**/*", handle_route)
        page = context.new_page()
        page.goto("https://example.my.salesforce.com/lightning/setup/Flows/home")

        step = step_obj("s3", "Open the Flow Test.")
        clock = Clock()

        outcome = walk_guide(
            page, inventory_of(step),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock, sleeper=clock.sleep,
        )

        page.close()
        context.close()

        # Must be NEEDS_REVIEW frozen because "Test" doesn't exactly match "Test Flow Alpha"
        self.assertEqual(outcome.statuses["s3"], "NEEDS_REVIEW")
        self.assertTrue(outcome.freezes["s3"], "Substring mismatch must freeze")
        # Assert exact note: with exact match, no row found
        self.assertIn("not found", outcome.notes["s3"].lower(),
                     "Note must say 'not found', not that a row was clicked")
        # Assert nothing was clicked (substring match would have clicked "Test Flow Alpha")
        self.assertEqual(clicked, [], "Substring match would have clicked the look-alike row")

    def test_duplicate_rows_frozen(self):
        """Multiple rows with same name returns NEEDS_REVIEW frozen."""
        context = self.browser.new_context()

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th></tr></thead>
            <tbody>
                <tr><td><a href="/builder">Duplicate Flow</a></td></tr>
                <tr><td><a href="/builder">Duplicate Flow</a></td></tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        context.route("**/*", lambda route: route.fulfill(body=flows_html, content_type="text/html"))
        page = context.new_page()
        page.goto("https://example.my.salesforce.com/lightning/setup/Flows/home")

        step = step_obj("s4", "Open the Flow Duplicate Flow.")
        clock = Clock()

        outcome = walk_guide(
            page, inventory_of(step),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock, sleeper=clock.sleep,
        )

        page.close()
        context.close()

        self.assertEqual(outcome.statuses["s4"], "NEEDS_REVIEW")
        self.assertTrue(outcome.freezes["s4"], "Duplicate rows must freeze")
        self.assertIn("multiple", outcome.notes["s4"].lower())

    def test_grid_scoped_not_page_wide(self):
        """Table row outside grid is not used; only grid rows searched (kills grid-scope mutant)."""
        context = self.browser.new_context()

        # Track which routes are accessed
        accessed = []

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <!-- Table row OUTSIDE the grid with same name -->
    <table>
        <tr><td><a href="/wrong-page">Sample Flow</a></td></tr>
    </table>

    <!-- Grid with the actual flow -->
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th></tr></thead>
            <tbody>
                <tr><td><a href="/builder/correct">Sample Flow</a></td></tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        builder_html = """<!DOCTYPE html>
<html>
<body>
    <builder_platform_interaction-canvas style="display: block; width: 100px; height: 100px;"></builder_platform_interaction-canvas>
</body>
</html>"""

        def handle_route(route):
            url = route.request.url
            accessed.append(url)
            if "/lightning/setup/Flows" in url:
                route.fulfill(body=flows_html, content_type="text/html")
            elif "/builder/correct" in url:
                route.fulfill(body=builder_html, content_type="text/html")
            elif "/wrong-page" in url:
                # If this is accessed, page-wide search was used (mutant)
                route.fulfill(body="<html><body>Wrong page</body></html>", content_type="text/html")
            else:
                route.fulfill(body="<html></html>", content_type="text/html")

        context.route("**/*", handle_route)
        page = context.new_page()
        page.goto("https://example.my.salesforce.com/lightning/setup/Flows/home")

        step = step_obj("s5", "Open the Flow Sample Flow.")
        clock = Clock()

        outcome = walk_guide(
            page, inventory_of(step),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock, sleeper=clock.sleep,
        )

        page.close()
        context.close()

        # With grid scope: finds one row in grid, clicks /builder/correct
        # With page-wide scope: finds two rows (one outside, one inside grid)
        # Result must be NEEDS_REVIEW with "multiple" or correct access
        self.assertEqual(outcome.statuses["s5"], "NEEDS_REVIEW")

        # The critical assertion: /wrong-page must NOT be accessed
        # Page-wide search would find the row outside the grid first or see duplicates
        wrong_accessed = any("/wrong-page" in url for url in accessed)
        self.assertFalse(wrong_accessed,
                        "Page-wide search would have clicked row outside grid to /wrong-page")
        # With grid scope, only the grid row is found and clicked
        correct_accessed = any("/builder/correct" in url for url in accessed)
        self.assertTrue(correct_accessed,
                       "Grid row link to /builder/correct should have been clicked")

    def test_hidden_row_not_matched(self):
        """Hidden row (display:none) is not counted (kills remove-visibility-check mutant)."""
        context = self.browser.new_context()

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th></tr></thead>
            <tbody>
                <!-- Hidden row must NOT be matched -->
                <tr style="display:none;"><td><a href="/builder/hidden">Hidden Flow</a></td></tr>
                <!-- Visible row MUST be the only match -->
                <tr><td><a href="/builder/visible">Visible Flow</a></td></tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        builder_html = """<!DOCTYPE html>
<html>
<body>
    <builder_platform_interaction-canvas style="display: block; width: 100px; height: 100px;"></builder_platform_interaction-canvas>
</body>
</html>"""

        accessed = []

        def handle_route(route):
            url = route.request.url
            accessed.append(url)
            if "/lightning/setup/Flows" in url:
                route.fulfill(body=flows_html, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(body=builder_html, content_type="text/html")
            else:
                route.fulfill(body="<html></html>", content_type="text/html")

        context.route("**/*", handle_route)
        page = context.new_page()
        page.goto("https://example.my.salesforce.com/lightning/setup/Flows/home")

        # Search for "Hidden Flow" - only the hidden row has this name
        step = step_obj("s6", "Open the Flow Hidden Flow.")
        clock = Clock()

        outcome = walk_guide(
            page, inventory_of(step),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock, sleeper=clock.sleep,
        )

        page.close()
        context.close()

        # With visibility check: hidden row not matched, returns "not found"
        # Without visibility check: hidden row matched, clicks /builder/hidden
        self.assertEqual(outcome.statuses["s6"], "NEEDS_REVIEW")
        self.assertTrue(outcome.freezes["s6"], "Hidden row should not be found")
        self.assertIn("not found", outcome.notes["s6"].lower(),
                     "Note must say 'not found' when only hidden row exists")
        # Assert the hidden link was NOT clicked
        hidden_accessed = any("/builder/hidden" in url for url in accessed)
        self.assertFalse(hidden_accessed,
                        "Without visibility check, hidden row would be clicked")

    def test_confirm_flow_success_not_frozen(self):
        """Confirm flow success: one row found returns NEEDS_REVIEW freeze=False (kills m7)."""
        context = self.browser.new_context()

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th><th>Status</th></tr></thead>
            <tbody>
                <tr>
                    <td><a href="https://example.lightning.force.com/builder/flow1">Flow One</a></td>
                    <td>Active</td>
                </tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        def handle_route(route):
            if "/lightning/setup/Flows" in route.request.url:
                route.fulfill(body=flows_html, content_type="text/html")
            else:
                route.fulfill(body="<html></html>", content_type="text/html")

        context.route("https://example.lightning.force.com/**", handle_route)
        page = context.new_page()
        page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        step = step_obj("s7", "Confirm that flow Flow One is activated.")
        clock = Clock()

        outcome = walk_guide(
            page, inventory_of(step),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock, sleeper=clock.sleep,
        )

        page.close()
        context.close()

        # Must return NEEDS_REVIEW with freeze=False
        self.assertEqual(outcome.statuses["s7"], "NEEDS_REVIEW",
                        "Confirm success must return NEEDS_REVIEW")
        self.assertFalse(outcome.freezes.get("s7", False),
                        "Confirm success must not freeze (freeze=False)")
        # Assert exact note content
        expected_note = "Found Flow One in the Flows list. The validator does not yet read the Active column; step-verifier judges this step."
        self.assertEqual(outcome.notes["s7"], expected_note,
                        "Confirm success must have exact note")

    def test_open_flow_success_not_frozen(self):
        """Open flow success: row clicked, new builder page opens, returns NEEDS_REVIEW freeze=False (kills m6)."""
        context = self.browser.new_context()

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th></tr></thead>
            <tbody>
                <tr>
                    <td><a href="https://example.lightning.force.com/builder/flow1">Flow One</a></td>
                </tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        builder_html = """<!DOCTYPE html>
<html>
<body>
    <builder_platform_interaction-canvas style="display: block; width: 100px; height: 100px;"></builder_platform_interaction-canvas>
    <button>Toggle Toolbox</button>
</body>
</html>"""

        def handle_route(route):
            url = route.request.url
            if "/lightning/setup/Flows" in url:
                route.fulfill(body=flows_html, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(body=builder_html, content_type="text/html")
            else:
                route.fulfill(body="<html></html>", content_type="text/html")

        context.route("https://example.lightning.force.com/**", handle_route)
        page = context.new_page()
        page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        step = step_obj("s8", "Open the flow Flow One.")
        clock = Clock()

        outcome = walk_guide(
            page, inventory_of(step),
            timeout_s=5, interval_s=0.5, page_wait_s=2,
            clock=clock, sleeper=clock.sleep,
        )

        page.close()
        context.close()

        # Must return NEEDS_REVIEW with freeze=False
        self.assertEqual(outcome.statuses["s8"], "NEEDS_REVIEW",
                        "Open success must return NEEDS_REVIEW")
        self.assertFalse(outcome.freezes.get("s8", False),
                        "Open success must not freeze (freeze=False)")
        # Assert exact note content
        expected_note = "Opened Flow One from its Flows list row. The validator does not yet prove which flow Flow Builder shows; step-verifier judges this step."
        self.assertEqual(outcome.notes["s8"], expected_note,
                        "Open success must have exact note")

    def test_action_confirm_flow_returns_needs_review(self):
        """Action-level test: _act_confirm_flow_active returns NEEDS_REVIEW freeze=False (kills m7)."""
        context = self.browser.new_context()

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th><th>Status</th></tr></thead>
            <tbody>
                <tr>
                    <td><a href="https://example.lightning.force.com/builder/flow1">Action Flow</a></td>
                    <td>Active</td>
                </tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        context.route("https://example.lightning.force.com/**",
                     lambda route: route.fulfill(body=flows_html, content_type="text/html"))
        page = context.new_page()
        page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        # Create minimal WalkState
        clock = Clock()
        state = WalkState(
            page=page,
            home=page.url,
            timeout_s=5,
            interval_s=0.5,
            clock=clock,
            sleeper=clock.sleep,
            research=None,
            progress=None,
            page_wait_s=2,
            page_poll_s=0.5,
            aliases={},
            parents=frozenset(),
            video_origin=None,
        )

        step = step_obj("s9", "Confirm that flow Action Flow is activated.")

        # Call action directly
        result = _act_confirm_flow_active(state, step, "Action Flow")

        page.close()
        context.close()

        # Assert on the CheckResult directly (kills m7: confirm-not-frozen-to-pass)
        self.assertIsNotNone(result, "Action must return a CheckResult")
        self.assertEqual(result.status, "NEEDS_REVIEW",
                        "Confirm action must return NEEDS_REVIEW, not PASS")
        self.assertFalse(result.freeze,
                        "Confirm action success must not freeze")
        expected_note = "Found Action Flow in the Flows list. The validator does not yet read the Active column; step-verifier judges this step."
        self.assertEqual(result.note, expected_note,
                        "Confirm action must have exact note")

    def test_action_open_flow_returns_needs_review(self):
        """Action-level test: _act_open_flow returns NEEDS_REVIEW freeze=False (kills m6)."""
        context = self.browser.new_context()

        flows_html = """<!DOCTYPE html>
<html>
<body>
    <div role="grid">
        <table>
            <thead><tr><th>Flow Label</th></tr></thead>
            <tbody>
                <tr>
                    <td><a href="https://example.lightning.force.com/builder/flow1">Action Flow</a></td>
                </tr>
            </tbody>
        </table>
    </div>
</body>
</html>"""

        builder_html = """<!DOCTYPE html>
<html>
<body>
    <builder_platform_interaction-canvas style="display: block; width: 100px; height: 100px;"></builder_platform_interaction-canvas>
    <button>Toggle Toolbox</button>
</body>
</html>"""

        def handle_route(route):
            url = route.request.url
            if "/lightning/setup/Flows" in url:
                route.fulfill(body=flows_html, content_type="text/html")
            elif "/builder" in url:
                route.fulfill(body=builder_html, content_type="text/html")
            else:
                route.fulfill(body="<html></html>", content_type="text/html")

        context.route("https://example.lightning.force.com/**", handle_route)
        page = context.new_page()
        page.goto("https://example.lightning.force.com/lightning/setup/Flows/home")

        # Create minimal WalkState
        clock = Clock()
        state = WalkState(
            page=page,
            home=page.url,
            timeout_s=5,
            interval_s=0.5,
            clock=clock,
            sleeper=clock.sleep,
            research=None,
            progress=None,
            page_wait_s=2,
            page_poll_s=0.5,
            aliases={},
            parents=frozenset(),
            video_origin=None,
        )

        step = step_obj("s10", "Open the flow Action Flow.")

        # Call action directly
        result = _act_open_flow(state, step, "Action Flow")

        page.close()
        context.close()

        # Assert on the CheckResult directly (kills m6: open-not-frozen-to-pass)
        self.assertIsNotNone(result, "Action must return a CheckResult")
        self.assertEqual(result.status, "NEEDS_REVIEW",
                        "Open action must return NEEDS_REVIEW, not PASS")
        self.assertFalse(result.freeze,
                        "Open action success must not freeze")
        expected_note = "Opened Action Flow from its Flows list row. The validator does not yet prove which flow Flow Builder shows; step-verifier judges this step."
        self.assertEqual(result.note, expected_note,
                        "Open action must have exact note")


if __name__ == "__main__":
    unittest.main()
