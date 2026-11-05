"""V43: a lookup pill inside a shadow root or with bidi marks is read and gives NEEDS_REVIEW.

The before-click pill set is read with deep() (across shadow roots), but pillsIn() used only
querySelectorAll, so it never saw a pill inside an open shadow root. After a fix, it must also
use deep(). Bidi control marks (U+200E, U+200F, U+2066-U+2069) are invisible by definition and
serve no function in a Latin title or label; they are trimmed at the start and end only (fail
closed for any other difference). Any other invisible character (U+200B, soft hyphen) must not
be silently trimmed; the label then fails to match.
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

CSS = """
<style>
  .slds-assistive-text { position: absolute !important; margin: -1px !important; border: 0 !important;
    padding: 0 !important; width: 1px !important; height: 1px !important; overflow: hidden !important;
    clip: rect(0 0 0 0) !important; white-space: nowrap !important; }
</style>
"""


def pill(label="recThing", title=None, label_html=None):
    """SLDS-style pill markup: record icon, the label link, a remove button."""
    title = label if title is None else title
    label_html = label if label_html is None else label_html
    titled = "" if title is False else f' title="{title}"'
    return (f'<span class="slds-pill slds-pill_link"><span class="slds-pill__icon_container">'
            '<span class="slds-assistive-text">Record</span><svg aria-hidden="true" width="12" height="12">'
            f'</svg></span><a class="slds-pill__action"{titled}><span class="slds-pill__label">'
            f'{label_html}</span></a><button class="slds-button slds-pill__remove" title="Remove">'
            '<svg aria-hidden="true" width="10" height="10"></svg><span class="slds-assistive-text">Remove'
            '</span></button></span>')


def shadow_html(pill_markup="", shadow_mode="open"):
    """The Record Variable lookup; the option click re-renders the host with the pill in a shadow
    root. A wrapper div inside the control gets a shadow root; the pill appears inside it after a
    delay. SHADOW_MODE can be "open" or "closed"."""
    return f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <div class="slds-form-element">
        <label class="slds-form-element__label" for="rv">Record Variable</label>
        <div class="slds-form-element__control">
          <div class="slds-combobox_container">
            <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="recThing"
                   autocomplete="off">
          </div>
          <div id="lb" role="listbox">
            <div role="option" id="opt1"><strong>recThing</strong></div>
          </div>
        </div>
      </div>
    </sample-field>
  </section>
</div>
<script>
  window.chosen = [];
  const host = document.getElementById("host-rv");
  const list = document.getElementById("lb");
  const input = document.getElementById("rv");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    input.setAttribute("aria-expanded", "false");
    input.value = "";
    list.hidden = true;
    setTimeout(() => {{
      const wrap = document.createElement("div");
      wrap.id = "shadow-wrap";
      wrap.attachShadow({{mode: {shadow_mode!r}}});
      wrap.shadowRoot.innerHTML = {pill_markup!r};
      host.innerHTML = '<div class="slds-form-element"><label class="slds-form-element__label">' +
        'Record Variable</label><div class="slds-form-element__control"></div></div>';
      host.querySelector(".slds-form-element__control").appendChild(wrap);
    }}, 50);
  }});
  input.focus();
</script>
"""


def bidi_html(label_text="recThing", title_text=None):
    """The Record Variable lookup; the option click renders a pill whose label and title may have
    bidi marks. The pill appears after a short delay (the delayed-appearance case)."""
    title_text = label_text if title_text is None else title_text
    return f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <label for="rv">Record Variable</label>
      <div class="slds-combobox_container">
        <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="recThing"
               autocomplete="off">
      </div>
      <div id="lb" role="listbox">
        <div role="option" id="opt1"><strong>recThing</strong></div>
      </div>
    </sample-field>
  </section>
</div>
<script>
  window.chosen = [];
  const host = document.getElementById("host-rv");
  const list = document.getElementById("lb");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    setTimeout(() => {{
      host.innerHTML = '<label>Record Variable</label><div class="slds-form-element__control">' +
        {pill(label_html=label_text, title=title_text)!r} + '</div>';
    }}, 50);
  }});
  document.getElementById("rv").focus();
</script>
"""


def shadow_bidi_html():
    """Pill inside a shadow root AND with bidi marks."""
    label = "‎recThing‎"
    return shadow_html(pill_markup=pill(label_html=label, title=label), shadow_mode="open")


REVIEW_NOTE = ("Record Variable option recThing was clicked and a selected pill labelled recThing "
               "appears near the field, but the validator cannot prove the pill belongs to this field; "
               "please confirm.")


def make_step(step_id, text):
    return GuideStep(step_id, 1, "Exercise 9: Sample", "Task 1: Sample.", "1", text,
                     (90.0, 10.0, 400.0, 24.0), None, ())


def serve(page, html, url=BUILDER_URL):
    """Show HTML at a synthetic org address. Every request is answered locally; no network."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url)
    page.wait_for_timeout(200)


