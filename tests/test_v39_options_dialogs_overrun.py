"""V39: list options are picked by their first line inside the list the control owns (P005N011);
a dialog a passing guide step opened is never closed as a pop-up (P006N002); a step past its
time limit keeps a failing or frozen result (overrun)."""

import os
import unittest
from types import SimpleNamespace

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
SMOKE_REASON = "Set RUN_BROWSER_SMOKE=1 to run the real-browser checks."


SAMPLE_URL = "https://example.lightning.force.com/lightning/page/home"


def serve(page, html):
    """Show HTML at a synthetic org address. Every request is answered locally; no network."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(SAMPLE_URL)


def make_step(step_id, text):
    return GuideStep(step_id, 1, "Exercise 9: Sample", "Task 1: Sample.", "1", text,
                     (90.0, 10.0, 400.0, 24.0), None, ())


def lookup_html(options, outside=""):
    """A New Resource dialog with an Object lookup whose options show a label line and an API name line."""
    items = "".join(
        f'<div role="option" id="ob-{i}"><span class="label">{label}</span>'
        f'<span class="api" style="display:block">{api}</span></div>'
        for i, (label, api) in enumerate(options)
    )
    return f"""
{outside}
<div role="dialog" aria-modal="true" aria-labelledby="nr-title">
  <h2 id="nr-title">New Resource</h2>
  <label for="ob-box">Object</label>
  <input id="ob-box" role="combobox" aria-expanded="false" aria-controls="ob-list" autocomplete="off">
  <div id="ob-list" role="listbox" hidden></div>
</div>
<script>
  window.chosen = [];
  window.selects = true;
  const box = document.getElementById("ob-box");
  const list = document.getElementById("ob-list");
  function openList() {{
    list.innerHTML = {items!r};
    list.hidden = false;
    box.setAttribute("aria-expanded", "true");
  }}
  box.addEventListener("input", openList);
  box.addEventListener("click", openList);
  list.addEventListener("click", (event) => {{
    const option = event.target.closest("[role=option]");
    if (!option) return;
    window.chosen.push(option.querySelector(".api").textContent);
    if (!window.selects) return;
    box.value = option.querySelector(".label").textContent;
    box.setAttribute("aria-expanded", "false");
    list.hidden = true;
  }});
</script>
"""


DECOY_OUTSIDE = """
<div role="listbox" aria-label="Sample Recent Items">
  <div role="option" id="decoy">Opportunity</div>
</div>
<script>
  window.decoyClicks = 0;
  document.getElementById("decoy").addEventListener("click", () => { window.decoyClicks += 1; });
</script>
"""

OBJECTS = (
    ("Opportunity Change Event", "OpportunityChangeEvent"),
    ("Opportunity", "Opportunity"),
    ("Opportunity Contact Role", "OpportunityContactRole"),
)


DATA_TYPE_HTML = """
<div role="listbox" aria-label="Sample Other List">
  <div role="option" id="decoy">Record</div>
</div>
<div role="dialog" aria-modal="true" aria-labelledby="dt-title">
  <h2 id="dt-title">New Resource</h2>
  <span id="dt-lbl">Data Type</span>
  <button id="dt-box" aria-labelledby="dt-lbl" aria-haspopup="listbox" aria-expanded="false"
          aria-controls="dt-list">Select...</button>
  <div id="dt-list" role="listbox" hidden>
    <div role="option"><span class="label">Record Collection</span><span style="display:block">Many.</span></div>
    <div role="option"><span class="label">Record</span><span style="display:block">One sample row.</span></div>
  </div>
</div>
<script>
  window.chosen = [];
  window.decoyClicks = 0;
  document.getElementById("decoy").addEventListener("click", () => { window.decoyClicks += 1; });
  const box = document.getElementById("dt-box");
  const list = document.getElementById("dt-list");
  box.addEventListener("click", () => { list.hidden = false; box.setAttribute("aria-expanded", "true"); });
  list.addEventListener("click", (event) => {
    const option = event.target.closest("[role=option]");
    const label = option.querySelector(".label").textContent;
    window.chosen.push(label);
    box.textContent = label;
    box.setAttribute("aria-expanded", "false");
    list.hidden = true;
  });
</script>
"""


NEW_SCREEN_HTML = """
<button id="add">Add</button>
<div id="ns" role="dialog" aria-modal="true" aria-labelledby="ns-title" hidden>
  <h2 id="ns-title">New Screen</h2>
  <input type="search" aria-label="Search components">
  <p>No results found for the sample search.</p>
  <button id="ns-close">Close</button>
</div>
<script>
  document.getElementById("add").addEventListener("click", () => {
    document.getElementById("ns").hidden = false;
  });
  document.getElementById("ns-close").addEventListener("click", () => {
    document.getElementById("ns").hidden = true;
  });
</script>
"""


WELCOME_HTML = """
<label for="lbl" id="lbl-wrap" hidden>Label</label><input id="lbl" hidden>
<div id="wl" role="dialog" aria-modal="true" aria-labelledby="wl-title" hidden>
  <h2 id="wl-title">Welcome</h2>
  <p>Take a short sample tour of this page.</p>
  <button id="wl-close">Close</button>
</div>
<script>
  setTimeout(() => { document.getElementById("wl").hidden = false; }, 100);
  document.getElementById("wl-close").addEventListener("click", () => {
    document.getElementById("wl").hidden = true;
    document.getElementById("lbl").hidden = false;
    document.getElementById("lbl-wrap").hidden = false;
  });
