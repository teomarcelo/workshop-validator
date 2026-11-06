"""V43 r5: every route to PASS for a lookup pick is closed to a pick shown only as a pill.

The only PASS in _pick_lookup_option is the closed read of the box (chosen(), not by pill). Two
ways a pill-only pick used to reach it:
1. A box that is not a form field (a div with role combobox) reads its own text as its value,
   so a pill inside it (open shadow roots included) read as the value. Such a box shows no value.
2. The walk types the wanted name before the click, so the first closed read after the click
   could be the typed text, a moment before the field re-renders into a pill. A pill near the
   field is now read first on every poll, and PASS needs the closed, value-showing read to hold
   on the same element for about a second (at least two polls).
Real browser, synthetic names only, every request answered locally.
"""

import os
import unittest
from types import SimpleNamespace

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.automate import INCONCLUSIVE, gate_outcome_of, outcome_of
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide
from workshop_validator.report import ReportStep

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
SMOKE_REASON = "Set RUN_BROWSER_SMOKE=1 to run the real-browser checks."

BUILDER_URL = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
STEP = "Record Variable: recThing"
MISS_NOTE = "Record Variable did not show recThing after its option was clicked."
PASS_NOTE = "Record Variable is recThing. The field shows it and its list closed."
REVIEW_NOTE = ("Record Variable option recThing was clicked and a selected pill labelled recThing "
               "appears near the field, but the validator cannot prove the pill belongs to this field; "
               "please confirm.")
PILL = ('<span class="slds-pill"><span class="slds-pill__label">recThing</span>'
        '<button class="slds-pill__remove" title="Remove">x</button></span>')
# The pill a div combobox renders inside itself: its label only, so the box's own text is recThing.
BARE = '<span class="slds-pill"><span class="slds-pill__label">recThing</span></span>'


def div_html(after_js, labelled=True):
    """A div with role combobox (not a form field). A click on it opens its list; the option click
    closes the list (aria-expanded "false") and runs AFTER_JS with rv, the box. LABELLED: named by
    aria-labelledby to a field label, else by aria-label only (no field container, no pill flag)."""
    name = ('aria-labelledby="lab"' if labelled else 'aria-label="Record Variable"')
    label = ('<span class="slds-form-element__label" id="lab">Record Variable</span>' if labelled else "")
    return f"""
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      {label}
      <div id="rv" role="combobox" tabindex="0" {name} aria-expanded="false" aria-controls="lb"
           style="display:block; min-width:120px; min-height:20px; border:1px solid #888"></div>
      <div id="lb" role="listbox" hidden>
        <div role="option" id="opt1">recThing</div>
      </div>
    </sample-field>
  </section>
</div>
<script>
  window.chosen = [];
  const rv = document.getElementById("rv");
  const list = document.getElementById("lb");
  rv.addEventListener("click", () => {{ list.hidden = false; rv.setAttribute("aria-expanded", "true"); }});
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    list.hidden = true;
    rv.setAttribute("aria-expanded", "false");
    {after_js}
  }});
</script>
"""


BARE_PILL = f"rv.innerHTML = {BARE!r};"
NO_EXPANDED = 'rv.removeAttribute("aria-expanded");'
# The box itself becomes the pill: its class is slds-pill and its text is recThing.
BOX_IS_PILL = "rv.className = 'slds-pill'; rv.textContent = 'recThing';"
# The box's own open shadow root holds the pill; its light text (slotted) is recThing.
OWN_SHADOW_PILL = (f"rv.textContent = 'recThing'; rv.attachShadow({{mode: 'open'}}).innerHTML = "
                   f"'<slot></slot>' + {BARE!r};")
# A part inside the box has an open shadow root holding the pill; the box's own text is recThing.
PART_SHADOW_PILL = ("rv.innerHTML = '<span>recThing</span><x-part></x-part>'; "
                    f"rv.querySelector('x-part').attachShadow({{mode: 'open'}}).innerHTML = {BARE!r};")


