"""V32 / W-080: a promotional dialog is closed with its own Close control (or Escape),
noted on the step, and only the blocked action is resumed. Its promoted action is never clicked."""

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from tests.test_guide_walk import Clock, Page, as_builder, inventory_of, step, walk_guide

FLOWS_URL = "https://example.lightning.force.com/lightning/setup/Flows/home"
BUILDER_URL = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
PROMO = "Sample Promo Heading\nGo Mobile\nClose"
OTHER_PROMO = "Another Sample Promo\nGo Mobile"
PROMO_NOTE = "A promotional dialog covered the page; the guide names no control to close it."
CLOSED_NOTE = "Environment: closed a promotional pop-up the guide does not mention (owner rule W-080)."
AFTER_NOTE = (
    "Environment: after the pop-up was closed, the step's action still did not complete. "
    "This is not counted against the guide."
)


def state_for(page):
    clock = Clock()
    return guide_walk.WalkState(
        page=page, timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        page_wait_s=0, page_poll_s=1,
    )


def flow_step():
    return step("flow", "Exercise 9: Sample", "Task 1: Sample.", "2", "Create a new Screen Flow.")


def variable_step():
    return step("var", "Exercise 9: Sample", "Task 1: Sample.", "3",
                "Create a record variable to hold sample data.")


def promo_page(url, body="", buttons=("Close", "Go Mobile"), dialog=PROMO):
    page = Page(url, body=body)
    page.roles.add(("dialog", dialog))
    inside = []
    for name in buttons:
        page.roles.add(("button", name))
        inside.append(("button", name))
    page.children[("dialog", dialog)] = inside
    return page


def clicks(page):
    return [event[1:] for event in page.events if event[0] == "click"]


def flows_list(page, *, promo_returns=False, close_works=True):
    """Close removes the promo; New Flow, Screen Flow and Create then open a synthetic builder."""
    page.roles.add(("button", "New Flow"))

    def reveal(kind, name):
        if name == "Close" and close_works:
            page.roles.discard(("dialog", PROMO))
        if name == "New Flow":
            page.body = "New Automation Screen Flow"
            if promo_returns:
                page.roles.add(("dialog", PROMO))
        if name == "Screen Flow":
            page.roles.add(("button", "Create"))
        if name == "Create":
            page.url = BUILDER_URL
            page.body = "Sample builder"
            as_builder(page)

    page.on_click = reveal
    return page


