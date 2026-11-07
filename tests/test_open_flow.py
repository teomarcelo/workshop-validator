"""Test opening a named flow from the Flows list page."""

import os
import unittest
from unittest.mock import patch, MagicMock, Mock
from types import SimpleNamespace
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import _intent_for, _act_open_flow, WalkState


def _step(text):
    return GuideStep(
        "test-1", 1, "Exercise 1", "Task 1", "1", text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


class TestOpenFlowIntent(unittest.TestCase):
    def test_intent_for_open_flow_lowercase(self):
        """'Open the flow X' creates open_flow intent."""
        intent = _intent_for("Open the flow Demo Flow Alpha.")
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Demo Flow Alpha")

    def test_intent_for_open_flow_titlecase(self):
        """'Open the Flow X' creates open_flow intent."""
        intent = _intent_for("Open the Flow Demo Flow Beta.")
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Demo Flow Beta")

    def test_intent_for_open_flow_strips_period(self):
        """Flow name extraction strips trailing period."""
        intent = _intent_for("Open the Flow Sample Flow.")
        self.assertEqual(intent.kind, "open_flow")
        self.assertEqual(intent.target, "Sample Flow")

    def test_intent_for_open_flow_with_suffix(self):
        """Flow name includes everything after 'open the flow', including 'in X'."""
        intent = _intent_for("Open the Flow My Flow in Opportunity Wizard.")
        self.assertEqual(intent.kind, "open_flow")
        # Per brief: do not guess at trimming "in <app>" suffixes
        self.assertEqual(intent.target, "My Flow in Opportunity Wizard")


class TestOpenFlowAction(unittest.TestCase):
    def _mock_page(self, url="https://example.lightning.force.com/setup/flows"):
        """Create a mock page with common methods."""
        page = Mock()
        page.url = url
        page.frames = []

        def get_by_role(role, name=None, exact=True):
            loc = Mock()
            loc.count = Mock(return_value=1)
            loc.first = Mock(return_value=loc)
            loc.click = Mock()
            return loc

        page.get_by_role = get_by_role
        return page

    @patch('workshop_validator.guide_walk._read_builder_flow_label')
    @patch('workshop_validator.guide_walk._focus_builder')
    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_passes_when_builder_shows_matching_label(
        self, mock_roots, mock_count, mock_focus, mock_read_label
    ):
        """Opening a flow passes when builder's label matches the guide's name."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Test Flow Alpha.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 1
        mock_focus.return_value = True
        mock_read_label.return_value = "Test Flow Alpha"

        result = _act_open_flow(state, step, "Test Flow Alpha", opened)

        self.assertEqual(result.status, "PASS")
        self.assertIn("Test Flow Alpha", result.note)
        self.assertTrue(opened["done"])

    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_needs_review_when_duplicate_rows(self, mock_roots, mock_count):
        """Two rows with the same label returns NEEDS_REVIEW, no click."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Duplicate Name.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 2  # Two matches

        result = _act_open_flow(state, step, "Duplicate Name", opened)

        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("Found 2 flows", result.note)
        self.assertFalse(opened["done"])  # No click happened

    @patch('workshop_validator.guide_walk._read_builder_flow_label')
    @patch('workshop_validator.guide_walk._focus_builder')
    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_needs_review_when_different_flow_opens(
        self, mock_roots, mock_count, mock_focus, mock_read_label
    ):
        """Builder label differs from guide's name returns NEEDS_REVIEW."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Expected Flow.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 1
        mock_focus.return_value = True
        mock_read_label.return_value = "Different Flow"  # Mismatch

        result = _act_open_flow(state, step, "Expected Flow", opened)

        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("different flow", result.note.lower())
        self.assertIn("Expected Flow", result.note)
        self.assertIn("Different Flow", result.note)

    @patch('workshop_validator.guide_walk.page_is_loading')
    @patch('workshop_validator.guide_walk._read_builder_flow_label')
    @patch('workshop_validator.guide_walk._focus_builder')
    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_needs_review_when_builder_label_unreadable(
        self, mock_roots, mock_count, mock_focus, mock_read_label, mock_loading
    ):
        """Builder opens but no label readable returns NEEDS_REVIEW."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Test Flow.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 1
        mock_focus.return_value = True
        mock_read_label.return_value = None  # Unreadable
        mock_loading.return_value = False  # Not loading

        result = _act_open_flow(state, step, "Test Flow", opened)

        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertIn("could not be read", result.note)

    @patch('workshop_validator.guide_walk.page_is_loading')
    @patch('workshop_validator.guide_walk._read_builder_flow_label')
    @patch('workshop_validator.guide_walk._focus_builder')
    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_polls_when_builder_still_loading(
        self, mock_roots, mock_count, mock_focus, mock_read_label, mock_loading
    ):
        """Builder still loading returns None for StillLoading poll."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Test Flow.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 1
        mock_focus.return_value = True
        mock_read_label.return_value = None
        mock_loading.return_value = True  # Still loading

        result = _act_open_flow(state, step, "Test Flow", opened)

        self.assertIsNone(result)  # StillLoading

    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_polls_when_flow_not_found(self, mock_roots, mock_count):
        """Flow not in list returns None for StillLoading poll."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Missing Flow.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 0  # Not found

        result = _act_open_flow(state, step, "Missing Flow", opened)

        self.assertIsNone(result)  # Will poll with StillLoading

    def test_open_flow_ui_mismatch_when_not_on_flows_page(self):
        """Returns UI_MISMATCH when not on /setup/flows page."""
        page = self._mock_page(url="https://example.lightning.force.com/other/page")
        state = WalkState(page=page)
        step = _step("Open the Flow Test Flow.")
        opened = {"done": False}

        result = _act_open_flow(state, step, "Test Flow", opened)

        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("not on the Flows list page", result.note)

    @patch('workshop_validator.guide_walk._first')
    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_ui_mismatch_when_click_fails(self, mock_roots, mock_count, mock_first):
        """Click fails returns UI_MISMATCH after retry."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Test Flow.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 1

        mock_loc = Mock()
        mock_loc.click = Mock(side_effect=Exception("Click failed"))
        mock_first.return_value = mock_loc

        result = _act_open_flow(state, step, "Test Flow", opened)

        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("click failed", result.note)
        self.assertTrue(opened["done"])
        self.assertTrue(opened["error"])

    @patch('workshop_validator.guide_walk._first')
    @patch('workshop_validator.guide_walk._read_builder_flow_label')
    @patch('workshop_validator.guide_walk._focus_builder')
    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_clicks_exactly_once(self, mock_roots, mock_count, mock_focus, mock_read_label, mock_first):
        """Opened dict prevents re-clicking on subsequent polls."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Test Flow.")
        opened = {"done": False}

        mock_loc = Mock()
        mock_loc.click = Mock()
        mock_first.return_value = mock_loc

        mock_roots.return_value = [page]
        mock_count.return_value = 1
        mock_focus.return_value = True
        mock_read_label.return_value = "Test Flow"

        # First call: clicks
        result1 = _act_open_flow(state, step, "Test Flow", opened)
        self.assertEqual(result1.status, "PASS")
        self.assertTrue(opened["done"])
        self.assertEqual(mock_loc.click.call_count, 1)

        # Second call: should not click again
        result2 = _act_open_flow(state, step, "Test Flow", opened)
        self.assertEqual(result2.status, "PASS")
        # Verify click wasn't called again (still 1)
        self.assertEqual(mock_loc.click.call_count, 1)

    @patch('workshop_validator.guide_walk._focus_builder')
    @patch('workshop_validator.guide_walk._count')
    @patch('workshop_validator.guide_walk._roots')
    def test_open_flow_polls_when_focus_builder_fails(self, mock_roots, mock_count, mock_focus):
        """Builder not ready returns None for StillLoading poll."""
        page = self._mock_page()
        state = WalkState(page=page)
        step = _step("Open the Flow Test Flow.")
        opened = {"done": False}

        mock_roots.return_value = [page]
        mock_count.return_value = 1
        mock_focus.return_value = False  # Builder not focused yet

        result = _act_open_flow(state, step, "Test Flow", opened)

        self.assertIsNone(result)  # StillLoading


