"""W-116: a labelled select or picklist PASSes only on a value read back after the click.

After the option click, the control held before the click is read again: a native select or a
listbox by its one selected option (the clicked one, by identity), any other control by its own
text. PASS needs that read to name the wanted value on at least two polls, on the same connected
element, about a second apart, with no lookup role. A value that does not show, does not hold,
cannot be read, or an element that changed or turned into a lookup is NEEDS_REVIEW, not frozen,
and nothing is typed into the control. A field that becomes a combobox before an option is
clicked goes to the lookup pick path. Real browser, synthetic names only, every request
answered locally.
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

PAGE_URL = "https://example.lightning.force.com/lightning/setup/SampleHome/home"
STEP = "Sample Status: Ready"
PASS_NOTE = "Sample Status is Ready. The control shows Ready after its option was clicked."
REVIEW_NOTE = ("Sample Status option Ready was clicked, but the control did not show Ready steadily "
               "afterwards; please confirm.")
LOOKUP_PASS_NOTE = ("Sample Status is Ready. The field shows it, its list closed and its value changed to "
                    "the picked name.")


def make_step(step_id, text):
    return GuideStep(step_id, 1, "Exercise 9: Sample", "Task 1: Sample.", "1", text,
                     (90.0, 10.0, 400.0, 24.0), None, ())


def serve(page, html):
    """Show HTML at a synthetic org address. Every request is answered locally; no network."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(PAGE_URL)
    page.wait_for_timeout(200)


def native_html(after_js=""):
    """A native select shown as a list (size 6). Its change handler records the value and runs
    AFTER_JS with st, the select."""
    return f"""
<label for="st">Sample Status</label>
<select id="st" size="6" style="height:160px; width:160px">
  <option>Draft</option><option>Ready</option><option>Closed</option>
</select>
<script>
  window.chosen = [];
  let st = document.getElementById("st");
  st.addEventListener("change", () => {{
    window.chosen.push(st.value);
    {after_js}
  }});
</script>
"""


def listbox_html(after_js=""):
    """A listbox (role listbox, not a native select) with Draft selected. It is tall, so a click on
    its middle hits no option. An option click marks only that option selected, records it and
    runs AFTER_JS with st (the listbox), picked and choose(id), which marks only ID selected."""
    return f"""
<span id="lab">Sample Status</span>
<div id="st" role="listbox" aria-labelledby="lab" tabindex="0"
     style="display:block; height:160px; width:160px; border:1px solid #888">
  <div role="option" id="o1" aria-selected="true">Draft</div>
  <div role="option" id="o2" aria-selected="false">Ready</div>
  <div role="option" id="o3" aria-selected="false">Closed</div>
</div>
<script>
  window.chosen = [];
  let st = document.getElementById("st");
  const choose = (id) => {{
    for (const o of st.querySelectorAll("[role=option]")) o.setAttribute("aria-selected", String(o.id === id));
  }};
  st.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    choose(picked.id);
    window.chosen.push(picked.id);
    {after_js}
  }});
</script>
"""


def button_html(after_js=""):
    """A picklist button showing --None--. Its click opens the list it owns; an option click
    records the option, closes the list and runs AFTER_JS with st (the button) and picked."""
    return f"""
<span id="lab">Sample Status</span>
<div id="home">
<button id="st" aria-labelledby="lab" aria-haspopup="listbox" aria-expanded="false"
        aria-controls="lst">--None--</button>
</div>
<div id="lst" role="listbox" hidden>
  <div role="option" id="o1">Draft</div>
  <div role="option" id="o2">Ready</div>
  <div role="option" id="o3">Closed</div>
</div>
<script>
  window.chosen = [];
  let st = document.getElementById("st");
  const lst = document.getElementById("lst");
  document.getElementById("home").addEventListener("click", (event) => {{
    if (event.target.closest("#st")) {{ lst.hidden = false; st.setAttribute("aria-expanded", "true"); }}
  }});
  lst.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    lst.hidden = true;
    st.setAttribute("aria-expanded", "false");
    {after_js}
  }});
</script>
"""


# A plain text field (no lookup role or marks) in a dialog that shows a Ready option.
RACE_HTML = """
<div role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Dialog</h2>
  <label for="st">Sample Status</label>
  <input id="st" autocomplete="off">
  <div id="lst" role="listbox" aria-label="Sample Choices"><div role="option" id="o2">Ready</div></div>
</div>
<script>
  window.chosen = [];
  document.getElementById("o2").addEventListener("click", () => {
    window.chosen.push("o2");
    document.getElementById("st").value = "Ready";
    document.getElementById("lst").hidden = true;
  });
</script>
"""

SHOWS = 'st.textContent = picked.textContent.trim();'


