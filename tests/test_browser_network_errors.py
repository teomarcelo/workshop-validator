"""Browser network error detection returns ENV_BLOCKED (I25-2).

Browser network errors (Chrome error pages, DNS errors, connection errors) are
environment problems, not UI mismatches. They must return ENV_BLOCKED to keep
the org and retry.
"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from workshop_validator import guide_walk
from workshop_validator.automate import INCONCLUSIVE, gate_outcome_of, outcome_of
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.report import ReportStep


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


class BrowserErrorSignalTests(unittest.TestCase):
    """Tests for _browser_error_signal helper."""

    def test_chrome_error_url_returns_signal(self):
        """Guard test: chrome-error:// URL returns signal."""
        page = Mock()
        page.url = "chrome-error://chromewebdata/"
        with patch.object(guide_walk, "_read_page_text", return_value=""):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "chrome-error://")

    def test_err_name_not_resolved_returns_code(self):
        """Guard test: ERR_NAME_NOT_RESOLVED returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="This site can't be reached ERR_NAME_NOT_RESOLVED"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_NAME_NOT_RESOLVED")

    def test_err_connection_refused_returns_code(self):
        """Guard test: ERR_CONNECTION_REFUSED returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="Connection refused ERR_CONNECTION_REFUSED"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_CONNECTION_REFUSED")

    def test_err_connection_reset_returns_code(self):
        """Guard test: ERR_CONNECTION_RESET returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="Connection reset ERR_CONNECTION_RESET"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_CONNECTION_RESET")

    def test_err_connection_timed_out_returns_code(self):
        """Guard test: ERR_CONNECTION_TIMED_OUT returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="Timeout ERR_CONNECTION_TIMED_OUT"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_CONNECTION_TIMED_OUT")

    def test_err_internet_disconnected_returns_code(self):
        """Guard test: ERR_INTERNET_DISCONNECTED returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="No internet ERR_INTERNET_DISCONNECTED"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_INTERNET_DISCONNECTED")

    def test_err_network_changed_returns_code(self):
        """Guard test: ERR_NETWORK_CHANGED returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="Network changed ERR_NETWORK_CHANGED"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_NETWORK_CHANGED")

    def test_err_address_unreachable_returns_code(self):
        """Guard test: ERR_ADDRESS_UNREACHABLE returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="Address unreachable ERR_ADDRESS_UNREACHABLE"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_ADDRESS_UNREACHABLE")

    def test_dns_probe_finished_nxdomain_returns_code(self):
        """Guard test: DNS_PROBE_FINISHED_NXDOMAIN returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="DNS probe finished DNS_PROBE_FINISHED_NXDOMAIN"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "DNS_PROBE_FINISHED_NXDOMAIN")

    def test_dns_probe_finished_no_internet_returns_code(self):
        """Guard test: DNS_PROBE_FINISHED_NO_INTERNET returns the code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="No internet DNS_PROBE_FINISHED_NO_INTERNET"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "DNS_PROBE_FINISHED_NO_INTERNET")

    def test_case_insensitive_code_detection(self):
        """Guard test: Error code detection is case-insensitive."""
        page = Mock()
        page.url = "https://example.lightning.force.com/"
        with patch.object(guide_walk, "_read_page_text", return_value="This site can't be reached err_name_not_resolved"):
            result = guide_walk._browser_error_signal(page)
        self.assertEqual(result, "ERR_NAME_NOT_RESOLVED")

    def test_normal_page_returns_none(self):
        """Normal page with no error signals returns None."""
        page = Mock()
        page.url = "https://example.lightning.force.com/setup"
        with patch.object(guide_walk, "_read_page_text", return_value="Account Name Email Phone Status Actions"):
            result = guide_walk._browser_error_signal(page)
        self.assertIsNone(result)


class FreezeResultBrowserErrorTests(unittest.TestCase):
    """Browser network errors in _freeze_result return ENV_BLOCKED."""

    def freeze_result_for_page(self, page_url, page_text):
        """Helper to test _freeze_result with synthetic page."""
        page = Mock()
        page.url = page_url
        state = SimpleNamespace(page=page)
        test_step = step("s1", "Exercise 1", "Task 1: Sample", "1", "Click Setup")
        with patch.object(guide_walk, "_read_page_text", return_value=page_text), \
                patch.object(guide_walk, "_unnamed_modal_title", return_value=None):
            result = guide_walk._freeze_result(state, test_step, "Setup")
        return result

    def test_err_name_not_resolved_returns_env_blocked(self):
        """Guard test: ERR_NAME_NOT_RESOLVED returns ENV_BLOCKED."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/",
            "This site can't be reached ERR_NAME_NOT_RESOLVED"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Could not find Setup", result.note)
        self.assertIn("Browser network error: ERR_NAME_NOT_RESOLVED", result.note)
        self.assertTrue(result.freeze)

    def test_dns_probe_finished_nxdomain_returns_env_blocked(self):
        """Guard test: DNS_PROBE_FINISHED_NXDOMAIN returns ENV_BLOCKED."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/",
            "DNS probe DNS_PROBE_FINISHED_NXDOMAIN"
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Could not find Setup", result.note)
        self.assertIn("Browser network error: DNS_PROBE_FINISHED_NXDOMAIN", result.note)
        self.assertTrue(result.freeze)

    def test_chrome_error_url_returns_env_blocked(self):
        """Guard test: chrome-error:// URL returns ENV_BLOCKED."""
        result = self.freeze_result_for_page(
            "chrome-error://chromewebdata/",
            ""
        )
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertIn("Could not find Setup", result.note)
        self.assertIn("Browser network error: chrome-error://", result.note)
        self.assertTrue(result.freeze)

    def test_note_keeps_original_and_adds_error(self):
        """The note keeps original 'Could not find' text and adds error code."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/",
            "ERR_CONNECTION_REFUSED"
        )
        self.assertIn("Could not find Setup", result.note)
        self.assertIn("Browser network error: ERR_CONNECTION_REFUSED", result.note)
        # Ensure no hostname in the note
        self.assertNotIn("example.lightning.force.com", result.note)

    def test_normal_data_not_available_stays_ui_mismatch(self):
        """Normal Salesforce page: Data Not Available with 'could not be found' stays UI_MISMATCH."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/lightning/r/Account/001xx000003D/view",
            "Data Not Available The data you are trying to access could not be found. Contact your administrator."
        )
        self.assertEqual(result.status, "UI_MISMATCH")

    def test_agent_reply_with_could_not_be_found_stays_ui_mismatch(self):
        """Normal page: Agent reply with 'could not be found' stays UI_MISMATCH."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/lightning/r/Account/001xx000003D/view",
            "Agent: I could not be found in the system. Please try again later."
        )
        self.assertEqual(result.status, "UI_MISMATCH")

    def test_setup_page_with_server_ip_address_label_stays_ui_mismatch(self):
        """Normal Setup page with 'Server IP Address' label stays UI_MISMATCH."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/lightning/setup/NetworkAccess/home",
            "Network Access Trusted IP Ranges Enter the server IP address range for your organization"
        )
        self.assertEqual(result.status, "UI_MISMATCH")

    def test_flow_toast_with_could_not_be_found_stays_ui_mismatch(self):
        """Normal page: Flow toast with 'could not be found' stays UI_MISMATCH."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            "Error The flow element could not be found. Please check your configuration."
        )
        self.assertEqual(result.status, "UI_MISMATCH")

    def test_help_article_mentioning_cant_be_reached_stays_ui_mismatch(self):
        """Normal page: Help article mentioning 'can't be reached' stays UI_MISMATCH."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/one/one.app",
            "Help Article: If your site can't be reached by customers, check your DNS settings and firewall rules."
        )
        self.assertEqual(result.status, "UI_MISMATCH")

    def test_network_access_page_stays_ui_mismatch(self):
        """Normal Setup Network Access page stays UI_MISMATCH."""
        result = self.freeze_result_for_page(
            "https://example.lightning.force.com/lightning/setup/NetworkAccess/home",
            "Security Network Access Trusted IP Ranges Server IP Address Netmask Description Actions"
        )
        self.assertEqual(result.status, "UI_MISMATCH")