def typed_html(after_js):
    """An input lookup. The list opens when text is typed; the option click closes the list
    (aria-expanded "false") and runs AFTER_JS with rv (the input), host and the pill markup."""
    return f"""
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <label for="rv">Record Variable</label>
      <input id="rv" role="combobox" aria-expanded="false" aria-controls="lb" value="" autocomplete="off">
      <div id="lb" role="listbox" hidden>
        <div role="option" id="opt1">recThing</div>
      </div>
    </sample-field>
  </section>
</div>
<script>
  window.chosen = [];
  window.typed = [];
  const host = document.getElementById("host-rv");
  let rv = document.getElementById("rv");
  const list = document.getElementById("lb");
  const pillHtml = {PILL!r};
  rv.addEventListener("input", () => {{
    window.typed.push(rv.value);
    list.hidden = false;
    rv.setAttribute("aria-expanded", "true");
  }});
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    list.hidden = true;
    rv.setAttribute("aria-expanded", "false");
    {after_js}
  }});
</script>
"""


def pill_beside(delay_ms):
    """The typed text stays in the input; a pill labelled recThing appears in the field later."""
    return f"setTimeout(() => host.insertAdjacentHTML('beforeend', pillHtml), {delay_ms});"


def pill_instead(delay_ms):
    """The field re-renders later: the input is gone and a pill labelled recThing shows."""
    return f"setTimeout(() => {{ host.innerHTML = '<label>Record Variable</label>' + pillHtml; }}, {delay_ms});"


# The field re-renders every 300 ms into a new input showing recThing: never the same element.
RERENDER_LOOP = """setInterval(() => {
  const next = rv.cloneNode(true);
  next.value = "recThing";
  rv.replaceWith(next);
  rv = next;
}, 300);"""
# The input's value flips between recThing and empty every 300 ms: it never holds.
FLICKER = 'setInterval(() => { rv.value = rv.value ? "" : "recThing"; }, 300);'
# A real pick: the input shows the clicked option's text, read-only, and no pill.
REAL_PICK = 'rv.value = picked.textContent.trim(); rv.readOnly = true;'


def make_step(step_id, text):
    return GuideStep(step_id, 1, "Exercise 9: Sample", "Task 1: Sample.", "1", text,
                     (90.0, 10.0, 400.0, 24.0), None, ())


