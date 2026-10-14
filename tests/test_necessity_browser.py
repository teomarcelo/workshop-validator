"""Synthetic fault-injection tests of generated-script state, NOT Salesforce."""

import os
import unittest

from workshop_validator.browser_worker import UIAssertion, collect_observations
from workshop_validator.evidence import AssertionSpec, StepContext, verify_step
from workshop_validator.necessity import assess_necessity

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
                     "Synthetic Chromium smoke test only")
class BehavioralBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.set_content('''
          <div role="textbox" aria-label="Generated script" contenteditable="true">items: list[object]
mode: generated</div>
          <button onclick="document.querySelector('[role=status]').innerText='Saved'">Save</button>
          <div role="status" aria-label="Save outcome">Waiting</div>
        ''')
        self.ctx = StepContext("synthetic-run", "synthetic-org", "step", "browser", "verifier")

    def check_script(self):
        specs = [
            UIAssertion(AssertionSpec("correct_type", "DOM_TEXT",
                                      "items: list[object]", "contains"),
                        "textbox", "Generated script"),
            UIAssertion(AssertionSpec("absent_legacy_block", "DOM_TEXT",
                                      "unused_setting:", "not_contains"),
                        "textbox", "Generated script"),
        ]
        observations = collect_observations(self.page, self.ctx, specs)
        return verify_step(self.ctx, [item.spec for item in specs], observations)

    def check_downstream(self):
        assertion = UIAssertion(AssertionSpec("saved", "DOM_TEXT", "Saved"),
                                "status", "Save outcome")
        observed = collect_observations(self.page, self.ctx, (assertion,))
        return verify_step(self.ctx, (assertion.spec,), observed)

    def test_auto_generated_state_and_skip_then_fault_injection(self):
        before = self.check_script()
        self.page.get_by_role("button", name="Save").click()
        downstream = self.check_downstream()
        self.page.get_by_role("textbox", name="Generated script").fill(
            "items: string\nmode: generated")
        wrong = self.check_script()
        self.assertEqual(wrong.status, "FUNCTIONAL_FAIL")
        decision = assess_necessity(
            before, after_skipping=downstream, negative_control=wrong,
            baseline_reproduced=True, prerequisites_verified=True)
        self.assertEqual(decision.classification, "REDUNDANCY_CANDIDATE")
        self.assertTrue(decision.requires_instructor_review)

    def test_legacy_block_absence_check_catches_deliberate_presence(self):
        self.assertEqual(self.check_script().status, "PASS")
        self.page.get_by_role("textbox", name="Generated script").fill(
            "items: list[object]\nunused_setting: old")
        self.assertEqual(self.check_script().status, "FUNCTIONAL_FAIL")


if __name__ == "__main__":
    unittest.main()