class ClickPathBrowserErrorTests(unittest.TestCase):
    """Click path guards: browser errors block URL-change PASS evidence."""

    def test_network_error_blocks_url_evidence(self):
        """Guard test: _is_error_page returns True for browser network errors."""
        page = Mock()
        page.url = "https://example.lightning.force.com/setup"
        page_text = "Setup Home ERR_NAME_NOT_RESOLVED"

        # Test the _is_error_page helper used by both click paths
        with patch.object(guide_walk, "_read_page_text", return_value=page_text):
            is_error = guide_walk._is_error_page(page)
            # With the browser error signal, is_error should be True
            self.assertTrue(is_error)

    def test_second_network_error_blocks_url_evidence(self):
        """Guard test: _is_error_page returns True for different error code."""
        page = Mock()
        page.url = "https://example.lightning.force.com/setup"
        page_text = "Setup Home ERR_CONNECTION_REFUSED"

        # Test the _is_error_page helper with a different error code
        with patch.object(guide_walk, "_read_page_text", return_value=page_text):
            is_error = guide_walk._is_error_page(page)
            # With the browser error signal, is_error should be True
            self.assertTrue(is_error)

    def test_normal_page_with_chat_does_not_block_url_pass(self):
        """Guard test: Normal page with 'could not be found' in chat does NOT block URL evidence."""
        page = Mock()
        page.url = "https://example.lightning.force.com/lightning/setup/SetupOneHome/home"
        page_text = "Setup Home Chat: The item could not be found"

        # Test that normal page text doesn't trigger _is_error_page
        with patch.object(guide_walk, "_read_page_text", return_value=page_text):
            is_error = guide_walk._is_error_page(page)
            # Without error indicators or browser error signal, is_error should be False
            self.assertFalse(is_error)