def serve(page, html):
    """Show HTML at a synthetic org address. Every request is answered locally; no network."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(BUILDER_URL)
    page.wait_for_timeout(200)


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class PickPassPathBrowserTests(unittest.TestCase):
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

    def fill(self, html):
        page = self.open(html)
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        guide_walk._open_guide_dialog(state, "Sample Screen")
        return guide_walk.attempt_step(state, make_step("rv", STEP))

    def assert_clicked(self):
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])

    def assert_review(self, result):
        """A pill near the field: NEEDS_REVIEW, not frozen, no evidence id, the safe note."""
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertFalse(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(result.note, REVIEW_NOTE)
        self.assert_clicked()

    def assert_miss(self, result):
        """No value read and no pill flag: the frozen miss, never PASS."""
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertTrue(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(result.note, MISS_NOTE)
        self.assert_clicked()

    def assert_pass(self, result):
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.note, PASS_NOTE)
        self.assertEqual(result.evidence_ids, ("rv:filled",))
        self.assert_clicked()

    # Path 1: a box that is not a form field and holds a pill shows no value.

    def test_div_combobox_holding_a_bare_pill_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(BARE_PILL)))

    def test_div_combobox_holding_a_bare_pill_without_aria_expanded_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(BARE_PILL + NO_EXPANDED)))

    def test_div_combobox_with_a_pill_in_its_own_shadow_root_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(OWN_SHADOW_PILL)))

    def test_aria_label_div_holding_a_bare_pill_is_a_miss_not_pass(self):
        self.assert_miss(self.fill(div_html(BARE_PILL, labelled=False)))

    def test_aria_label_div_holding_a_bare_pill_without_aria_expanded_is_a_miss_not_pass(self):
        self.assert_miss(self.fill(div_html(BARE_PILL + NO_EXPANDED, labelled=False)))

    def test_aria_label_div_that_becomes_a_pill_itself_is_a_miss_not_pass(self):
        self.assert_miss(self.fill(div_html(BOX_IS_PILL, labelled=False)))

    def test_aria_label_div_with_a_pill_in_its_own_shadow_root_is_a_miss_not_pass(self):
        self.assert_miss(self.fill(div_html(OWN_SHADOW_PILL, labelled=False)))

    def test_aria_label_div_with_a_pill_in_a_part_shadow_root_is_a_miss_not_pass(self):
        self.assert_miss(self.fill(div_html(PART_SHADOW_PILL, labelled=False)))

    # Path 2: the typed text is never PASS before a pill shows.

    def test_typed_text_then_pill_beside_it_50_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_beside(50))))
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])
        self.assertEqual(self.page.input_value("#rv"), "recThing")

    def test_typed_text_then_pill_beside_it_400_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_beside(400))))
        self.assertEqual(self.page.input_value("#rv"), "recThing")

    def test_typed_text_then_field_becomes_a_pill_50_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_instead(50))))

    def test_typed_text_then_field_becomes_a_pill_400_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_instead(400))))

    def test_a_field_that_keeps_re_rendering_is_a_miss_not_pass(self):
        self.assert_miss(self.fill(typed_html(RERENDER_LOOP)))

    def test_a_value_that_does_not_hold_is_a_miss_not_pass(self):
        self.assert_miss(self.fill(typed_html(FLICKER)))

    # The list the typed text opened closed after the click, and the same input kept showing the
    # name for a second with no pill: the click picked it. PASS.

    def test_typed_text_list_closes_nothing_else_changes_passes_after_it_holds(self):
        self.assert_pass(self.fill(typed_html("")))
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])

    def test_a_real_pick_that_shows_the_name_in_the_input_passes(self):
        self.assert_pass(self.fill(typed_html(REAL_PICK)))
        self.assertTrue(self.page.evaluate("document.getElementById('rv').readOnly"))

    def test_walk_goes_on_after_a_delayed_pill_and_run_is_inconclusive(self):
        page = self.open(typed_html(pill_beside(400)))
        ran = []

        def opened(state, item):
            guide_walk._open_guide_dialog(state, "Sample Screen")
            return CheckResult("PASS", "Sample Screen is open.", (f"{item.step_id}:opened",))

        def next_step(state, item):
            ran.append(item.step_id)
            return CheckResult("PASS", "Sample next step read.", (f"{item.step_id}:read",))

        steps = (make_step("s1", "Add a Sample Screen."), make_step("s2", STEP),
                 make_step("s3", "Sample next step."))
        checks = (GuideCheck("opened", lambda item: item.step_id == "s1", opened),
                  GuideCheck("next", lambda item: item.step_id == "s3", next_step))
        outcome = walk_guide(page, SimpleNamespace(steps=steps), checks=checks, timeout_s=3, interval_s=0.2,
                             page_wait_s=2, page_poll_s=0.2)
        self.assertEqual(outcome.statuses["s2"], "NEEDS_REVIEW", outcome.notes.get("s2"))
        self.assertEqual(outcome.notes.get("s2"), REVIEW_NOTE)
        self.assertEqual(tuple(outcome.evidence.get("s2", ())), ())
        self.assertEqual(ran, ["s3"])
        report = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       tuple(outcome.evidence.get(item.step_id, ())), outcome.notes.get(item.step_id, ""))
            for item in steps
        )
        result = SimpleNamespace(signed_in=True, message="", report_steps=report)
        self.assertEqual(outcome_of(result), (INCONCLUSIVE, REVIEW_NOTE))
        self.assertEqual(gate_outcome_of(result), INCONCLUSIVE)


if __name__ == "__main__":
    unittest.main()