</script>
"""


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class OwnedOptionBrowserTests(unittest.TestCase):
    """Real lookups and dropdowns: only the list the control owns is searched, by first line."""

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

    def open(self, html):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        serve(page, html)
        self.page = page
        return page

    def fill(self, html, text):
        page = self.open(html)
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        return guide_walk.attempt_step(state, make_step("obj", text))

    def test_object_option_is_picked_by_its_first_line_inside_its_own_list(self):
        result = self.fill(lookup_html(OBJECTS, outside=DECOY_OUTSIDE), "Object: Opportunity")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(self.page.evaluate("window.chosen"), ["Opportunity"])
        self.assertEqual(self.page.evaluate("window.decoyClicks"), 0)
        self.assertEqual(self.page.locator("#ob-box").input_value(), "Opportunity")
        self.assertEqual(self.page.locator("#ob-box").get_attribute("aria-expanded"), "false")
        self.assertFalse(self.page.locator("#ob-list").is_visible())

    def test_two_options_labelled_alike_are_never_clicked(self):
        twins = (("Opportunity", "Opportunity"), ("Opportunity", "SampleOpportunity__c"))
        result = self.fill(lookup_html(twins, outside=DECOY_OUTSIDE), "Object: Opportunity")
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(self.page.evaluate("window.decoyClicks"), 0)

    def test_click_that_does_not_set_the_value_is_a_frozen_mismatch(self):
        html = lookup_html(OBJECTS).replace("window.selects = true;", "window.selects = false;")
        result = self.fill(html, "Object: Opportunity")
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertIn("Object did not show Opportunity", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), ["Opportunity"])

    def test_dropdown_button_option_is_picked_inside_its_own_list(self):
        result = self.fill(DATA_TYPE_HTML, "Data Type: Record")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(self.page.evaluate("window.chosen"), ["Record"])
        self.assertEqual(self.page.evaluate("window.decoyClicks"), 0)


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class GuideDialogBrowserTests(unittest.TestCase):
    """A dialog the guide's own step opened stays open; an unrelated timed pop-up is still closed."""

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

    def walk(self, html, texts):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        serve(page, html)
        page.wait_for_timeout(300)
        self.page = page
        steps = tuple(make_step(f"s{i}", text) for i, text in enumerate(texts, 1))
        return walk_guide(page, SimpleNamespace(steps=steps), timeout_s=3, interval_s=0.2,
                          page_wait_s=1, page_poll_s=0.2)

    def test_miss_inside_the_dialog_a_step_opened_is_a_frozen_mismatch_not_a_close(self):
        outcome = self.walk(NEW_SCREEN_HTML, ("Click Add.", "Label: Sample", "Click Add."))
        self.assertEqual(outcome.statuses["s1"], "PASS")
        self.assertEqual(outcome.statuses["s2"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["s3"], "NOT_RUN")
        self.assertNotIn("pop-up", outcome.notes["s2"])
        self.assertTrue(self.page.locator("#ns").is_visible())

    def test_timed_welcome_dialog_no_step_opened_is_still_closed(self):
        outcome = self.walk(WELCOME_HTML, ("Label: Sample",))
        self.assertEqual(outcome.statuses["s1"], "PASS")
        self.assertIn('closed "Welcome" pop-up', outcome.notes["s1"])
        self.assertFalse(self.page.locator("#wl").is_visible())
        self.assertEqual(self.page.locator("#lbl").input_value(), "Sample")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class OverrunTests(unittest.TestCase):
    """A step that ran past its time limit keeps a failing or frozen result."""

    def walk(self, result):
        clock = FakeClock()

        def slow(state, step):
            clock.now += 500  # far past min(page_wait_s + 30, 120)
            return result

        steps = (make_step("s1", "Sample step one."), make_step("s2", "Sample step two."))
        check = GuideCheck("slow", lambda step: step.step_id == "s1", slow)
        page = SimpleNamespace(url="https://example.my.salesforce.com", frames=[])
        return walk_guide(page, SimpleNamespace(steps=steps), checks=[check], clock=clock,
                          sleeper=lambda seconds: None, page_wait_s=60)

    def test_frozen_mismatch_after_the_limit_keeps_status_and_freeze(self):
        outcome = self.walk(CheckResult("UI_MISMATCH", "Could not find Sample.", (), freeze=True))
        self.assertEqual(outcome.statuses["s1"], "UI_MISMATCH")
        self.assertIn("Could not find Sample.", outcome.notes["s1"])
        self.assertIn("ran past its time limit", outcome.notes["s1"])
        self.assertEqual(outcome.statuses["s2"], "NOT_RUN")
        self.assertIn("Could not find Sample.", outcome.notes["s2"])

    def test_functional_fail_after_the_limit_is_kept(self):
        outcome = self.walk(CheckResult("FUNCTIONAL_FAIL", "Sample value was not saved.", ()))
        self.assertEqual(outcome.statuses["s1"], "FUNCTIONAL_FAIL")

    def test_pass_after_the_limit_is_still_not_run(self):
        outcome = self.walk(CheckResult("PASS", "Sample.", ("s1:sample",)))
        self.assertEqual(outcome.statuses["s1"], "NOT_RUN")
        self.assertIn("exceeded its time limit", outcome.notes["s1"])


if __name__ == "__main__":
    unittest.main()
