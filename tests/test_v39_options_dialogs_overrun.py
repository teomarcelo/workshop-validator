"""V39: list options are picked by their first line inside the list the control owns (P005N011);
a dialog a passing guide step opened is never closed as a pop-up (P006N002); a step past its
time limit keeps a failing or frozen result (overrun)."""

import json
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


BUILDER_URL = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"


def serve(page, html, url=SAMPLE_URL):
    """Show HTML at a synthetic org address. Every request is answered locally; no network."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url)


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


BUILDER_SHELL = """
<builder_platform_interaction-canvas style="display:block;width:400px;height:120px">
  <button>Start</button>
</builder_platform_interaction-canvas>
<button>Toggle Toolbox</button>
"""


SCREEN_BUILDER_HTML = BUILDER_SHELL + """
<div role="listbox" aria-label="Sample Elements"><div role="option" id="screen-opt">Screen</div></div>
<div id="ns" role="dialog" aria-modal="true" aria-labelledby="ns-title" hidden>
  <h2 id="ns-title">New Screen</h2>
  <label for="ns-label">Label</label><input id="ns-label">
  <p>Sample screen properties for this test.</p>
  <button id="ns-close">Close</button>
</div>
<script>
  document.getElementById("screen-opt").addEventListener("click", () => {
    document.getElementById("ns").hidden = false;
  });
  document.getElementById("ns-close").addEventListener("click", () => {
    document.getElementById("ns").hidden = true;
  });
</script>
"""


RESOURCE_BUILDER_HTML = BUILDER_SHELL + """
<button id="nr-open">New Resource</button>
<div id="nr" role="dialog" aria-modal="true" aria-labelledby="nr-title" hidden>
  <h2 id="nr-title">New Resource</h2>
  <label for="nr-api">API Name</label><input id="nr-api">
  <p>Sample resource properties for this test.</p>
  <button id="nr-close">Close</button>
</div>
<script>
  document.getElementById("nr-open").addEventListener("click", () => {
    document.getElementById("nr").hidden = false;
  });
  document.getElementById("nr-close").addEventListener("click", () => {
    document.getElementById("nr").hidden = true;
  });
</script>
"""


CLICK_OPENS_WELCOME_HTML = """
<button id="open">Open Sample Panel</button>
<div id="panel" hidden>Sample panel is open.</div>
<label for="lbl" id="lbl-wrap" hidden>Label</label><input id="lbl" hidden>
<div id="wl" role="dialog" aria-modal="true" aria-labelledby="wl-title" hidden>
  <h2 id="wl-title">Welcome</h2>
  <p>Take a short sample tour of this page.</p>
  <button id="wl-close">Close</button>
</div>
<script>
  document.getElementById("open").addEventListener("click", () => {
    document.getElementById("panel").hidden = false;
    document.getElementById("wl").hidden = false;
  });
  document.getElementById("wl-close").addEventListener("click", () => {
    document.getElementById("wl").hidden = true;
    document.getElementById("lbl").hidden = false;
    document.getElementById("lbl-wrap").hidden = false;
  });
</script>
"""


TEXT_ONLY_DROPDOWN_HTML = """
<span id="dt-text">Data Type</span>
<button id="dt-box" aria-haspopup="listbox" aria-expanded="false">Select...</button>
<div id="dt-list" role="listbox" hidden>
  <div role="option"><span class="label">Record</span><span style="display:block">One sample row.</span></div>
</div>
<script>
  window.chosen = [];
  const list = document.getElementById("dt-list");
  document.getElementById("dt-text").addEventListener("click", () => { list.hidden = false; });
  list.addEventListener("click", (event) => {
    const option = event.target.closest("[role=option]");
    window.chosen.push(option.querySelector(".label").textContent);
  });
</script>
"""


def page_lookup_html(options, *, owner="input", pill=True, pill_text=None, pill_outside=False):
    """An Object lookup outside any dialog, inside a form element, with a decoy list elsewhere.

    OWNER says where the list is named: "input" (aria-controls on the input), "ancestor"
    (aria-owns on the combobox ancestor) or "none". With PILL, choosing an option hides the input
    and shows a selected pill (PILL_TEXT, default the option label); PILL_OUTSIDE puts that pill
    in a different form element.
    """
    items = "".join(
        f'<div role="option" id="pl-{i}"><span class="label">{label}</span>'
        f'<span class="api" style="display:block">{api}</span></div>'
        for i, (label, api) in enumerate(options)
    )
    on_input = ' aria-controls="pl-list"' if owner == "input" else ""
    on_ancestor = ' aria-owns="pl-list"' if owner == "ancestor" else ""
    pill_home = "other-field" if pill_outside else "pill-slot"
    return f"""
