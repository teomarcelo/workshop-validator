"""V38: Promo on other tab is found and closed on the correct page."""
import os
import unittest
from workshop_validator.guide_walk import (
    _promo_cleared,
    WalkState,
    GuideStep,
    PROMO_NOTE,
    PROMO_CLOSED_NOTE,
)


def _make_step(text="Click something"):
    return GuideStep(
        step_id="001",
        page=1,
        exercise=None,
        task=None,
        number="001",
        text=text,
        bbox=(0, 0, 0, 0),
        parent_id=None,
        warnings=(),
    )


@unittest.skipIf(os.getenv("RUN_BROWSER_SMOKE") != "1", "browser test")
class TestV38PromoOtherTab(unittest.TestCase):
    def test_promo_on_other_tab_closed_with_own_dismiss(self):
        """(a) Promo on the other tab is closed with its own Close, step resumes, notes have active tab no."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()

            # Main page (page 1) - no promo
            page1 = context.new_page()
            page1.set_content("<html><body>Main page content</body></html>")

            # Secondary page (page 2) - has promo with Close button
            page2 = context.new_page()
            page2.set_content("""
                <html><body>
                    <div role="dialog" aria-modal="true" id="promo" style="position: fixed; z-index: 999; display: block;">
                        <p>Go Mobile with Salesforce</p>
                        <button onclick="document.getElementById('promo').remove()">Close</button>
                    </div>
                </body></html>
            """)

            state = WalkState(
                page=page1,
                clock=lambda: 0.0,
                popup_notes=[],
                step_deadline=5.0
            )

            step = _make_step()

            # Call _promo_cleared: should find promo on page2, close it, and return None
            result = _promo_cleared(state, step)

            browser.close()

            # After fix: promo should be closed, result is None
            self.assertIsNone(result)
            # Check that the popup notes were added
            self.assertEqual(len(state.popup_notes), 2)
            self.assertEqual(state.popup_notes[0], PROMO_CLOSED_NOTE)
            self.assertEqual(state.popup_notes[1], "Environment: the promotional pop-up was on another tab.")

    def test_promo_on_active_tab_unchanged(self):
        """(b) Promo on the active tab is unchanged (existing behavior), with active tab yes."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()

            # Only one page with promo
            page1 = context.new_page()
            page1.set_content("""
                <html><body>
                    <div role="dialog" aria-modal="true" id="promo" style="position: fixed; z-index: 999; display: block;">
                        <p>Go Mobile with Salesforce</p>
                        <button onclick="document.getElementById('promo').remove()">Close</button>
                    </div>
                </body></html>
            """)

            state = WalkState(
                page=page1,
                clock=lambda: 0.0,
                popup_notes=[],
                step_deadline=5.0
            )

            step = _make_step()

            result = _promo_cleared(state, step)

            browser.close()

            # Promo on active tab: should be closed, result is None
            self.assertIsNone(result)
            # Check that the popup notes were added
            self.assertEqual(len(state.popup_notes), 1)
            self.assertEqual(state.popup_notes[0], PROMO_CLOSED_NOTE)


    def test_no_promo_item_on_any_tab_diagnostic(self):
        """(c) No promo item on any tab: bare note plus 'found on no tab (tabs 2)'."""
        # This tests the race condition case with mocking
        from unittest.mock import patch
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()

            # Two pages without promo
            page1 = context.new_page()
            page1.set_content("<html><body>Page 1</body></html>")

            page2 = context.new_page()
            page2.set_content("<html><body>Page 2</body></html>")

            state = WalkState(
                page=page1,
                clock=lambda: 0.0,
                popup_notes=[],
                step_deadline=5.0
            )

            step = _make_step()

            # Mock _promo_over to return True (simulating a promo was seen)
            # but _promo_item will return None (promo disappeared)
            with patch('workshop_validator.guide_walk._promo_over', return_value=True):
                result = _promo_cleared(state, step)

            browser.close()

            # Should return blocked with diagnostic
            self.assertIsNotNone(result)
            self.assertEqual(result.status, "ENV_BLOCKED")
            # Check for the "found on no tab (tabs 2)" diagnostic
            self.assertIn("found on no tab (tabs 2)", result.note)
            # Note should start with PROMO_NOTE
            self.assertTrue(result.note.startswith(PROMO_NOTE))

    def test_escape_pressed_on_promo_page_not_state_page(self):
        """(d) Escape is pressed on the promo's page, not state.page, when dismiss control is missing."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()

            # Main page (page 1)
            page1 = context.new_page()
            page1.set_content("<html><body><div id='main'>Main page</div></body></html>")

            # Secondary page (page 2) - has promo WITHOUT a dismiss button
            page2 = context.new_page()
            page2.set_content("""
                <html><body>
                    <div role="dialog" id="promo" aria-modal="true" style="position: fixed; z-index: 999;">
                        <p>Go Mobile with Salesforce</p>
                    </div>
                </body></html>
            """)

            # Add a keyboard listener to page2 to record Escape press
            page2.evaluate("""
                window.escapePressed = false;
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape') {
                        window.escapePressed = true;
                        // Remove the dialog to simulate successful close
                        document.getElementById('promo').remove();
                    }
                });
            """)

            # Add a keyboard listener to page1 to detect if Escape is wrongly pressed there
            page1.evaluate("""
                window.escapePressed = false;
                document.addEventListener('keydown', (e) => {
                    if (e.key === 'Escape') {
                        window.escapePressed = true;
                    }
                });
            """)

            state = WalkState(
                page=page1,
                clock=lambda: 0.0,
                popup_notes=[],
                step_deadline=5.0
            )

            step = _make_step()

            # Call _promo_cleared: should press Escape on page2, not page1
            result = _promo_cleared(state, step)

            # Check that Escape was pressed on page2
            escape_on_page2 = page2.evaluate("window.escapePressed")
            escape_on_page1 = page1.evaluate("window.escapePressed")

            browser.close()

            # After fix: Escape should be pressed on page2, not page1
            self.assertTrue(escape_on_page2, "Escape should be pressed on promo's page (page2)")
            self.assertFalse(escape_on_page1, "Escape should NOT be pressed on state.page (page1)")

            # Result should be None (successful close)
            self.assertIsNone(result)
            # Check popup notes
            self.assertEqual(len(state.popup_notes), 2)
            self.assertEqual(state.popup_notes[0], PROMO_CLOSED_NOTE)
            self.assertEqual(state.popup_notes[1], "Environment: the promotional pop-up was on another tab.")

    def test_promo_comes_back_on_other_tab_shows_correct_tree(self):
        """(e) Promo on page 2 comes back; unrelated dialog on page 1. Tree has button Close, not heading."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()

            # Page 1 - unrelated dialog with heading
            page1 = context.new_page()
            page1.set_content("""
                <html><body>
                    <div role="dialog" aria-modal="true">
                        <h2>Unrelated Dialog</h2>
                        <button>OK</button>
                    </div>
                </body></html>
            """)

            # Page 2 - promo that comes back after close
            page2 = context.new_page()
            page2.set_content("""
                <html><body>
                    <div role="dialog" id="promo" aria-modal="true" style="position: fixed; z-index: 999; display: block;">
                        <p>Go Mobile with Salesforce</p>
                        <button id="close-btn">Close</button>
                    </div>
                    <script>
                        var clickCount = 0;
                        document.getElementById('close-btn').onclick = function() {
                            clickCount++;
                            // First click: remove and immediately add back (simulates came-back)
                            var promo = document.getElementById('promo');
                            promo.remove();
                            if (clickCount === 1) {
                                // Add it back immediately so it's visible when re-checked
                                var newPromo = document.createElement('div');
                                newPromo.id = 'promo';
                                newPromo.setAttribute('role', 'dialog');
                                newPromo.setAttribute('aria-modal', 'true');
                                newPromo.style.cssText = 'position: fixed; z-index: 999; display: block;';
                                newPromo.innerHTML = '<p>Go Mobile with Salesforce</p><button id="close-btn">Close</button>';
                                document.body.appendChild(newPromo);
                                // Re-bind the handler
                                document.getElementById('close-btn').onclick = arguments.callee;
                            }
                        };
                    </script>
                </body></html>
            """)

            state = WalkState(
                page=page1,
                clock=lambda: 0.0,
                popup_notes=[],
                step_deadline=10.0
            )

            step = _make_step()

            # Call _promo_cleared: promo should be found on page2, Close clicked, but it comes back
            result = _promo_cleared(state, step)

            browser.close()

            # Should return ENV_BLOCKED (came back)
            self.assertIsNotNone(result)
            self.assertEqual(result.status, "ENV_BLOCKED")
            # Tree should have button "Close" from the promo on page2
            self.assertIn('button "Close"', result.note)
            # Tree should NOT have heading from unrelated dialog on page1
            self.assertNotIn("heading", result.note)

    def test_promo_on_other_tab_wont_close_diagnostic(self):
        """(f) Promo on page 2 won't close. Assert active tab no, Escape yes, still visible yes."""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()

            # Page 1 - no promo
            page1 = context.new_page()
            page1.set_content("<html><body>Page 1</body></html>")

            # Page 2 - promo WITHOUT dismiss button, Escape does nothing
            page2 = context.new_page()
            page2.set_content("""
                <html><body>
                    <div role="dialog" id="promo" aria-modal="true" style="position: fixed; z-index: 999;">
                        <p>Go Mobile with Salesforce</p>
                    </div>
                    <script>
                        // Escape does nothing - promo stays
                        document.addEventListener('keydown', (e) => {
                            if (e.key === 'Escape') {
                                e.preventDefault();
                                // Don't close
                            }
                        });
                    </script>
                </body></html>
            """)

            state = WalkState(
                page=page1,
                clock=lambda: 0.0,
                popup_notes=[],
                step_deadline=10.0
            )

            step = _make_step()

            # Call _promo_cleared: should press Escape on page2, but it won't close
            result = _promo_cleared(state, step)

            browser.close()

            # Should return ENV_BLOCKED with diagnostic
            self.assertIsNotNone(result)
            self.assertEqual(result.status, "ENV_BLOCKED")
            # Check diagnostic flags
            self.assertIn("promo on the active tab no", result.note)
            self.assertIn("Escape pressed yes", result.note)
            self.assertIn("still visible yes", result.note)


if __name__ == "__main__":
    unittest.main()
