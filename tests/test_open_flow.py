"""Test opening a named flow from the Flows list page."""

import unittest
from unittest.mock import patch, MagicMock
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


class TestOpenFlowAction(unittest.TestCase):
    @patch('workshop_validator.guide_walk._flow_builder_open')
    @patch('workshop_validator.guide_walk._click_locator')
    @patch('workshop_validator.guide_walk._locator_for_name')
    def test_open_flow_on_flows_page_passes(self, mock_locator, mock_click, mock_builder):
        """Opening a flow on /setup/flows passes when Flow Builder opens."""
        page = SimpleNamespace(url="https://example.lightning.force.com/setup/flows")
        state = WalkState(page=page)
        step = _step("Open the Flow Demo Flow Alpha.")

        mock_locator.return_value = MagicMock()  # Found
        mock_click.return_value = True  # Click succeeded
        mock_builder.return_value = True  # Flow Builder opened

        result = _act_open_flow(state, step, "Demo Flow Alpha")

        self.assertEqual(result.status, "PASS")
        self.assertIn("Flow Builder opened", result.note)
        self.assertIn("Demo Flow Alpha", result.note)
        mock_locator.assert_called_once_with(page, "Demo Flow Alpha")

    @patch('workshop_validator.guide_walk._locator_for_name')
    def test_open_flow_not_on_flows_page_returns_none(self, mock_locator):
        """Returns None when not on /setup/flows page."""
        page = SimpleNamespace(url="https://example.lightning.force.com/other/page")
        state = WalkState(page=page)
        step = _step("Open the Flow Demo Flow Alpha.")

        result = _act_open_flow(state, step, "Demo Flow Alpha")

        self.assertIsNone(result)
        mock_locator.assert_not_called()

    @patch('workshop_validator.guide_walk._locator_for_name')
    def test_open_flow_not_found_returns_none(self, mock_locator):
        """Returns None when flow link is not found."""
        page = SimpleNamespace(url="https://example.lightning.force.com/setup/flows")
        state = WalkState(page=page)
        step = _step("Open the Flow Nonexistent Flow.")

        mock_locator.return_value = None  # Not found

        result = _act_open_flow(state, step, "Nonexistent Flow")

        self.assertIsNone(result)

    @patch('workshop_validator.guide_walk._click_locator')
    @patch('workshop_validator.guide_walk._locator_for_name')
    def test_open_flow_click_fails_returns_none(self, mock_locator, mock_click):
        """Returns None when click fails."""
        page = SimpleNamespace(url="https://example.lightning.force.com/setup/flows")
        state = WalkState(page=page)
        step = _step("Open the Flow Demo Flow Alpha.")

        mock_locator.return_value = MagicMock()  # Found
        mock_click.return_value = False  # Click failed

        result = _act_open_flow(state, step, "Demo Flow Alpha")

        self.assertIsNone(result)

    @patch('workshop_validator.guide_walk._flow_builder_open')
    @patch('workshop_validator.guide_walk._click_locator')
    @patch('workshop_validator.guide_walk._locator_for_name')
    def test_open_flow_builder_does_not_open_returns_none(self, mock_locator, mock_click, mock_builder):
        """Returns None when Flow Builder does not open after click."""
        page = SimpleNamespace(url="https://example.lightning.force.com/setup/flows")
        state = WalkState(page=page)
        step = _step("Open the Flow Demo Flow Alpha.")

        mock_locator.return_value = MagicMock()  # Found
        mock_click.return_value = True  # Click succeeded
        mock_builder.return_value = False  # Flow Builder did not open

        result = _act_open_flow(state, step, "Demo Flow Alpha")

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
