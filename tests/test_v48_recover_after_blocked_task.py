"""W-142: recover from guide's own dialog left open after blocked task.

After a task is blocked (W-135), a dialog the guide opened in that task can still cover the
page when the next task starts. The walk now tries Escape, then return_home, before stopping.

Real browser tests with synthetic HTML only. No clicks inside the dialog.
"""

import os
import unittest
from types import SimpleNamespace

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide

from tests.test_guide_walk import Clock, Page, Loc
from unittest.mock import patch

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
EX_A = "Exercise 1: Sample Alpha"
HOME = "https://example.lightning.force.com/lightning/page/home"


def _step(step_id, exercise, task, number, text=None):
    return GuideStep(step_id, 1, exercise, task, number, text or f"Do sample action {step_id}.",
                     (90.0, 10.0, 400.0, 24.0), None, ())


def _ok(state, step):
    return CheckResult("PASS", "done", (f"{step.step_id}:sample-done",))


def _frozen(status, note="Sample control was not found."):
    return lambda state, step: CheckResult(status, note, (), freeze=True)


def _add_modal_dialog(page, title, *buttons):
    """Add a modal dialog with buttons to a Page instance (aria-modal=true)."""
    from workshop_validator.guide_walk import norm
    dialog = ("dialog", f"{title}\nSample description")
    inside = [("h2", title), *(("button", name) for name in buttons)]
    page.roles.add(dialog)
    page.roles.update(inside)
    page.children[dialog] = inside
    # Mark as guide dialog
    page._guide_dialogs = {norm(title)}
    # Store modal dialogs so get_attribute_patched can detect them
    if not hasattr(page, "_modal_dialogs"):
        page._modal_dialogs = set()
    page._modal_dialogs.add(dialog)
    return dialog


# Patch get_attribute to handle aria-modal for modal dialogs
_original_get_attribute = Loc.get_attribute


def _get_attribute_patched(self, name, timeout=None):
    if name == "aria-modal" and hasattr(self.page, "_modal_dialogs"):
        if (self.kind, self.name) in self.page._modal_dialogs:
            return "true"
    return _original_get_attribute(self, name, timeout)


def _walk(page, items, results, use_real_time=False):
    """RESULTS maps a step id to a run(state, step) function; every other step passes."""
    calls = []

    def run(state, step):
        calls.append(step.step_id)
        return results.get(step.step_id, _ok)(state, step)

    if use_real_time:
        # For browser tests: use real time and sleep
        outcome = walk_guide(
            page, GuideInventory(1, tuple(items), (), ()),
            checks=(GuideCheck("sample", lambda item: True, run),),
            timeout_s=0, interval_s=1, page_wait_s=5,
        )
    else:
        # For fake tests: use fake clock
        clock = Clock()
        outcome = walk_guide(
            page, GuideInventory(1, tuple(items), (), ()),
            checks=(GuideCheck("sample", lambda item: True, run),),
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep, page_wait_s=5,
        )
    return outcome, calls


TWO_TASKS = (
    _step("a1", EX_A, "Task 1: Sample one", "1"),
    _step("a2", EX_A, "Task 1: Sample one", "2"),
    _step("a3", EX_A, "Task 1: Sample one", "3"),
    _step("b1", EX_A, "Task 2: Sample two", "1"),
    _step("b2", EX_A, "Task 2: Sample two", "2"),
)