<div role="listbox" aria-label="Sample Recent Items">
  <div role="option" id="decoy">Opportunity</div>
</div>
<div class="slds-form-element">
  <label for="pl-box">Object</label>
  <div role="combobox" aria-expanded="false" aria-haspopup="listbox"{on_ancestor}>
    <input id="pl-box" role="combobox" aria-expanded="false"{on_input} autocomplete="off">
  </div>
  <div id="pill-slot"></div>
  <div id="pl-list" role="listbox" hidden></div>
</div>
<div class="slds-form-element"><span>Sample Other Field</span><div id="other-field"></div></div>
<script>
  window.chosen = [];
  window.decoyClicks = 0;
  document.getElementById("decoy").addEventListener("click", () => {{ window.decoyClicks += 1; }});
  const box = document.getElementById("pl-box");
  const list = document.getElementById("pl-list");
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
    const label = option.querySelector(".label").textContent;
    window.chosen.push(option.querySelector(".api").textContent);
    box.setAttribute("aria-expanded", "false");
    list.hidden = true;
    if ({str(bool(pill)).lower()}) {{
      box.value = "";
      box.hidden = true;
      const shown = {json.dumps(pill_text)} || label;
      document.getElementById({pill_home!r}).innerHTML =
        '<span class="slds-pill"><span class="slds-pill__label">' + shown + '</span>' +
        '<button>Remove</button></span>';
    }} else {{
      box.value = label;
    }}
  }});
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

    def test_page_lookup_named_by_its_combobox_ancestor_is_picked_in_that_list(self):
        html = page_lookup_html(OBJECTS, owner="ancestor", pill=False)
        result = self.fill(html, "Object: Opportunity")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(self.page.evaluate("window.chosen"), ["Opportunity"])
        self.assertEqual(self.page.evaluate("window.decoyClicks"), 0)

    def test_selected_pill_with_the_value_is_pass_evidence(self):
        result = self.fill(page_lookup_html(OBJECTS), "Object: Opportunity")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(self.page.evaluate("window.chosen"), ["Opportunity"])
        self.assertEqual(self.page.evaluate("window.decoyClicks"), 0)
        self.assertEqual(self.page.locator("#pill-slot .slds-pill__label").inner_text(), "Opportunity")

    def test_pill_with_other_text_is_not_evidence(self):
        result = self.fill(page_lookup_html(OBJECTS, pill_text="Opportunity Contact Role"), "Object: Opportunity")
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertIn("Object did not show Opportunity", result.note)

    def test_pill_in_another_field_is_not_evidence(self):
        result = self.fill(page_lookup_html(OBJECTS, pill_outside=True), "Object: Opportunity")
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertIn("Object did not show Opportunity", result.note)

    def test_page_lookup_that_names_no_list_is_refused_without_a_click(self):
        result = self.fill(page_lookup_html(OBJECTS, owner="none"), "Object: Opportunity")
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertTrue(result.freeze)
        self.assertIn("names no list of its own", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(self.page.evaluate("window.decoyClicks"), 0)

    def test_dropdown_found_by_visible_text_only_picks_no_option_and_says_so(self):
        page = self.open(TEXT_ONLY_DROPDOWN_HTML)
        steps = (make_step("s1", "Data Type: Record"),)
        outcome = walk_guide(page, SimpleNamespace(steps=steps), timeout_s=3, interval_s=0.2,
                             page_wait_s=1, page_poll_s=0.2)
        self.assertNotEqual(outcome.statuses["s1"], "PASS")
        self.assertIn("dropdown found by visible text only; option not picked", outcome.notes["s1"])
        self.assertEqual(page.evaluate("window.chosen"), [])


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

    def walk(self, html, texts, url=SAMPLE_URL, checks=None):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        serve(page, html, url)
        page.wait_for_timeout(300)
        self.page = page
        steps = tuple(make_step(f"s{i}", text) for i, text in enumerate(texts, 1))
        extra = {} if checks is None else {"checks": checks}
        return walk_guide(page, SimpleNamespace(steps=steps), timeout_s=3, interval_s=0.2,
                          page_wait_s=1, page_poll_s=0.2, **extra)

    def test_miss_inside_the_new_screen_the_screen_check_opened_is_a_frozen_mismatch(self):
        outcome = self.walk(
            SCREEN_BUILDER_HTML,
            ("Add a Screen Element with Label Sample Screen", "Sample Field: Sample", "Sample Field: Sample"),
            url=BUILDER_URL,
        )
        self.assertEqual(outcome.statuses["s1"], "PASS", outcome.notes.get("s1"))
        self.assertEqual(self.page.locator("#ns-label").input_value(), "Sample Screen")
        self.assertEqual(outcome.statuses["s2"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["s3"], "NOT_RUN")
        self.assertNotIn("pop-up", outcome.notes["s2"])
        self.assertTrue(self.page.locator("#ns").is_visible())

    def test_miss_inside_the_new_resource_the_variable_check_opened_is_a_frozen_mismatch(self):
        outcome = self.walk(
            RESOURCE_BUILDER_HTML,
            ("Create a record variable.", "Sample Field: Sample", "Sample Field: Sample"),
            url=BUILDER_URL,
        )
        self.assertEqual(outcome.statuses["s1"], "PASS", outcome.notes.get("s1"))
        self.assertEqual(outcome.statuses["s2"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["s3"], "NOT_RUN")
        self.assertNotIn("pop-up", outcome.notes["s2"])
        self.assertTrue(self.page.locator("#nr").is_visible())

    def test_welcome_opened_by_a_passing_steps_click_is_closed_by_the_next_step(self):
        def open_panel(state, step):
            state.page.locator("#open").click()
            state.page.locator("#panel").wait_for(state="visible", timeout=2000)
            return guide_walk.passed(step, "sample-panel", "Sample panel is open.")

        check = GuideCheck("open panel", lambda step: step.step_id == "s1", open_panel)
        outcome = self.walk(CLICK_OPENS_WELCOME_HTML, ("Open the sample panel.", "Label: Sample"), checks=[check])
        self.assertEqual(outcome.statuses["s1"], "PASS")
        self.assertEqual(outcome.statuses["s2"], "PASS", outcome.notes.get("s2"))
        self.assertIn('closed "Welcome" pop-up', outcome.notes["s2"])
        self.assertFalse(self.page.locator("#wl").is_visible())
        self.assertEqual(self.page.locator("#lbl").input_value(), "Sample")

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


STEP_CAP_S = 90  # min(page_wait_s + 30, 120) with page_wait_s=60
SAMPLE_PAGE = "https://example.my.salesforce.com"


def walk_checked(texts, run, clock, sleeper=None, scan=None, case=None):
    """Walk synthetic steps through one check RUN(state, step) on a fake page and fake clock."""
    steps = tuple(make_step(f"s{i}", text) for i, text in enumerate(texts, 1))
    check = GuideCheck("sample", lambda step: True, run)
    if scan is not None:
        original = guide_walk._visible_dialogs
        guide_walk._visible_dialogs = scan
        case.addCleanup(setattr, guide_walk, "_visible_dialogs", original)
    page = SimpleNamespace(url=SAMPLE_PAGE, frames=[])
    return walk_guide(page, SimpleNamespace(steps=steps), checks=[check], clock=clock,
                      sleeper=sleeper or (lambda seconds: None), page_wait_s=60, interval_s=2)


class TimedMissTests(unittest.TestCase):
    """A miss decided with no step time left is inconclusive; one decided with time left is kept."""

    def walk(self, run):
        clock = FakeClock()

        def sleeper(seconds):
            clock.now += seconds

        def first_only(state, step):
            if step.step_id == "s1":
                return run(state, step, clock)
            return guide_walk.passed(step, "sample", "Sample.")

        return walk_checked(("Sample step one.", "Sample step two."), first_only, clock, sleeper)

    @staticmethod
    def miss(state):
        limit = min(state.page_wait_s, 10, guide_walk._time_left(state))
        missed = guide_walk.require(state, lambda: False, "Sample control was not found.", limit=limit)
        return guide_walk._stop_here(missed)

    def test_miss_decided_after_the_time_was_spent_is_not_run_and_still_stops(self):
        def run(state, step, clock):
            clock.now += STEP_CAP_S + 5  # earlier work used the step's whole time
            return self.miss(state)

        outcome = self.walk(run)
        self.assertEqual(outcome.statuses["s1"], "NOT_RUN")
        self.assertIn("The step ran out of time before it could be checked.", outcome.notes["s1"])
        self.assertEqual(outcome.statuses["s2"], "NOT_RUN")
        self.assertIn("ran out of time before it could be checked", outcome.notes["s2"])
        self.assertEqual(outcome.executed, ("s1",))

    def test_miss_decided_exactly_at_the_limit_is_not_run(self):
        def run(state, step, clock):
            clock.now += STEP_CAP_S  # no time left, but not past the limit either
            return self.miss(state)

        outcome = self.walk(run)
        self.assertEqual(outcome.statuses["s1"], "NOT_RUN")
        self.assertIn("ran out of time before it could be checked", outcome.notes["s1"])
        self.assertEqual(outcome.executed, ("s1",))

    def test_miss_decided_with_time_left_is_kept_after_the_step_runs_long(self):
        def run(state, step, clock):
            result = self.miss(state)  # full 10 s wait, with most of the step's time left
            clock.now += STEP_CAP_S  # later work runs the step past its limit
            return result

        outcome = self.walk(run)
        self.assertEqual(outcome.statuses["s1"], "UI_MISMATCH")
        self.assertIn("Sample control was not found.", outcome.notes["s1"])
        self.assertIn("ran past its time limit", outcome.notes["s1"])
        self.assertNotIn("ran out of time", outcome.notes["s1"])
        self.assertEqual(outcome.statuses["s2"], "NOT_RUN")

    def test_timed_out_miss_does_not_carry_into_the_next_step(self):
        clock = FakeClock()

        def run(state, step):
            if step.step_id == "s1":
                clock.now += STEP_CAP_S + 5
                self.miss(state)  # the check moves on after this miss
                return guide_walk.passed(step, "recovered", "Sample recovered.")
            return CheckResult("FUNCTIONAL_FAIL", "Sample value was not saved.", ())

        outcome = walk_checked(("Sample step one.", "Sample step two."), run, clock)
        self.assertEqual(outcome.statuses["s1"], "NOT_RUN")
        self.assertEqual(outcome.statuses["s2"], "FUNCTIONAL_FAIL")


class DialogBookkeepingTests(unittest.TestCase):
    """Dialog bookkeeping never stops the walk and never scans for a skipped step."""

    def test_a_failing_dialog_scan_never_stops_the_walk(self):
        def broken(page):
            raise RuntimeError("sample scan error")

        def opening(state, step):
            guide_walk._open_guide_dialog(state, "Sample Dialog")
            return guide_walk.passed(step, "sample", "Sample.")

        outcome = walk_checked(("Sample step one.", "Sample step two."), opening, FakeClock(),
                               scan=broken, case=self)
        self.assertEqual(outcome.statuses, {"s1": "PASS", "s2": "PASS"})

    def test_a_recorded_dialog_is_forgotten_once_it_closes(self):
        open_titles = []
        seen = []

        def scan(page):
            return [(None, title, "sample dialog text") for title in open_titles]

        def run(state, step):
            if step.step_id == "s1":
                guide_walk._open_guide_dialog(state, "Sample Dialog")
                open_titles.append("Sample Dialog")
            else:
                seen.append(set(state.guide_dialogs))
                open_titles.clear()
            return guide_walk.passed(step, "sample", "Sample.")

        walk_checked(("Sample step.",) * 3, run, FakeClock(), scan=scan, case=self)
        self.assertEqual(seen, [{"sample dialog"}, set()])

    def test_skipped_steps_do_not_scan_for_dialogs(self):
        scans = []

        def counting(page):
            scans.append(1)
            return [(None, "Sample Dialog", "sample dialog text")]

        def opening(state, step):
            guide_walk._open_guide_dialog(state, "Sample Dialog")
            return guide_walk.passed(step, "sample", "Sample.")

        outcome = walk_checked(
            ("Sample step one.", "Change your password.", "Change your password."),
            opening, FakeClock(), scan=counting, case=self,
        )
        self.assertEqual(outcome.statuses["s2"], "NOT_RUN")
        self.assertEqual(outcome.statuses["s3"], "NOT_RUN")
        self.assertEqual(len(scans), 1)  # only the end of s1; nothing was recorded at its start


if __name__ == "__main__":
    unittest.main()