def opened_screen(state, item):
    """The guide step that opened the Sample Screen dialog: it records the dialog by name."""
    guide_walk._open_guide_dialog(state, "Sample Screen")
    return CheckResult("PASS", "Sample Screen is open.", (f"{item.step_id}:opened",))


def report_steps(outcome, steps):
    return tuple(
        ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id], tuple(
            outcome.evidence.get(item.step_id, ())), outcome.notes.get(item.step_id, ""))
        for item in steps
    )


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class ShadowRootPillBrowserTests(unittest.TestCase):
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

    def assert_review(self, result):
        """A pill near the field: NEEDS_REVIEW, not frozen, no evidence id, the safe note."""
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertFalse(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(result.note, REVIEW_NOTE)
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])

    def test_pill_inside_open_shadow_root_is_review_not_pass(self):
        result = self.fill(shadow_html(pill_markup=pill()))
        self.assert_review(result)
        shadow_wrap = self.page.query_selector("#shadow-wrap")
        self.assertIsNotNone(shadow_wrap)
        pill_label = shadow_wrap.evaluate("(el) => el.shadowRoot.querySelector('.slds-pill__label').textContent")
        self.assertEqual(pill_label, "recThing")

    def test_pill_with_bidi_marks_is_review_not_pass(self):
        label = "‎recThing‎"
        result = self.fill(bidi_html(label_text=label, title_text=label))
        self.assert_review(result)
        self.assertEqual(self.page.locator("#host-rv .slds-pill__label").inner_text(), label)

    def test_pill_inside_shadow_root_with_bidi_marks_is_review_not_pass(self):
        result = self.fill(shadow_bidi_html())
        self.assert_review(result)

    def test_pill_with_mismatched_title_after_bidi_trim_is_not_pass(self):
        # The title does not match even after trimming bidi marks: not PASS.
        label = "‎recThing‎"
        result = self.fill(bidi_html(label_text=label, title_text="recOther"))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_pill_with_zero_width_space_does_not_match(self):
        # U+200B (zero-width space) is not a bidi mark; the label must not match.
        label = "​recThing​"
        result = self.fill(bidi_html(label_text=label, title_text=label))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_pill_with_soft_hyphen_does_not_match(self):
        # U+00AD (soft hyphen) is not a bidi mark; the label must not match.
        label = "­recThing­"
        result = self.fill(bidi_html(label_text=label, title_text=label))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_pill_with_different_label_inside_shadow_root_is_not_pass(self):
        result = self.fill(shadow_html(pill_markup=pill("recOther")))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_two_pills_inside_shadow_root_are_not_pass(self):
        result = self.fill(shadow_html(pill_markup=pill() + pill()))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_lightning_pill_host_with_nested_slds_pill_gives_review_not_mismatch(self):
        # M1: A lightning-pill host with .slds-pill in its open shadow root must count as 1 pill (NEEDS_REVIEW),
        # not 2 (UI_MISMATCH). The outermost-only filter must cross shadow boundaries.
        nested_html = f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <label for="rv">Record Variable</label>
      <div class="slds-combobox_container">
        <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="recThing" autocomplete="off">
      </div>
      <div id="lb" role="listbox">
        <div role="option" id="opt1"><strong>recThing</strong></div>
      </div>
    </sample-field>
  </section>
</div>
<script>
  window.chosen = [];
  const host = document.getElementById("host-rv");
  const list = document.getElementById("lb");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    setTimeout(() => {{
      const lightningPill = document.createElement("lightning-pill");
      lightningPill.attachShadow({{mode: "open"}});
      lightningPill.shadowRoot.innerHTML = {pill()!r};
      host.innerHTML = '<label>Record Variable</label><div class="slds-form-element__control"></div>';
      host.querySelector(".slds-form-element__control").appendChild(lightningPill);
    }}, 50);
  }});
  document.getElementById("rv").focus();
