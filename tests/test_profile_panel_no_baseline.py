"""Tests for profile panel detection when pre-click baseline is missing."""
import unittest
from workshop_validator.guide_walk import _PANEL_JS


class TestProfilePanelNoBaseline(unittest.TestCase):
    """Test _PANEL_JS logic for finding panels without pre-click baseline."""

    def test_panel_js_does_not_fail_fast_on_missing_baseline(self):
        """_PANEL_JS does NOT return false immediately when baseline is missing.

        Old buggy code had: if (!seen) return false;
        This prevented finding panels in frames without pre-click baseline.
        New code must have a fallback path that finds panels structurally.
        """
        # The bug was: if (!seen) return false; at the start
        # This would fail immediately when baseline was missing
        # New code should NOT have this pattern
        self.assertNotIn("if (!seen) return false", _PANEL_JS)

        # Instead, it should have conditional logic that handles both cases
        self.assertIn("if (seen)", _PANEL_JS)

    def test_panel_js_finds_settings_without_baseline(self):
        """_PANEL_JS searches for Settings elements when baseline is missing.

        This is the core of the fix: when window.__wvShownBeforeProfile is missing,
        the code must find Settings elements and compute common ancestors with Log Out.
        """
        # Must search for Settings elements
        self.assertIn("Settings", _PANEL_JS)
        # Must have a function or logic to find Settings elements
        self.assertIn("findVisibleSettings", _PANEL_JS)

    def test_panel_js_computes_smallest_common_ancestor(self):
        """_PANEL_JS finds the smallest common ancestor of Log Out and Settings.

        When baseline is missing, the panel is the smallest container holding
        both Log Out (the element passed to the function) and Settings.
        """
        self.assertIn("smallestPanel", _PANEL_JS)
        self.assertIn("smallestSize", _PANEL_JS)
        # Must iterate through Settings elements to find common ancestors
        self.assertIn("logoutAncestors", _PANEL_JS)

    def test_panel_js_excludes_body_documentElement(self):
        """_PANEL_JS rejects body and documentElement as panels."""
        # Both the baseline and no-baseline paths must exclude these
        body_checks = _PANEL_JS.count("document.body")
        doc_checks = _PANEL_JS.count("document.documentElement")
        self.assertGreaterEqual(body_checks, 2, "Should check document.body in multiple places")
        self.assertGreaterEqual(doc_checks, 2, "Should check document.documentElement in multiple places")

    def test_panel_js_excludes_global_navigation(self):
        """_PANEL_JS rejects containers that also hold global header/navigation.

        This prevents selecting a container so large it includes both the profile
        panel and the global page header.
        """
        self.assertIn("hasGlobalNav", _PANEL_JS)
        # Must check for header, nav, and ARIA roles
        self.assertIn("'header'", _PANEL_JS)
        self.assertIn("'nav'", _PANEL_JS)
        self.assertIn("'banner'", _PANEL_JS)
        self.assertIn("'navigation'", _PANEL_JS)


if __name__ == '__main__':
    unittest.main()