class FakeTests(unittest.TestCase):
    """Fake-page tests to prove the logic; real browser tests prove it works in a browser."""

    def setUp(self):
        """Patch Loc.get_attribute to handle aria-modal."""
        self.patcher = patch.object(Loc, 'get_attribute', _get_attribute_patched)
        self.patcher.start()

    def tearDown(self):
        """Restore original get_attribute."""
        self.patcher.stop()

    def test_no_recovery_when_no_task_blocked(self):
        """No Escape or navigation when no task was blocked (dialog open mid-task)."""
        page = Page(HOME)
        # Add a dialog that does not close on Escape
        _add_modal_dialog(page, "New Sample Screen", "Cancel")

        # All steps in the same task; no task is blocked
        same_task_steps = (
            _step("a1", EX_A, "Task 1: Sample one", "1"),
            _step("a2", EX_A, "Task 1: Sample one", "2"),
            _step("a3", EX_A, "Task 1: Sample one", "3"),
        )
        outcome, calls = _walk(page, same_task_steps, {})
        # All steps run (no recovery triggered)
        self.assertEqual(calls, ["a1", "a2", "a3"])
        self.assertEqual(outcome.statuses["a1"], "PASS")
        self.assertEqual(outcome.statuses["a2"], "PASS")
        self.assertEqual(outcome.statuses["a3"], "PASS")
        # No recovery note on any step
        self.assertNotIn("pressed Escape", outcome.notes.get("a1", ""))
        self.assertNotIn("pressed Escape", outcome.notes.get("a2", ""))
        self.assertNotIn("pressed Escape", outcome.notes.get("a3", ""))

    def test_dialog_closes_on_escape_task_runs(self):
        """A guide dialog that closes on Escape: task B runs with recovery note."""
        page = Page(HOME)
        dialog = _add_modal_dialog(page, "New Sample Screen")

        # When Escape is pressed, remove the dialog
        def on_key(key):
            if key == "Escape":
                page.roles.discard(dialog)
                page._modal_dialogs.discard(dialog)
        page.on_key = on_key

        outcome, calls = _walk(page, TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "Sample field was not found.")
        })
        # a2 frozen blocks a3, but b1 and b2 run
        self.assertEqual(outcome.statuses["a2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertEqual(outcome.statuses["b2"], "PASS")
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        # b1 carries the recovery note
        self.assertIn("New Sample Screen", outcome.notes["b1"])
        self.assertIn("pressed Escape", outcome.notes["b1"])
        self.assertIn("blocked task a2", outcome.notes["b1"])

    def test_dialog_ignores_escape_return_home_clears(self):
        """A dialog that ignores Escape; return_home clears it: task B runs, note names both actions."""
        from workshop_validator import guide_walk as gw_module

        page = Page(HOME)
        dialog = _add_modal_dialog(page, "New Sample Screen")

        # Dialog ignores Escape (no on_key handler)
        # Mock return_home to remove the dialog and return True
        original_return_home = gw_module.return_home
        def mock_return_home(state):
            page.roles.discard(dialog)
            page._modal_dialogs.discard(dialog)
            return True

        with patch.object(gw_module, 'return_home', side_effect=mock_return_home):
            outcome, calls = _walk(page, TWO_TASKS, {
                "a2": _frozen("UI_MISMATCH", "Sample control not found.")
            })

        self.assertEqual(outcome.statuses["a2"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        self.assertEqual(outcome.statuses["b1"], "PASS")
        self.assertEqual(outcome.statuses["b2"], "PASS")
        self.assertEqual(calls, ["a1", "a2", "b1", "b2"])
        # b1 carries both actions in the note
        self.assertIn("New Sample Screen", outcome.notes["b1"])
        self.assertIn("pressed Escape", outcome.notes["b1"])
        self.assertIn("returned to the org home", outcome.notes["b1"])
        self.assertIn("blocked task a2", outcome.notes["b1"])

    def test_dialog_survives_both_walk_stops(self):
        """A dialog that survives Escape and return_home: walk stops with today's note."""
        page = Page(HOME)
        _add_modal_dialog(page, "New Sample Screen")
        # Dialog never goes away (no on_escape or on_goto handlers)

        outcome, calls = _walk(page, TWO_TASKS, {
            "a2": _frozen("FUNCTIONAL_FAIL", "Sample change did not happen.")
        })
        self.assertEqual(outcome.statuses["a2"], "FUNCTIONAL_FAIL")
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        # b1 and b2 are NOT_RUN with the stop note
        self.assertEqual(outcome.statuses["b1"], "NOT_RUN")
        self.assertEqual(outcome.statuses["b2"], "NOT_RUN")
        self.assertEqual(calls, ["a1", "a2"])
        # b1 has the stop note
        self.assertIn("still covered the page after the failure in a2", outcome.notes["b1"])
        self.assertIn("New Sample Screen", outcome.notes["b1"])

    def test_later_step_needing_blocked_state_fails_on_its_own(self):
        """A step in task B that needed blocked task's state fails, is never PASS."""
        page = Page(HOME)
        dialog = _add_modal_dialog(page, "New Sample Screen")

        def on_key(key):
            if key == "Escape":
                page.roles.discard(dialog)
                page._modal_dialogs.discard(dialog)
        page.on_key = on_key

        outcome, calls = _walk(page, TWO_TASKS, {
            "a2": _frozen("NEEDS_REVIEW", "Sample field was not found."),
            "b1": _frozen("UI_MISMATCH", "Sample screen was not found (needs state from a2).")
        })
        # a2 frozen blocks a3; dialog recovers, b1 runs and fails, b2 is blocked
        self.assertEqual(outcome.statuses["a2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.statuses["a3"], "NOT_RUN")
        self.assertEqual(outcome.statuses["b1"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["b2"], "NOT_RUN")
        self.assertEqual(calls, ["a1", "a2", "b1"])
        # b1 is not PASS, it has the recovery note and its own failure
        self.assertIn("Sample screen was not found", outcome.notes["b1"])
        self.assertIn("pressed Escape", outcome.notes["b1"])


@unittest.skipUnless(SMOKE, "RUN_BROWSER_SMOKE=1 not set")
class BrowserTests(unittest.TestCase):
    """Real browser tests on synthetic HTML."""

    def test_escape_closes_dialog_no_clicks_inside(self):
        """Escape closes the dialog; no clicks happen inside it (listener counts 0)."""
        from workshop_validator.guide_walk import _open_guide_dialog

        html = """<!doctype html><html><head><title>Sample Home</title></head><body>
        <main><h1>Sample Home</h1><p>Made-up home page.</p></main>
        <div id="new-screen" role="dialog" aria-modal="true" aria-labelledby="screen-title"
          style="position:fixed;inset:0;background:rgba(255,255,255,0.96);z-index:50">
          <h2 id="screen-title">New Sample Screen</h2>
          <p>Made-up screen builder.</p>
          <button id="cancel-btn">Cancel</button>
          <button id="save-btn">Save</button>
        </div>
        <script>
          window.dialogClicks = 0;
          document.getElementById('new-screen').addEventListener('click', () => {
            window.dialogClicks += 1;
          }, true);
          document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
              document.getElementById('new-screen').remove();
            }
          });
        </script></body></html>"""

        home_url = "https://example.lightning.force.com/lightning/page/home"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            # Navigate to org URL first, then set content
            page.goto(home_url)
            page.set_content(html)

            # Simulate a1 opening the dialog (record it as a guide dialog)
            def a1_opens_dialog(state, step):
                _open_guide_dialog(state, "New Sample Screen")
                return CheckResult("PASS", "done", (f"{step.step_id}:sample-done",))

            outcome, calls = _walk(page, TWO_TASKS, {
                "a1": a1_opens_dialog,
                "a2": _frozen("NEEDS_REVIEW", "Sample field not found.")
            }, use_real_time=True)

            # Check no clicks inside dialog
            click_count = page.evaluate("() => window.dialogClicks")
            self.assertEqual(click_count, 0, "No clicks should happen inside the dialog")

            # Check recovery happened
            self.assertEqual(outcome.statuses["b1"], "PASS")
            self.assertIn("pressed Escape", outcome.notes["b1"])
            self.assertIn("New Sample Screen", outcome.notes["b1"])

            browser.close()

    def test_return_home_after_escape_fails(self):
        """Dialog ignores Escape; return_home clears it; note names both actions."""
        from workshop_validator.guide_walk import _open_guide_dialog
        from workshop_validator import guide_walk as gw_module

        html_with_dialog = """<!doctype html><html><head><title>Sample Home</title></head><body>
        <main><h1>Sample Home</h1><p>Made-up home page.</p></main>
        <div id="new-screen" role="dialog" aria-modal="true" aria-labelledby="screen-title"
          style="position:fixed;inset:0;background:rgba(255,255,255,0.96);z-index:50">
          <h2 id="screen-title">New Sample Screen</h2>
          <p>Made-up screen builder.</p>
          <button>Cancel</button>
          <button>Save</button>
        </div>
        <script>
          window.dialogClicks = 0;
          document.getElementById('new-screen').addEventListener('click', () => {
            window.dialogClicks += 1;
          }, true);
          // Dialog ignores Escape (no keydown handler)
        </script></body></html>"""

        html_without_dialog = """<!doctype html><html><head><title>Sample Home</title></head><body>
        <main><h1>Sample Home</h1><p>Made-up home page.</p></main>
        <script>
          window.dialogClicks = 0;
        </script></body></html>"""

        home_url = "https://example.lightning.force.com/lightning/page/home"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.goto(home_url)
            page.set_content(html_with_dialog)

            # Mock return_home to navigate and remove the dialog
            original_return_home = gw_module.return_home
            def mock_return_home(state):
                # Navigate away and back, removing the dialog
                state.page.set_content(html_without_dialog)
                return True

            # Simulate a1 opening the dialog (record it as a guide dialog)
            def a1_opens_dialog(state, step):
                _open_guide_dialog(state, "New Sample Screen")
                return CheckResult("PASS", "done", (f"{step.step_id}:sample-done",))

            with patch.object(gw_module, 'return_home', side_effect=mock_return_home):
                outcome, calls = _walk(page, TWO_TASKS, {
                    "a1": a1_opens_dialog,
                    "a2": _frozen("UI_MISMATCH", "Sample control not found.")
                }, use_real_time=True)

            # Check no clicks inside dialog
            click_count = page.evaluate("() => window.dialogClicks")
            self.assertEqual(click_count, 0, "No clicks should happen inside the dialog")

            # Check both recovery actions in note
            self.assertEqual(outcome.statuses["b1"], "PASS")
            self.assertIn("pressed Escape", outcome.notes["b1"])
            self.assertIn("returned to the org home", outcome.notes["b1"])
            self.assertIn("New Sample Screen", outcome.notes["b1"])

            browser.close()

    def test_dialog_survives_both_actions_walk_stops(self):
        """Dialog survives Escape and return_home; walk stops with today's note."""
        from workshop_validator.guide_walk import _open_guide_dialog

        # A non-dismissible dialog (ignores Escape, stays after navigation)
        html = """<!doctype html><html><head><title>Sample Home</title></head><body>
        <main><h1>Sample Home</h1><p>Made-up home page.</p></main>
        <div id="sticky-dialog" role="dialog" aria-modal="true" aria-labelledby="sticky-title"
          style="position:fixed;inset:0;background:rgba(255,255,255,0.96);z-index:50">
          <h2 id="sticky-title">Sticky Sample Dialog</h2>
          <p>Made-up persistent dialog.</p>
        </div>
        <script>
          // Dialog ignores Escape and survives navigation
          window.dialogClicks = 0;
          document.getElementById('sticky-dialog').addEventListener('click', () => {
            window.dialogClicks += 1;
          }, true);
        </script></body></html>"""

        home_url = "https://example.lightning.force.com/lightning/page/home"

        with sync_playwright() as p:
            browser = p.chromium.launch()
            context = browser.new_context()
            page = context.new_page()

            def handle_route(route):
                if "example.lightning.force.com" in route.request.url:
                    # Return HTML with the same dialog still present
                    route.fulfill(body=html, content_type="text/html")
                else:
                    route.continue_()

            page.route("**/*", handle_route)
            page.goto(home_url)
            page.set_content(html)

            # Simulate a1 opening the dialog (record it as a guide dialog)
            def a1_opens_dialog(state, step):
                _open_guide_dialog(state, "Sticky Sample Dialog")
                return CheckResult("PASS", "done", (f"{step.step_id}:sample-done",))

            outcome, calls = _walk(page, TWO_TASKS, {
                "a1": a1_opens_dialog,
                "a2": _frozen("FUNCTIONAL_FAIL", "Sample change did not happen.")
            }, use_real_time=True)

            # Check no clicks inside dialog
            click_count = page.evaluate("() => window.dialogClicks")
            self.assertEqual(click_count, 0, "No clicks should happen inside the dialog")

            # Walk should stop
            self.assertEqual(calls, ["a1", "a2"])
            self.assertEqual(outcome.statuses["b1"], "NOT_RUN")
            self.assertIn("still covered the page after the failure in a2", outcome.notes["b1"])
            self.assertIn("Sticky Sample Dialog", outcome.notes["b1"])

            browser.close()


if __name__ == "__main__":
    unittest.main()
