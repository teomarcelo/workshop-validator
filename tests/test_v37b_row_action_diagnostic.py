"""V37b: Row action diagnostic when dropdown not found."""

import os
import unittest
from unittest import mock

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.guide_walk import walk_guide
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from tests.test_guide_walk import inventory_of, step


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
                     "Browser smoke only (requires Chromium)")
class TestRowActionDiagnostic(unittest.TestCase):
    """Test V37b row action diagnostic on the 'not found' exit."""

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

    def tearDown(self):
        self.page.close()

    def test_a_hidden_arrow_with_open_aside(self):
        """Test (a): hidden arrow + open aside with header Close shows exact flags and tree."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <aside id="test-aside" role="complementary" aria-label="Zeta Panel" style="position:fixed; right:0; top:0; width:300px; height:100%; background:white; border-left:1px solid gray;" data-clicked="false">
                    <header style="padding:10px; border-bottom:1px solid gray;">
                        <button aria-label="Close">X</button>
                    </header>
                    <div style="padding:10px;">
                        <p>Alpha note with SENTINEL_TEXT_12345</p>
                    </div>
                </aside>
                <script>
                    document.getElementById('test-aside').onclick = function() {
                        this.setAttribute('data-clicked', 'true');
                    };
                </script>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        status = outcome.statuses[s.step_id]
        note = outcome.notes[s.step_id]

        self.assertEqual(status, "UI_MISMATCH")
        self.assertIn("row was not found", note)

        # Check diagnostic flags
        self.assertIn("Rows matched: 1", note)  # From _row_menu_scan note
        self.assertIn("hidden menu buttons in the row: 1", note)  # From _row_menu_scan note
        self.assertIn("side landmarks visible 1", note)
        self.assertIn("tree:", note)
        self.assertIn("complementary", note)

        # Check redaction: sentinel should NOT appear in the note
        self.assertNotIn("SENTINEL_TEXT_12345", note, "Sentinel should be redacted")
        self.assertIn("<text>", note, "Text should be redacted to <text>")

        # The aside should still be there (not clicked)
        aside_still_there = self.page.locator("#test-aside").count() > 0
        self.assertTrue(aside_still_there, "The aside panel should not be clicked/removed")
        # Check that the aside was NOT clicked
        aside_clicked = self.page.locator("#test-aside").get_attribute("data-clicked")
        self.assertEqual(aside_clicked, "false", "The aside should not have been clicked")

    def test_b_no_arrow_no_panel(self):
        """Test (b): no arrow and no panel shows 'side landmarks visible 0' with no tree."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <!-- No action button -->
                            </td>
                        </tr>
                    </tbody>
                </table>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        self.assertEqual(outcome.statuses[s.step_id], "UI_MISMATCH")
        note = outcome.notes[s.step_id]
        self.assertIn("row was not found", note)

        # Check diagnostic shows no panel
        self.assertIn("side landmarks visible 0", note)
        self.assertNotIn("tree:", note)

    def test_c_existing_passing_row_dropdown_unchanged(self):
        """Test (c): existing passing row dropdown is unchanged (no diagnostic)."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button id="actions-btn" aria-haspopup="true" aria-label="Show actions">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <div id="menu" role="menu" style="display:none;">
                    <div role="menuitem">Edit</div>
                </div>
                <script>
                    document.getElementById('actions-btn').onclick = function() {
                        document.getElementById('menu').style.display = 'block';
                    };
                </script>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        status = outcome.statuses[s.step_id]
        self.assertIn(status, ["PASS", "NEEDS_REVIEW"])
        note = outcome.notes[s.step_id]
        self.assertIn("Opened the dropdown in the Flow Builder Quick Reference row and clicked Edit", note)

        # No diagnostic should be added
        self.assertNotIn("side landmarks visible", note)

    def test_d_late_step_tree_skipped(self):
        """Test (d): late step shows 'tree: skipped, step time nearly used'."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <aside role="complementary" aria-label="Zeta Panel" style="position:fixed; right:0; top:0; width:300px; height:100%; background:white;">
                    <button aria-label="Close">X</button>
                </aside>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)

        # Mock _time_left to return 1.5 (< 2) to trigger the skip
        with mock.patch('workshop_validator.guide_walk._time_left') as mock_time_left:
            mock_time_left.return_value = 1.5
            outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        note = outcome.notes[s.step_id]
        self.assertIn("side landmarks visible 1", note)
        # Tree should be skipped due to time
        self.assertIn("tree: skipped, step time nearly used", note)

    def test_e_no_deadline_inf_guard(self):
        """Test (e): time_left returns inf and tree is captured."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <aside role="complementary" aria-label="Zeta Panel" style="position:fixed; right:0; top:0; width:300px; height:100%; background:white;">
                    <button aria-label="Close">X</button>
                </aside>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)

        # Mock _time_left to return inf
        with mock.patch('workshop_validator.guide_walk._time_left') as mock_time_left:
            mock_time_left.return_value = float('inf')
            outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        note = outcome.notes[s.step_id]
        self.assertIn("side landmarks visible 1", note)
        # Should have tree with no crash
        self.assertIn("tree: - complementary", note)

    def test_f_snapshot_error_unavailable(self):
        """Test (f): aria_snapshot raises and tree shows 'unavailable'."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <aside id="test-aside" role="complementary" aria-label="Zeta Panel" style="position:fixed; right:0; top:0; width:300px; height:100%; background:white;">
                    <button aria-label="Close">X</button>
                </aside>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)

        # Patch aria_snapshot to raise an exception
        def mock_snapshot(*args, **kwargs):
            raise RuntimeError("Snapshot failed")

        original_locator = self.page.locator
        def patched_locator(selector):
            loc = original_locator(selector)
            if "complementary" in selector or selector == "aside":
                original_all = loc.all
                def patched_all():
                    items = original_all()
                    for item in items:
                        item.aria_snapshot = mock_snapshot
                    return items
                loc.all = patched_all
            return loc

        self.page.locator = patched_locator
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        note = outcome.notes[s.step_id]
        self.assertIn("tree: unavailable", note)

    def test_g_dismiss_word_boundaries(self):
        """Test (g): dismiss check uses word boundaries."""
        self.page.goto("data:text/html,")
        # Test "Closed Won" (no match), "Enclosed" (no match), "Close panel" (match)
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <aside role="complementary" aria-label="Zeta Panel" style="position:fixed; right:0; top:0; width:300px; height:100%; background:white;">
                    <button aria-label="Closed Won">CW</button>
                    <button aria-label="Enclosed">E</button>
                </aside>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        note = outcome.notes[s.step_id]
        # "Closed Won" and "Enclosed" should NOT match
        self.assertIn("dismiss-like control in landmark no", note)

    def test_h_dismiss_word_boundaries_yes(self):
        """Test (h): 'Close panel' matches Close."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <aside role="complementary" aria-label="Zeta Panel" style="position:fixed; right:0; top:0; width:300px; height:100%; background:white;">
                    <button aria-label="Close panel">X</button>
                </aside>
            </body>
            </html>
        """)

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        note = outcome.notes[s.step_id]
        # "Close panel" should match "Close"
        self.assertIn("dismiss-like control in landmark yes", note)


    def test_i_execution_context_destroyed(self):
        """Test (i): Locator.all raises Error (execution context destroyed) -> unknown."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
            </body>
            </html>
        """)

        # Patch Locator.all to raise Error
        from playwright.sync_api import Error as PlaywrightError

        original_locator = self.page.locator
        def patched_locator(selector):
            loc = original_locator(selector)
            if "complementary" in selector or selector == "aside":
                def raise_error():
                    raise PlaywrightError("Execution context was destroyed")
                loc.all = raise_error
            return loc

        self.page.locator = patched_locator

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        note = outcome.notes[s.step_id]
        # The original note must be preserved
        self.assertIn("The dropdown in the Flow Builder Quick Reference row was not found", note)
        self.assertIn("Rows matched: 1", note)
        # The diagnostic should show unknown
        self.assertIn("side landmarks visible unknown", note)
        self.assertIn("dismiss-like control in landmark unknown", note)


    def test_j_dismiss_check_fails(self):
        """Test (j): Dismiss check raises Error -> dismiss-like control unknown."""
        self.page.goto("data:text/html,")
        self.page.set_content("""
            <!DOCTYPE html>
            <html>
            <body>
                <table role="table">
                    <tbody>
                        <tr role="row" aria-label="Flow Builder Quick Reference">
                            <td>Flow Builder Quick Reference</td>
                            <td>
                                <button aria-haspopup="true" aria-label="Show actions" style="display:none;">Actions</button>
                            </td>
                        </tr>
                    </tbody>
                </table>
                <aside id="test-aside" role="complementary" aria-label="Zeta Panel" style="position:fixed; right:0; top:0; width:300px; height:100%; background:white;">
                    <button aria-label="Close">X</button>
                </aside>
            </body>
            </html>
        """)

        # Patch get_by_role to raise Error when called on the aside
        from playwright.sync_api import Error as PlaywrightError

        original_locator = self.page.locator
        def patched_locator(selector):
            loc = original_locator(selector)
            if "complementary" in selector or selector == "aside":
                original_all = loc.all
                def all_with_patch():
                    locs = original_all()
                    if locs:
                        original_get_by_role = locs[0].get_by_role
                        def raise_error(role):
                            raise PlaywrightError("Execution context was destroyed")
                        locs[0].get_by_role = raise_error
                    return locs
                loc.all = all_with_patch
            return loc

        self.page.locator = patched_locator

        s = step("test1", "Test", "Task", "1",
                 "Click the dropdown arrow to the far right of Flow Builder Quick Reference and select Edit.")
        inventory = inventory_of(s)
        outcome = walk_guide(self.page, inventory, timeout_s=90, interval_s=2, page_wait_s=2)

        note = outcome.notes[s.step_id]
        status = outcome.statuses[s.step_id]
        # Should not be an unexpected error
        self.assertEqual(status, "UI_MISMATCH")
        # Landmark count should succeed
        self.assertIn("side landmarks visible 1", note)
        # Dismiss check exception is caught and sets unknown
        self.assertIn("dismiss-like control in landmark unknown", note)
        # Should still have the original note
        self.assertIn("The dropdown in the Flow Builder Quick Reference row was not found", note)


if __name__ == "__main__":
    unittest.main()