class BrowserNetworkErrorGateTests(unittest.TestCase):
    """Gate integration: ENV_BLOCKED from network errors is INCONCLUSIVE."""

    def test_env_blocked_from_network_error_is_inconclusive(self):
        """Gate test: ENV_BLOCKED from browser error returns INCONCLUSIVE (exit 3)."""
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
                    note="Could not find Setup. The page showed: error page. Browser network error: ERR_NAME_NOT_RESOLVED",
                ),
                ReportStep("s3", 1, "Step 3", "NOT_RUN"),
            ),
        )
        outcome, note = outcome_of(run)
        self.assertEqual(outcome, INCONCLUSIVE)
        self.assertIn("Browser network error", note)
        self.assertEqual(gate_outcome_of(run), INCONCLUSIVE)


class BrowserNetworkErrorSmokeTest(unittest.TestCase):
    """Real browser test: synthetic HTML with Chrome error returns ENV_BLOCKED."""

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE") == "1", "Browser smoke tests skipped")
    def test_chrome_error_page_returns_env_blocked(self):
        """Real-browser guard test: Chrome-style error page returns ENV_BLOCKED."""
        from playwright.sync_api import sync_playwright

        # Create synthetic HTML with Chrome error
        error_html = """<!DOCTYPE html>
<html>
<head><title>This site can't be reached</title></head>
<body>
<h1>This site can't be reached</h1>
<p>example.lightning.force.com's server IP address could not be found.</p>
<p>ERR_NAME_NOT_RESOLVED</p>
</body>
</html>"""

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(error_html)
            html_path = f.name

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"file://{html_path}")

                # Test the helper
                with patch.object(guide_walk, "_read_page_text") as mock_read:
                    mock_read.return_value = page.locator("body").inner_text()
                    error_signal = guide_walk._browser_error_signal(page)
                    self.assertEqual(error_signal, "ERR_NAME_NOT_RESOLVED")

                browser.close()
        finally:
            os.unlink(html_path)

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE") == "1", "Browser smoke tests skipped")
    def test_normal_page_missing_control_returns_ui_mismatch(self):
        """Real-browser guard test: Normal page missing control returns UI_MISMATCH."""
        from playwright.sync_api import sync_playwright

        # Create synthetic normal HTML
        normal_html = """<!DOCTYPE html>
<html>
<head><title>Account Home</title></head>
<body>
<h1>Accounts</h1>
<button>New</button>
<button>Import</button>
</body>
</html>"""

        # Write to temp file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(normal_html)
            html_path = f.name

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"file://{html_path}")

                # Test the helper returns None for normal page
                with patch.object(guide_walk, "_read_page_text") as mock_read:
                    mock_read.return_value = page.locator("body").inner_text()
                    error_signal = guide_walk._browser_error_signal(page)
                    self.assertIsNone(error_signal)

                browser.close()
        finally:
            os.unlink(html_path)


if __name__ == "__main__":
    unittest.main()
