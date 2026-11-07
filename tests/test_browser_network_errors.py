"""Browser network error detection returns ENV_BLOCKED (I25-2).

DNS errors, connection refused, and other browser network errors are environment problems,
not UI mismatches. They must return ENV_BLOCKED (inconclusive) to keep the org and retry.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from workshop_validator import guide_walk
from workshop_validator.automate import INCONCLUSIVE, gate_outcome_of, outcome_of
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.report import ReportStep


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


class BrowserNetworkErrorTests(unittest.TestCase):
    """Browser network errors are ENV_BLOCKED, not UI_MISMATCH."""

    def freeze_result_for_page_text(self, page_text):
        """Helper to test _freeze_result with synthetic page text."""
        state = SimpleNamespace(page=object())
        test_step = step("s1", "Exercise 1", "Task 1: Sample", "1", "Click Setup")
        with patch.object(guide_walk, "_read_page_text", return_value=page_text), \
                patch.object(guide_walk, "_unnamed_modal_title", return_value=None):
            result = guide_walk._freeze_result(state, test_step, "Setup")
        return result

    def test_dns_error_cant_be_reached_returns_env_blocked(self):
        """Guard test: 'can't be reached' indicator returns ENV_BLOCKED."""
        result = self.freeze_result_for_page_text(
            "example.lightning.force.com This site can't be reached "
            "example.lightning.force.com's server IP address could not be found."
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Browser error page", result.note)
        self.assertTrue(result.freeze)

    def test_dns_error_err_name_not_resolved_returns_env_blocked(self):
        """Guard test: ERR_NAME_NOT_RESOLVED indicator returns ENV_BLOCKED."""
        result = self.freeze_result_for_page_text(
            "This site can't be reached ERR_NAME_NOT_RESOLVED"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Browser error page", result.note)
        self.assertTrue(result.freeze)

    def test_dns_error_server_ip_address_returns_env_blocked(self):
        """Guard test: 'server IP address' indicator returns ENV_BLOCKED."""
        result = self.freeze_result_for_page_text(
            "The server IP address could not be found. DNS_PROBE_FINISHED_NXDOMAIN"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Browser error page", result.note)
        self.assertTrue(result.freeze)

    def test_connection_refused_returns_env_blocked(self):
        """Guard test: ERR_CONNECTION_REFUSED indicator returns ENV_BLOCKED."""
        result = self.freeze_result_for_page_text(
            "This site can't be reached ERR_CONNECTION_REFUSED"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Browser error page", result.note)
        self.assertTrue(result.freeze)

    def test_connection_timeout_returns_env_blocked(self):
        """Guard test: ERR_CONNECTION_TIMED_OUT indicator returns ENV_BLOCKED."""
        result = self.freeze_result_for_page_text(
            "This page isn't working ERR_CONNECTION_TIMED_OUT"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Browser error page", result.note)
        self.assertTrue(result.freeze)

    def test_no_internet_returns_env_blocked(self):
        """Guard test: ERR_INTERNET_DISCONNECTED indicator returns ENV_BLOCKED."""
        result = self.freeze_result_for_page_text(
            "No internet ERR_INTERNET_DISCONNECTED"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Browser error page", result.note)
        self.assertTrue(result.freeze)

    def test_normal_missing_control_returns_ui_mismatch(self):
        """Regression test: Normal missing controls still return UI_MISMATCH."""
        result = self.freeze_result_for_page_text(
            "Account Name Email Phone Status Actions New Account"
        )
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("Could not find Setup", result.note)
        self.assertTrue(result.freeze)

    def test_existing_error_indicators_still_detected(self):
        """Regression test: Existing error indicators (404, access denied) still work."""
        # Test 404
        result = self.freeze_result_for_page_text("404 Not Found")
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("Could not find Setup", result.note)

        # Test access denied
        result = self.freeze_result_for_page_text(
            "Access Denied. You do not have permission to view this page."
        )
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("Could not find Setup", result.note)

    def test_case_insensitive_detection(self):
        """Guard test: Error detection is case-insensitive."""
        result = self.freeze_result_for_page_text(
            "THIS SITE CAN'T BE REACHED ERR_NAME_NOT_RESOLVED"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Browser error page", result.note)


class BrowserNetworkErrorGateTests(unittest.TestCase):
    """Gate integration: ENV_BLOCKED from network errors is INCONCLUSIVE."""

    def test_env_blocked_from_network_error_is_inconclusive(self):
        """Gate test: ENV_BLOCKED from DNS error returns INCONCLUSIVE (exit 3)."""
        run = SimpleNamespace(
            signed_in=True,
            message="",
            report_steps=(
                ReportStep("s1", 1, "Step 1", "PASS"),
                ReportStep(
                    "s2",
                    1,
                    "Step 2",
                    "ENV_BLOCKED",
                    note="Browser error page: example.lightning.force.com this site can't be reached "
                         "err_name_not_resolved",
                ),
                ReportStep("s3", 1, "Step 3", "NOT_RUN"),
            ),
        )
        outcome, note = outcome_of(run)
        self.assertEqual(outcome, INCONCLUSIVE)
        self.assertIn("Browser error page", note)
        self.assertEqual(gate_outcome_of(run), INCONCLUSIVE)


if __name__ == "__main__":
    unittest.main()