class PromoCloseTests(unittest.TestCase):
    def test_promo_with_close_is_closed_and_new_flow_resumed_once(self):
        page = flows_list(promo_page(FLOWS_URL))
        state = state_for(page)
        result = guide_walk.run_screen_flow(state, flow_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.evidence_ids, ("flow:screen-flow",))
        self.assertEqual(state.popup_notes, [CLOSED_NOTE])
        self.assertEqual(clicks(page)[0], ("button", "Close"))
        self.assertEqual(clicks(page).count(("button", "New Flow")), 1)
        self.assertEqual(clicks(page).count(("button", "Close")), 1)
        self.assertNotIn(("button", "Go Mobile"), clicks(page))

    def test_walk_notes_the_close_and_passes_only_on_the_resumed_action(self):
        page = flows_list(promo_page(FLOWS_URL))
        clock = Clock()
        outcome = walk_guide(page, inventory_of(flow_step()), timeout_s=0, interval_s=1,
                             clock=clock, sleeper=clock.sleep)
        self.assertEqual(outcome.statuses["flow"], "PASS")
        self.assertTrue(outcome.notes["flow"].startswith(CLOSED_NOTE))
        self.assertEqual(outcome.evidence["flow"], ("flow:screen-flow",))
        self.assertNotIn(("button", "Go Mobile"), clicks(page))

    def test_promo_without_dismiss_and_escape_failing_stays_blocked(self):
        page = flows_list(promo_page(FLOWS_URL, buttons=("Go Mobile",)))
        state = state_for(page)
        result = guide_walk.run_screen_flow(state, flow_step())
        self.assertEqual(result.status, "ENV_BLOCKED")
        self.assertTrue(result.freeze)
        self.assertEqual(result.note, PROMO_NOTE)
        self.assertEqual(page.keys, ["Escape"])
        self.assertEqual(clicks(page), [])
        self.assertEqual(state.popup_notes, [])

    def test_close_that_does_not_remove_the_promo_stays_blocked(self):
        page = flows_list(promo_page(FLOWS_URL), close_works=False)
        result = guide_walk.run_screen_flow(state_for(page), flow_step())
        self.assertEqual((result.status, result.note), ("ENV_BLOCKED", PROMO_NOTE))
        self.assertEqual(clicks(page), [("button", "Close")])

    def test_promo_with_escape_closing_it_is_resumed(self):
        page = flows_list(promo_page(FLOWS_URL, buttons=("Go Mobile",)))
        page.on_key = lambda key: page.roles.discard(("dialog", PROMO))
        state = state_for(page)
        result = guide_walk.run_screen_flow(state, flow_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(page.keys, ["Escape"])
        self.assertEqual(state.popup_notes, [CLOSED_NOTE])
        self.assertNotIn(("button", "Go Mobile"), clicks(page))

    def test_promo_coming_back_after_close_stays_blocked(self):
        page = flows_list(promo_page(FLOWS_URL), promo_returns=True)
        result = guide_walk.run_screen_flow(state_for(page), flow_step())
        self.assertEqual((result.status, result.note), ("ENV_BLOCKED", PROMO_NOTE))
        self.assertTrue(result.freeze)
        self.assertEqual(clicks(page).count(("button", "Close")), 1)
        self.assertNotIn(("button", "Go Mobile"), clicks(page))

    def test_new_flow_appearing_late_after_close_is_waited_for(self):
        page = flows_list(promo_page(FLOWS_URL))
        page.roles.discard(("button", "New Flow"))
        state = state_for(page)
        state.page_wait_s = 10
        tick = state.sleeper

        def sleep(seconds):
            tick(seconds)
            if ("dialog", PROMO) not in page.roles:
                page.roles.add(("button", "New Flow"))

        state.sleeper = sleep
        result = guide_walk.run_screen_flow(state, flow_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(clicks(page).count(("button", "New Flow")), 1)

    def test_new_flow_missing_after_close_is_not_a_guide_failure(self):
        page = flows_list(promo_page(FLOWS_URL))
        page.roles.discard(("button", "New Flow"))
        result = guide_walk.run_screen_flow(state_for(page), flow_step())
        self.assertEqual((result.status, result.note), ("ENV_BLOCKED", AFTER_NOTE))
        self.assertEqual(clicks(page), [("button", "Close")])

    def test_step_naming_the_promo_does_not_close_it(self):
        page = flows_list(promo_page(FLOWS_URL))
        named = step("flow", "Exercise 9: Sample", "Task 1: Sample.", "2", "Click Go Mobile.")
        self.assertIsNone(guide_walk._promo_cleared(state_for(page), named))
        self.assertEqual(clicks(page), [])
        self.assertEqual(page.keys, [])

    def test_promo_over_the_builder_is_closed_and_the_step_resumes(self):
        page = as_builder(promo_page(BUILDER_URL))
        page.on_click = lambda kind, name: (
            page.roles.discard(("dialog", PROMO)) if name == "Close" else
            page.roles.add(("button", "New Resource")) if name == "Toggle Toolbox" else
            page.roles.add(("option", "Variable")) if name == "New Resource" else
            page.roles.add(("textbox", "API Name")) if name == "Variable" else None
        )
        state = state_for(page)
        result = guide_walk.run_record_variable(state, variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(state.popup_notes, [CLOSED_NOTE])
        self.assertEqual(clicks(page)[0], ("button", "Close"))
        self.assertNotIn(("button", "Go Mobile"), clicks(page))

    def test_promo_replaced_right_after_close_blocks_the_builder_gate(self):
        page = as_builder(promo_page(BUILDER_URL))

        def swap(kind, name):
            if name == "Close":
                page.roles.discard(("dialog", PROMO))
                page.roles.add(("dialog", OTHER_PROMO))

        page.on_click = swap
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual((result.status, result.note), ("ENV_BLOCKED", PROMO_NOTE))
        self.assertEqual(clicks(page), [("button", "Close")])

    def test_builder_not_open_after_close_is_not_a_guide_failure(self):
        page = promo_page(FLOWS_URL, body="Flows")
        page.on_click = lambda kind, name: page.roles.discard(("dialog", PROMO)) if name == "Close" else None
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual((result.status, result.note), ("ENV_BLOCKED", AFTER_NOTE))


PROMO_HTML = """
<button id="new-flow" onclick="window.clicked.push('New Flow')">New Flow</button>
<div role="dialog" aria-modal="true" id="promo"
     style="position:fixed;inset:0;background:#fff">
  <h2>Sample Promo Heading</h2>
  <button onclick="window.clicked.push('Go Mobile')">Go Mobile</button>
  {close}
</div>
<script>
  window.clicked = [];
  document.addEventListener('keydown', e => {{
    if (e.key === 'Escape' && window.escapeCloses) document.getElementById('promo').remove();
  }});
</script>
"""
CLOSE_BUTTON = ("<button aria-label=\"Close\" "
                "onclick=\"window.clicked.push('Close');this.parentElement.remove()\">X</button>")


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class PromoCloseBrowserTests(unittest.TestCase):
    """The close runs in a real browser, so the dismiss lookup and Escape are checked, not only the fake."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(
            headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None,
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def run_page(self, close, escape_closes=False):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(PROMO_HTML.format(close=close))
        page.evaluate(f"window.escapeCloses = {str(escape_closes).lower()}")
        state = guide_walk.WalkState(page=page, timeout_s=2, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        blocked = guide_walk._wait_past_promo(
            state, flow_step(), lambda: guide_walk._named_choice(page, "New Flow"), "New Flow missing."
        )
        resumed = blocked is None and guide_walk._click_last(page, "button", "New Flow")
        return blocked, resumed, page.evaluate("window.clicked"), state.popup_notes

    def test_close_button_closes_the_promo_and_new_flow_is_resumed(self):
        blocked, resumed, clicked, notes = self.run_page(CLOSE_BUTTON)
        self.assertIsNone(blocked)
        self.assertTrue(resumed)
        self.assertEqual(clicked, ["Close", "New Flow"])
        self.assertEqual(notes, [CLOSED_NOTE])

    def test_no_dismiss_and_escape_failing_stays_blocked(self):
        blocked, resumed, clicked, notes = self.run_page("")
        self.assertEqual((blocked.status, blocked.note), ("ENV_BLOCKED", PROMO_NOTE))
        self.assertFalse(resumed)
        self.assertEqual(clicked, [])
        self.assertEqual(notes, [])

    def test_escape_closes_a_promo_with_no_dismiss_control(self):
        blocked, resumed, clicked, notes = self.run_page("", escape_closes=True)
        self.assertIsNone(blocked)
        self.assertEqual(clicked, ["New Flow"])
        self.assertEqual(notes, [CLOSED_NOTE])


if __name__ == "__main__":
    unittest.main()
