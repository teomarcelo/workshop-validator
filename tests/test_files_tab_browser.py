"""F-P005N004-NR: Files tab from Setup real-browser test. Synthetic HTML only. RUN_BROWSER_SMOKE=1."""
import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import walk_guide

SETUP_URL = "https://example.lightning.force.com/lightning/setup/HomePage/home"
FILES_URL = "https://example.lightning.force.com/lightning/o/ContentDocument/home"

SETUP_HTML = """<!doctype html>
<html><head><title>Setup | Salesforce</title></head>
<body>
<div>Personal Information</div>
<div>Files</div>
</body></html>"""

FILES_HTML = """<!doctype html>
<html><head><title>Files | Salesforce</title></head>
<body>
<div role="navigation">
  <a href="/files" aria-current="page">Files</a>
</div>
<h1>Files Home</h1>
<div>Recent Files</div>
</body></html>"""


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class FilesTabBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get("CHROMIUM_PATH") or None
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_files_nav_current_after_goto_is_needs_review_in_real_browser(self):
        """Files clicked from Setup with nav item current after goto returns NEEDS_REVIEW."""
        page = self.browser.new_page(viewport={"width": 800, "height": 600})
        try:
            page.route("**/*", lambda route: route.fulfill(
                status=200, content_type="text/html",
                body=FILES_HTML if "ContentDocument" in route.request.url else SETUP_HTML,
            ))
            page.goto(SETUP_URL)

            files_step = GuideStep(
                "f1", 1, "Exercise 5: Files", "Task 1: Open files.", "1", "Click the Files tab.",
                (0, 0, 0, 0), None, (),
            )
            outcome = walk_guide(
                page, GuideInventory(1, (files_step,), (), ()),
                checks=(), timeout_s=90, interval_s=2, page_wait_s=10,
            )

            self.assertEqual(outcome.statuses["f1"], "NEEDS_REVIEW")
            self.assertIn("Opened the Files home", outcome.notes["f1"])
            self.assertIn("step-verifier judges this step", outcome.notes["f1"])
        finally:
            page.close()


if __name__ == "__main__":
    unittest.main()
