"""P005N004: Real-browser smoke test for Files tab covered/nav-bar guards.

Tests the elementFromPoint (uncovered) check and nav-bar scoping with real HTML.
Run with: RUN_BROWSER_SMOKE=1 python -m unittest tests.test_p005n004_files_browser_smoke
"""
import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


FILES_HOME_HTML = """<!DOCTYPE html>
<html>
<head><title>Files Home</title></head>
<body>
  <div class="one-app-nav-bar">
    <a aria-current="page">Files</a>
  </div>
  <main>
    <h1>Files</h1>
    <p>Your files are shown here.</p>
  </main>
</body>
</html>
"""

FILES_COVERED_HTML = """<!DOCTYPE html>
<html>
<head><title>Files Home</title></head>
<body>
  <div class="one-app-nav-bar">
    <a aria-current="page">Files</a>
  </div>
  <main>
    <h1 id="files-heading">Files</h1>
    <p>Your files are shown here.</p>
  </main>
  <div class="modal" style="position: fixed; top: 0; left: 0; width: 100%; height: 100%;
       background: rgba(0,0,0,0.8); z-index: 9999;">
    <div class="modal-content">
      <p>Modal covering the Files heading</p>
    </div>
  </div>
</body>
</html>
"""

FILES_NAV_ONLY_HTML = """<!DOCTYPE html>
<html>
<head><title>Setup Home</title></head>
<body>
  <div class="one-app-nav-bar">
    <h2>Files</h2>
    <a aria-current="page">Files</a>
  </div>
  <main>
    <h1>Setup</h1>
    <p>No Files content area.</p>
  </main>
</body>
</html>
"""


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1", "Browser smoke only (requires Chromium)")
class FilesTabBrowserSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get('CHROMIUM_PATH') or None
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_new_uncovered_files_heading_detected(self):
        """Positive: New uncovered Files h1 outside nav bar → detected."""
        page = self.browser.new_page()
        page.set_content(FILES_HOME_HTML)

        from workshop_validator.guide_walk import _new_files_heading_uncovered

        before = frozenset()
        result = _new_files_heading_uncovered(page, before)
        page.close()

        self.assertTrue(result, "New uncovered Files heading should be detected")

    def test_files_heading_covered_by_modal_not_detected(self):
        """Adversarial 4: Files heading covered by modal → not detected."""
        page = self.browser.new_page()
        page.set_content(FILES_COVERED_HTML)

        from workshop_validator.guide_walk import _new_files_heading_uncovered

        before = frozenset()
        result = _new_files_heading_uncovered(page, before)
        page.close()

        self.assertFalse(result, "Covered Files heading should NOT be detected")

    def test_files_heading_inside_nav_bar_only_not_detected(self):
        """Adversarial 5: Files heading inside nav bar only → not detected."""
        page = self.browser.new_page()
        page.set_content(FILES_NAV_ONLY_HTML)

        from workshop_validator.guide_walk import _new_files_heading_uncovered

        before = frozenset()
        result = _new_files_heading_uncovered(page, before)
        page.close()

        self.assertFalse(result, "Files heading inside nav bar should NOT be detected")

    def test_snapshot_excludes_nav_bar_headings(self):
        """Verify snapshot excludes headings inside one-app-nav-bar."""
        page = self.browser.new_page()
        page.set_content(FILES_NAV_ONLY_HTML)

        from workshop_validator.guide_walk import _snapshot_content_headings

        snapshot = _snapshot_content_headings(page)
        page.close()

        # "Files" h2 in nav bar should be excluded; "Setup" h1 in main should be included
        self.assertNotIn("files", snapshot, "Files heading in nav bar should be excluded")
        self.assertIn("setup", snapshot, "Setup heading in main should be included")


if __name__ == "__main__":
    unittest.main()