</script>
"""
        result = self.fill(nested_html)
        self.assert_review(result)

    def test_pill_already_shown_before_click_is_not_evidence(self):
        # A pill already in a shadow root before the click must not count as evidence of this pick.
        # Fail-closed check only: the pill sits outside the innermost live container, so this stays
        # UI_MISMATCH even with the identity check and the walk stop removed. The guards are proven
        # by test_pill_moved_into_field_after_click_is_not_evidence (M3) and
        # test_ancestor_with_pill_before_click_is_never_a_field_container (M14).
        preexisting_html = f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <div class="slds-form-element">
        <label class="slds-form-element__label" for="rv">Record Variable</label>
        <div class="slds-form-element__control">
          <div class="slds-combobox_container">
            <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="recThing" autocomplete="off">
          </div>
          <div id="lb" role="listbox">
            <div role="option" id="opt1"><strong>recThing</strong></div>
          </div>
          <div id="shadow-wrap"></div>
        </div>
      </div>
    </sample-field>
  </section>
</div>
<script>
  window.chosen = [];
  const host = document.getElementById("host-rv");
  const wrap = document.getElementById("shadow-wrap");
  wrap.attachShadow({{mode: "open"}});
  wrap.shadowRoot.innerHTML = {pill()!r};
  const list = document.getElementById("lb");
  const input = document.getElementById("rv");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    // Click collapses the list but the pre-existing pill stays (no new pill added).
    input.setAttribute("aria-expanded", "false");
    input.value = "";
    list.hidden = true;
  }});
  input.focus();
</script>
"""
        result = self.fill(preexisting_html)
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertNotEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_all_bidi_marks_trimmed_at_ends(self):
        # M5, M6: All bidi control marks (U+200E, U+200F, U+2066-U+2069) must be trimmed at start and end.
        marks = [("‎", "U+200E"), ("‏", "U+200F"), ("⁦", "U+2066"),
                 ("⁧", "U+2067"), ("⁨", "U+2068"), ("⁩", "U+2069")]
        for mark, name in marks:
            with self.subTest(mark=name):
                label = f"{mark}recThing{mark}"
                result = self.fill(bidi_html(label_text=label, title_text=label))
                self.assert_review(result)

    def test_bidi_mark_in_middle_does_not_match(self):
        # M9: Bidi marks are trimmed only at the start and end; a mark in the middle must not match.
        label = "rec‎Thing"
        result = self.fill(bidi_html(label_text=label, title_text=label))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_pill_in_ancestor_stops_container_walk(self):
        # A pill inside a shadow root in an ancestor, and a NEW pill added to an inner wrapper after
        # the click. Fail-closed check only: the innermost live container is the combobox wrapper,
        # which never holds the new pill, so this stays UI_MISMATCH with or without the walk stop.
        # The stop itself is proven by test_ancestor_with_pill_before_click_is_never_a_field_container.
        ancestor_pill_html = f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <div id="outer-wrapper">
      <label for="rv">Record Variable</label>
      <div id="inner-wrapper">
        <div class="slds-combobox_container">
          <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="recThing" autocomplete="off">
        </div>
        <div id="lb" role="listbox">
          <div role="option" id="opt1"><strong>recThing</strong></div>
        </div>
      </div>
    </div>
  </section>
</div>
<script>
  window.chosen = [];
  const outer = document.getElementById("outer-wrapper");
  const inner = document.getElementById("inner-wrapper");
  const prePillWrap = document.createElement("div");
  prePillWrap.id = "pre-pill-wrap";
  prePillWrap.attachShadow({{mode: "open"}});
  prePillWrap.shadowRoot.innerHTML = {pill("otherField")!r};
  outer.appendChild(prePillWrap);
  const list = document.getElementById("lb");
  const input = document.getElementById("rv");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    input.setAttribute("aria-expanded", "false");
    input.value = "";
    list.hidden = true;
    setTimeout(() => {{
      const newPillWrap = document.createElement("div");
      newPillWrap.id = "new-pill-wrap";
      newPillWrap.attachShadow({{mode: "open"}});
      newPillWrap.shadowRoot.innerHTML = {pill()!r};
      inner.appendChild(newPillWrap);
    }}, 50);
  }});
  input.focus();