def later(js, ms):
    return f"setTimeout(() => {{ {js} }}, {ms});"


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class SelectReadBackBrowserTests(unittest.TestCase):
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

    def state(self, page, poll):
        if poll == 2.0:
            return guide_walk.WalkState(page=page, timeout_s=12, interval_s=2.0, page_wait_s=6, page_poll_s=2.0)
        return guide_walk.WalkState(page=page, timeout_s=3, interval_s=poll, page_wait_s=2, page_poll_s=poll)

    def fill(self, html, poll=0.2):
        """Fill at a 0.2 s poll (2 s wait), or at the production 2 s poll (6 s wait)."""
        page = self.open(html)
        return guide_walk.attempt_step(self.state(page, poll), make_step("st", STEP))

    def assert_review(self, result, chosen):
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertFalse(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(result.note, REVIEW_NOTE)
        self.assertEqual(self.page.evaluate("window.chosen"), chosen)

    def assert_pass(self, result, chosen):
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.note, PASS_NOTE)
        self.assertEqual(result.evidence_ids, ("st:filled",))
        self.assertEqual(self.page.evaluate("window.chosen"), chosen)

    # A native select: the shared option reader cannot read a form control's option text (a
    # known limit of _option_lines), so no option is matched or clicked and nothing is typed.
    # Never PASS. This guard holds on the base too; the listbox tests below carry the regression.

    def test_native_select_is_never_picked_typed_into_or_passed(self):
        result = self.fill(native_html('st.selectedIndex = 0;'))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(self.page.evaluate("st.selectedIndex"), -1)

    # A listbox that shows its pick as the one selected option.

    def test_listbox_reset_by_its_click_handler_is_review(self):
        result = self.fill(listbox_html('choose("o1");'))
        self.assert_review(result, ["o2"])

    def test_listbox_reset_600_ms_after_the_click_is_review(self):
        result = self.fill(listbox_html(later('choose("o1");', 600)))
        self.assert_review(result, ["o2"])

    def test_listbox_that_selects_another_option_is_review(self):
        result = self.fill(listbox_html('choose("o3");'))
        self.assert_review(result, ["o2"])

    def test_listbox_left_with_nothing_selected_is_review(self):
        result = self.fill(listbox_html(later('choose("none");', 100)))
        self.assert_review(result, ["o2"])

    def test_listbox_with_two_options_selected_is_review(self):
        result = self.fill(listbox_html('document.getElementById("o1").setAttribute("aria-selected", "true");'))
        self.assert_review(result, ["o2"])

    def test_listbox_that_keeps_the_pick_passes(self):
        result = self.fill(listbox_html())
        self.assert_pass(result, ["o2"])

    def test_listbox_that_keeps_the_pick_passes_at_the_2_s_poll(self):
        result = self.fill(listbox_html(), poll=2.0)
        self.assert_pass(result, ["o2"])

    # A picklist button that shows its value as its own text.

    def test_picklist_button_whose_shown_value_does_not_change_is_review(self):
        result = self.fill(button_html())
        self.assert_review(result, ["o2"])
        self.assertEqual(self.page.evaluate("st.textContent"), "--None--")

    def test_picklist_button_that_shows_another_option_is_review(self):
        result = self.fill(button_html('st.textContent = "Closed";'))
        self.assert_review(result, ["o2"])

    def test_picklist_button_that_shows_another_option_at_the_2_s_poll_is_review(self):
        result = self.fill(button_html('st.textContent = "Closed";'), poll=2.0)
        self.assert_review(result, ["o2"])

    def test_picklist_button_that_shows_the_pick_then_reverts_is_review(self):
        result = self.fill(button_html(SHOWS + later('st.textContent = "--None--";', 600)))
        self.assert_review(result, ["o2"])

    def test_picklist_button_replaced_after_the_click_is_review(self):
        js = SHOWS + later('const next = st.cloneNode(true); st.replaceWith(next); st = next;', 100)
        result = self.fill(button_html(js))
        self.assert_review(result, ["o2"])

    def test_picklist_button_that_becomes_a_combobox_after_the_click_is_review(self):
        result = self.fill(button_html(SHOWS + later('st.setAttribute("role", "combobox");', 100)))
        self.assert_review(result, ["o2"])

    def test_picklist_button_that_shows_the_pick_passes(self):
        result = self.fill(button_html(SHOWS))
        self.assert_pass(result, ["o2"])

    def test_picklist_button_that_shows_the_pick_passes_at_the_2_s_poll(self):
        result = self.fill(button_html(SHOWS), poll=2.0)
        self.assert_pass(result, ["o2"])

    def test_picklist_button_that_shows_the_pick_400_ms_later_passes(self):
        result = self.fill(button_html(later(SHOWS, 400)))
        self.assert_pass(result, ["o2"])

    # The role race: the field turns into a combobox after _is_lookup read it.

    def test_field_that_becomes_a_combobox_before_the_pick_goes_to_the_lookup_path(self):
        page = self.open(RACE_HTML)
        real = guide_walk._is_lookup
        calls = []

        def racing(page_, label):
            out = real(page_, label)
            if not calls:
                page_.evaluate('document.getElementById("st").setAttribute("role", "combobox")')
            calls.append(out)
            return out

        guide_walk._is_lookup = racing
        self.addCleanup(setattr, guide_walk, "_is_lookup", real)
        result = guide_walk.attempt_step(self.state(page, 0.2), make_step("st", STEP))
        self.assertEqual(calls[0], False)
        # The lookup path's own PASS, on its own signal (the value changed to the picked name).
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.note, LOOKUP_PASS_NOTE)
        self.assertEqual(page.evaluate("window.chosen"), ["o2"])

    # The gate: a review keeps the walk going and the run inconclusive, never a success.

    def test_a_review_select_goes_through_the_gate_as_inconclusive(self):
        page = self.open(button_html('st.textContent = "Closed";'))
        ran = []

        def next_step(state, item):
            ran.append(item.step_id)
            return CheckResult("PASS", "Sample next step read.", (f"{item.step_id}:read",))

        steps = (make_step("st", STEP), make_step("s2", "Sample next step."))
        checks = (GuideCheck("next", lambda item: item.step_id == "s2", next_step),)
        outcome = walk_guide(page, SimpleNamespace(steps=steps), checks=checks, timeout_s=3, interval_s=0.2,
                             page_wait_s=2, page_poll_s=0.2)
        self.assertEqual(outcome.statuses["st"], "NEEDS_REVIEW", outcome.notes.get("st"))
        self.assertEqual(outcome.notes.get("st"), REVIEW_NOTE)
        self.assertEqual(tuple(outcome.evidence.get("st", ())), ())
        self.assertEqual(ran, ["s2"])
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