# Real-browser smoke test with synthetic HTML
@unittest.skipIf(not os.getenv("RUN_BROWSER_SMOKE"), "RUN_BROWSER_SMOKE not set")
class TestOpenFlowBrowser(unittest.TestCase):
    def test_open_flow_browser_passes_with_matching_label(self):
        """Real browser: flow list with named flow opens builder with matching label."""
        from playwright.sync_api import sync_playwright
        from workshop_validator.guide_walk import attempt_step, WalkState

        flows_html = """<!DOCTYPE html>
<html>
<head><title>Flows</title></head>
<body>
    <h1>Flows</h1>
    <div role="grid">
        <a role="link" href="/builder">Test Flow Alpha</a>
        <a role="link" href="/builder">Other Flow</a>
    </div>
</body>
</html>"""

        builder_html = """<!DOCTYPE html>
<html>
<head><title>Flow Builder</title></head>
<body>
    <h1>Test Flow Alpha</h1>
    <builder_platform_interaction-canvas style="display: block; width: 100px; height: 100px;"></builder_platform_interaction-canvas>
    <button>Toggle Toolbox</button>
</body>
</html>"""

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            # Intercept navigation to serve our synthetic HTML
            def handle_route(route):
                if "/setup/flows" in route.request.url:
                    route.fulfill(body=flows_html, content_type="text/html")
                elif "/builder" in route.request.url:
                    route.fulfill(body=builder_html, content_type="text/html")
                else:
                    route.continue_()

            page.route("**/*", handle_route)

            # Navigate to the flows page
            page.goto("https://example.lightning.force.com/setup/flows")

            step = _step("Open the Flow Test Flow Alpha.")
            state = WalkState(page=page, timeout_s=5, interval_s=0.5, page_wait_s=2)

            result = attempt_step(state, step)

            browser.close()

            self.assertEqual(result.status, "PASS")
            self.assertIn("Test Flow Alpha", result.note)

    def test_open_flow_browser_needs_review_with_duplicate_rows(self):
        """Real browser: duplicate rows returns NEEDS_REVIEW."""
        from playwright.sync_api import sync_playwright
        from workshop_validator.guide_walk import attempt_step, WalkState

        flows_html = """<!DOCTYPE html>
<html>
<head><title>Flows</title></head>
<body>
    <h1>Flows</h1>
    <div role="grid">
        <a role="link" href="/builder">Duplicate Flow</a>
        <a role="link" href="/builder">Duplicate Flow</a>
    </div>
</body>
</html>"""

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()

            # Intercept navigation to serve our synthetic HTML
            page.route("**/*", lambda route: route.fulfill(body=flows_html, content_type="text/html"))

            # Navigate to the flows page
            page.goto("https://example.lightning.force.com/setup/flows")

            step = _step("Open the Flow Duplicate Flow.")
            state = WalkState(page=page, timeout_s=5, interval_s=0.5, page_wait_s=2)

            result = attempt_step(state, step)

            browser.close()

            self.assertEqual(result.status, "NEEDS_REVIEW")
            self.assertIn("Found 2 flows", result.note)


if __name__ == "__main__":
    unittest.main()