</script>
"""
        result = self.fill(ancestor_pill_html)
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_pill_moved_into_field_after_click_is_not_evidence(self):
        # M3 only (W-108): the pill is on the page before the click, in another section, so the
        # container walk never meets it (the section edge ends the walk first). After the click
        # the page re-renders the field and MOVES that same pill node into it. The pill now sits
        # in the field's recorded container with the wanted label, so only the identity check
        # tells it was not shown by this pick.
        moved_html = f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <label for="rv">Record Variable</label>
      <div class="slds-combobox_container">
        <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="recThing" autocomplete="off">
      </div>
      <div id="lb" role="listbox">
        <div role="option" id="opt1"><strong>recThing</strong></div>
      </div>
    </sample-field>
  </section>
  <section aria-label="Parking">
    <div id="parking">{pill()}</div>
  </section>
</div>
<script>
  window.chosen = [];
  const host = document.getElementById("host-rv");
  const list = document.getElementById("lb");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    window.oldPill = document.querySelector("#parking .slds-pill");
    setTimeout(() => {{
      host.innerHTML = '<label>Record Variable</label><div class="slds-form-element__control"></div>';
      host.querySelector(".slds-form-element__control").appendChild(window.oldPill);
    }}, 50);
  }});
  document.getElementById("rv").focus();
</script>
"""
        result = self.fill(moved_html)
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertEqual(result.note, "Record Variable did not show recThing after its option was clicked.")
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])
        # The same pill node now sits inside the field, with the wanted label.
        self.assertTrue(self.page.evaluate(
            "document.querySelector('#host-rv .slds-pill') === window.oldPill"))
        self.assertEqual(self.page.locator("#host-rv .slds-pill__label").inner_text(), "recThing")

    def test_ancestor_with_pill_before_click_is_never_a_field_container(self):
        # M14 only: an ancestor of the field already holds a visible pill (in an open shadow root)
        # before the click, so the container walk must stop below it. After the click the page
        # hides that old pill, removes the field's own elements and renders a NEW recThing pill
        # with the field's label inside the ancestor. Had the ancestor been recorded, it would be
        # the only live container, holding one visible new pill: a false NEEDS_REVIEW. The pill
        # is new, so the identity check does not reject it; only the walk stop does.
        ancestor_html = f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <div id="outer">
      <div id="old-wrap"></div>
      <sample-field id="host-rv">
        <label for="rv">Record Variable</label>
        <div class="slds-combobox_container">
          <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="recThing" autocomplete="off">
        </div>
        <div id="lb" role="listbox">
          <div role="option" id="opt1"><strong>recThing</strong></div>
        </div>
      </sample-field>
    </div>
  </section>
</div>
<script>
  window.chosen = [];
  const outer = document.getElementById("outer");
  const oldWrap = document.getElementById("old-wrap");
  oldWrap.attachShadow({{mode: "open"}});
  oldWrap.shadowRoot.innerHTML = {pill("recOld")!r};
  const list = document.getElementById("lb");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    setTimeout(() => {{
      oldWrap.style.display = "none";
      document.getElementById("host-rv").remove();
      const field = document.createElement("div");
      field.id = "new-field";
      field.innerHTML = '<label>Record Variable</label>' + {pill()!r};
      outer.appendChild(field);
    }}, 50);
  }});
  document.getElementById("rv").focus();
</script>
"""
        page = self.open(ancestor_html)
        self.assertTrue(page.locator("#old-wrap .slds-pill").is_visible())
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        guide_walk._open_guide_dialog(state, "Sample Screen")
        result = guide_walk.attempt_step(state, make_step("rv", STEP))
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertEqual(result.note, "Record Variable did not show recThing after its option was clicked.")
        self.assertEqual(page.evaluate("window.chosen"), ["opt1"])
        self.assertFalse(page.locator("#old-wrap .slds-pill").is_visible())
        self.assertEqual(page.locator("#new-field .slds-pill__label").inner_text(), "recThing")

    def test_pill_inside_closed_shadow_root_is_not_readable(self):
        # A closed shadow root is not readable, so the pill is not found: UI_MISMATCH.
        result = self.fill(shadow_html(pill_markup=pill(), shadow_mode="closed"))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertIn("Record Variable did not show recThing", result.note)

    def test_walk_goes_on_after_shadow_pill_and_run_is_inconclusive(self):
        page = self.open(shadow_html(pill_markup=pill()))
        ran = []

        def next_step(state, item):
            ran.append(item.step_id)
            return CheckResult("PASS", "Sample next step read.", (f"{item.step_id}:read",))

        steps = (make_step("s1", "Add a Sample Screen."), make_step("s2", STEP),
                 make_step("s3", "Sample next step."))
        checks = (GuideCheck("opened", lambda item: item.step_id == "s1", opened_screen),
                  GuideCheck("next", lambda item: item.step_id == "s3", next_step))
        outcome = walk_guide(page, SimpleNamespace(steps=steps), checks=checks, timeout_s=3, interval_s=0.2,
                             page_wait_s=1.5, page_poll_s=0.2)
        self.assertEqual(outcome.statuses["s2"], "NEEDS_REVIEW", outcome.notes.get("s2"))
        self.assertEqual(outcome.notes.get("s2"), REVIEW_NOTE)
        self.assertEqual(tuple(outcome.evidence.get("s2", ())), ())
        self.assertEqual(ran, ["s3"])
        self.assertEqual(outcome.statuses["s3"], "PASS")
        result = SimpleNamespace(signed_in=True, message="", report_steps=report_steps(outcome, steps))
        self.assertEqual(outcome_of(result), (INCONCLUSIVE, REVIEW_NOTE))
        self.assertEqual(gate_outcome_of(result), INCONCLUSIVE)


if __name__ == "__main__":
    unittest.main()
