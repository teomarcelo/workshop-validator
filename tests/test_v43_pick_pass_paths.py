"""V43 r6 (W-112): a lookup pick PASSes only on a post-click signal that names the pick.

The walk types the wanted name before the click, so the box showing it is not evidence. PASS
needs, on one poll: no new pill labelled the name near the field or anywhere on the page, no pill
on the box or its ancestors; a closed read of the same connected box showing the name that held
for about a second; and a signal read on that box that changed with the click: its value went
from other text to the name, it became read-only, or the clicked option became selected or the
box's active descendant. A pill gives NEEDS_REVIEW (W-110); typed text alone, or a field that
keeps changing, gives NEEDS_REVIEW, not frozen. The non-combobox text fill follows the same rule.
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
PASS_START = "Record Variable is recThing. The field shows it, its list closed and "
PASS_LOCKED = PASS_START + "it became read-only."
PASS_CHANGED = PASS_START + "its value changed to the picked name."
PASS_SELECTED = PASS_START + "the clicked option reads as selected."
PASS_ACTIVE = PASS_START + "the box points to the clicked option."
REVIEW_NOTE = ("Record Variable option recThing was clicked and a selected pill labelled recThing "
               "appears near the field, but the validator cannot prove the pill belongs to this field; "
               "please confirm.")
PAGE_REVIEW_NOTE = ("Record Variable option recThing was clicked and a new selected pill labelled recThing "
                    "appears on the page, but the validator cannot prove the pill belongs to this field; "
                    "please confirm.")
UNCONFIRMED_NOTE = ("Record Variable option recThing was clicked. The field shows the typed text; nothing "
                    "on the page confirms the pick, so please confirm.")
UNSTEADY_NOTE = ("Record Variable option recThing was clicked, but the field kept changing and never held "
                 "recThing long enough to read; please confirm.")
UNREAD_NOTE = ("Record Variable option recThing was clicked and the field shows recThing, but a new pill on "
               "the page could not be read, so the validator cannot tell whether it belongs to this field; "
               "please confirm.")
TYPED_PILL_NOTE = ("recThing was typed into Record Variable and a new selected pill labelled recThing appears "
                   "on the page, so the field may be a lookup the validator cannot pick in; please confirm.")
MARKED_NOTE = ("The Record Variable field behaves as a lookup but is not a combobox or searchbox, so no "
               "option can be picked and nothing was typed; please confirm.")
PILL = ('<span class="slds-pill"><span class="slds-pill__label">recThing</span>'
        '<button class="slds-pill__remove" title="Remove">x</button></span>')
# The pill a div combobox renders inside itself: its label only, so the box's own text is recThing.
BARE = '<span class="slds-pill"><span class="slds-pill__label">recThing</span></span>'


def div_html(after_js, labelled=True, wrap_before=False):
    """A div with role combobox (not a form field). A click on it opens its list; the option click
    closes the list (aria-expanded "false") and runs AFTER_JS with rv, the box. LABELLED: named by
    aria-labelledby to a field label, else by aria-label only (no field container). WRAP_BEFORE:
    the box sits inside a pill element from the start (on the page before the click)."""
    name = ('aria-labelledby="lab"' if labelled else 'aria-label="Record Variable"')
    label = ('<span class="slds-form-element__label" id="lab">Record Variable</span>' if labelled else "")
    box = (f'<div id="rv" role="combobox" tabindex="0" {name} aria-expanded="false" aria-controls="lb" '
           'style="display:block; min-width:120px; min-height:20px; border:1px solid #888"></div>')
    if wrap_before:
        box = f'<span class="slds-pill" id="wrap">{box}</span>'
    return f"""
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      {label}
      {box}
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
# A selected option with assistive text rendered inside the div box (Lightning's selection list).
ASSISTIVE_PILL = ("rv.innerHTML = '<div class=\"slds-listbox_selection\"><span role=\"option\" "
                  "aria-selected=\"true\">recThing<span class=\"slds-assistive-text\">Press delete to "
                  "remove</span></span></div>'; rv.setAttribute('aria-readonly', 'true');")


def typed_html(after_js, name='<label for="rv">Record Variable</label>', box_attrs='role="combobox"',
               list_shown=False):
    """An input lookup. The list opens when text is typed; the option click closes the list
    (aria-expanded "false") and runs AFTER_JS with rv (the input), host, picked and the pill
    markup. LIST_SHOWN: the list is open before anything is typed, so the walk clicks at once."""
    hidden = "" if list_shown else " hidden"
    expanded = "true" if list_shown else "false"
    return f"""
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      {name}
      <input id="rv" {box_attrs} aria-expanded="{expanded}" aria-controls="lb" value="" autocomplete="off">
      <div id="lb" role="listbox"{hidden}>
        <div role="option" id="opt1">recThing</div>
      </div>
    </sample-field>
  </section>
</div>
<div id="elsewhere"></div>
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


def text_html(attrs="", on_input=""):
    """A plain text field labelled Record Variable: no combobox or searchbox role, no list.
    ON_INPUT runs on each input event with rv and the pill markup."""
    return f"""
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <label for="rv">Record Variable</label>
      <input id="rv" type="text" value="" {attrs}>
    </sample-field>
  </section>
</div>
<div id="elsewhere"></div>
<script>
  window.typed = [];
  const host = document.getElementById("host-rv");
  const rv = document.getElementById("rv");
  const pillHtml = {PILL!r};
  rv.addEventListener("input", () => {{ window.typed.push(rv.value); {on_input} }});
</script>
"""


def pill_beside(delay_ms):
    """The typed text stays in the input; a pill labelled recThing appears in the field later."""
    return f"setTimeout(() => host.insertAdjacentHTML('beforeend', pillHtml), {delay_ms});"


def pill_elsewhere(delay_ms):
    """The typed text stays in the input; a pill labelled recThing appears outside the field."""
    return (f"setTimeout(() => document.getElementById('elsewhere').insertAdjacentHTML('beforeend', pillHtml), "
            f"{delay_ms});")


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
# A pill beside the input shows and hides every 700 ms; the input keeps the typed text.
PILL_FLICKER = """host.insertAdjacentHTML('beforeend', '<span id="flick">' + pillHtml + '</span>');
setInterval(() => { const f = document.getElementById('flick'); f.hidden = !f.hidden; }, 700);"""
# Real picks, one per W-112 signal.
LOCKS = 'rv.value = picked.textContent.trim(); rv.readOnly = true;'
SELECTS = 'picked.setAttribute("aria-selected", "true");'
POINTS = 'rv.setAttribute("aria-activedescendant", picked.id);'
SHOWS = 'rv.value = picked.textContent.trim();'


def make_step(step_id, text):
    return GuideStep(step_id, 1, "Exercise 9: Sample", "Task 1: Sample.", "1", text,
                     (90.0, 10.0, 400.0, 24.0), None, ())


def serve(page, html):
    """Show HTML at a synthetic org address. Every request is answered locally; no network."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(BUILDER_URL)
    page.wait_for_timeout(200)


class _SpyBox:
    """The lookup box locator, with a hook on its element_handle calls (all else passes through)."""

    def __init__(self, box, on_handle):
        self._box = box
        self._on_handle = on_handle
        self._handles = 0

    def element_handle(self, *args, **kwargs):
        self._handles += 1
        self._on_handle("before", self._handles)
        handle = self._box.element_handle(*args, **kwargs)
        self._on_handle("after", self._handles)
        return handle

    def __getattr__(self, name):
        return getattr(self._box, name)


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

    def fill_scripted(self, html, on_poll=None, on_handle=None, on_hold=None, page_wait_s=2.0):
        """Fill with a stepped clock: each sleep moves it by the interval (0.25 s, exact). After
        each closed read of the pick, ON_POLL(n) runs with the read's number (1-based) before the
        next poll; ON_HANDLE(n, when, k) runs around the k-th box handle of read n. ON_HOLD runs
        once, in the sleep that brings the clock to the first closed read's time plus the hold
        (_PICK_STEADY_S): just before the poll on which the hold completes."""
        page = self.open(html)
        now = [100.0]
        reads = [0]
        due = []
        first_read = []

        def sleeper(seconds):
            now[0] += seconds
            while due:
                due.pop(0)()
            if on_hold is not None and first_read and now[0] >= first_read[0] + guide_walk._PICK_STEADY_S:
                on_hold()
                first_read.append("done")
                first_read[0] = float("inf")
            page.wait_for_timeout(20)

        state = guide_walk.WalkState(page=page, timeout_s=5, interval_s=0.25, page_wait_s=page_wait_s,
                                     page_poll_s=0.25, clock=lambda: now[0], sleeper=sleeper)
        guide_walk._open_guide_dialog(state, "Sample Screen")
        real = guide_walk._closed_read

        def closed_read(root, box, want, dialog_name="", first=None, option=None):
            reads[0] += 1
            n = reads[0]
            if on_handle is not None and box is not None:
                box = _SpyBox(box, lambda when, k: on_handle(n, when, k))
            out = real(root, box, want, dialog_name, first, option)
            if out[0] is not None and not first_read:
                first_read.append(now[0])
            if on_poll is not None:
                due.append(lambda: on_poll(n))
            return out

        guide_walk._closed_read = closed_read
        self.addCleanup(setattr, guide_walk, "_closed_read", real)
        return guide_walk.attempt_step(state, make_step("rv", STEP))

    def assert_clicked(self):
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])

    def assert_review(self, result, note=REVIEW_NOTE):
        """NEEDS_REVIEW, not frozen, no evidence id, the given safe note."""
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertFalse(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(result.note, note)
        self.assert_clicked()

    def assert_miss(self, result):
        """No value read and no pill flag: the frozen miss, never PASS."""
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertTrue(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(result.note, MISS_NOTE)
        self.assert_clicked()

    def assert_pass(self, result, note):
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.note, note)
        self.assertEqual(result.evidence_ids, ("rv:filled",))
        self.assert_clicked()

    # Path 1: a box that is not a form field and holds a pill shows no value.

    def test_div_combobox_holding_a_bare_pill_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(BARE_PILL)))

    def test_div_combobox_holding_a_bare_pill_without_aria_expanded_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(BARE_PILL + NO_EXPANDED)))

    def test_div_combobox_with_a_pill_in_its_own_shadow_root_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(OWN_SHADOW_PILL)))

    # An aria-label box has no field container; the page-wide search sees the new pill (W-112).

    def test_aria_label_div_holding_a_bare_pill_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(BARE_PILL, labelled=False)), PAGE_REVIEW_NOTE)

    def test_aria_label_div_holding_a_bare_pill_without_aria_expanded_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(BARE_PILL + NO_EXPANDED, labelled=False)), PAGE_REVIEW_NOTE)

    def test_aria_label_div_that_becomes_a_pill_itself_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(BOX_IS_PILL, labelled=False)), PAGE_REVIEW_NOTE)

    def test_aria_label_div_with_a_pill_in_its_own_shadow_root_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(OWN_SHADOW_PILL, labelled=False)), PAGE_REVIEW_NOTE)

    def test_aria_label_div_with_a_pill_in_a_part_shadow_root_is_review_not_pass(self):
        self.assert_review(self.fill(div_html(PART_SHADOW_PILL, labelled=False)), PAGE_REVIEW_NOTE)

    def test_aria_label_div_inside_a_pill_is_never_pass_even_when_it_turns_read_only(self):
        # The box sits in a pill already on the page (so the page-wide search skips it). After
        # the click it shows recThing and turns read-only: only the ancestor check stops a PASS.
        result = self.fill(div_html("rv.textContent = 'recThing'; rv.setAttribute('aria-readonly', 'true');",
                                    labelled=False, wrap_before=True))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assert_miss(result)

    def test_assistive_text_selection_inside_a_div_box_is_never_pass(self):
        result = self.fill(div_html(ASSISTIVE_PILL, labelled=False))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(result.evidence_ids, ())
        self.assert_clicked()

    # Path 2: the typed text is never PASS, before or after a pill shows.

    def test_typed_text_then_pill_beside_it_50_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_beside(50))))
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])
        self.assertEqual(self.page.input_value("#rv"), "recThing")

    def test_typed_text_then_pill_beside_it_400_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_beside(400))))
        self.assertEqual(self.page.input_value("#rv"), "recThing")

    def test_typed_text_then_pill_beside_it_1500_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_beside(1500))))

    def test_typed_text_then_pill_beside_it_3000_ms_later_is_never_pass(self):
        # After the 2 s limit: nothing named the pick, so the typed text is review, not PASS.
        self.assert_review(self.fill(typed_html(pill_beside(3000))), UNCONFIRMED_NOTE)

    def test_typed_text_then_pill_outside_the_field_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_elsewhere(50))), PAGE_REVIEW_NOTE)
        self.assertEqual(self.page.input_value("#rv"), "recThing")

    def test_aria_label_input_with_a_pill_beside_it_is_review_not_pass(self):
        html = typed_html(pill_beside(50), name="", box_attrs='role="combobox" aria-label="Record Variable"')
        self.assert_review(self.fill(html), PAGE_REVIEW_NOTE)

    def test_typed_text_then_field_becomes_a_pill_50_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_instead(50))))

    def test_typed_text_then_field_becomes_a_pill_400_ms_later_is_review_not_pass(self):
        self.assert_review(self.fill(typed_html(pill_instead(400))))

    def test_a_pill_that_shows_and_hides_beside_the_typed_text_is_never_pass(self):
        self.assert_review(self.fill(typed_html(PILL_FLICKER)))

    def test_typed_text_list_closes_nothing_else_changes_is_review_not_pass(self):
        # W-112: the list closed and the input kept the typed text, but nothing names the pick.
        self.assert_review(self.fill(typed_html("")), UNCONFIRMED_NOTE)
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])

    def test_a_field_that_keeps_re_rendering_is_review_not_frozen(self):
        self.assert_review(self.fill(typed_html(RERENDER_LOOP + LOCKS)), UNSTEADY_NOTE)

    def test_a_value_that_does_not_hold_is_review_not_frozen(self):
        self.assert_review(self.fill(typed_html(FLICKER + "rv.readOnly = true;")), UNSTEADY_NOTE)

    def test_a_re_render_with_the_same_value_and_no_signal_is_never_pass(self):
        rerender = ("setTimeout(() => { const next = rv.cloneNode(true); next.value = 'recThing'; "
                    "rv.replaceWith(next); rv = next; }, 100);")
        self.assert_review(self.fill(typed_html(rerender)), UNCONFIRMED_NOTE)

    # A real pick: one PASS per W-112 signal, each read on the same box after the click.

    def test_a_real_pick_that_turns_the_box_read_only_passes(self):
        self.assert_pass(self.fill(typed_html(LOCKS)), PASS_LOCKED)
        self.assertTrue(self.page.evaluate("document.getElementById('rv').readOnly"))

    def test_a_real_pick_that_changes_the_value_to_the_picked_name_passes(self):
        # The list is open before anything is typed, so the walk clicks at once and the value
        # goes from empty to the picked name.
        self.assert_pass(self.fill(typed_html(SHOWS, list_shown=True)), PASS_CHANGED)
        self.assertEqual(self.page.evaluate("window.typed"), [])

    def test_a_real_pick_that_marks_the_clicked_option_selected_passes(self):
        self.assert_pass(self.fill(typed_html(SELECTS)), PASS_SELECTED)

    def test_a_real_pick_that_points_the_box_at_the_clicked_option_passes(self):
        self.assert_pass(self.fill(typed_html(POINTS)), PASS_ACTIVE)

    def test_a_box_already_read_only_before_the_click_is_no_signal(self):
        # The box turns read-only as soon as the name is typed, before the option click.
        html = typed_html("").replace("window.typed.push(rv.value);",
                                      "window.typed.push(rv.value); rv.readOnly = true;")
        self.assert_review(self.fill(html), UNCONFIRMED_NOTE)

    def test_an_option_already_selected_before_the_click_is_no_signal(self):
        html = typed_html("").replace('<div role="option" id="opt1">',
                                      '<div role="option" id="opt1" aria-selected="true">')
        self.assert_review(self.fill(html), UNCONFIRMED_NOTE)

    def test_a_box_already_pointing_at_the_option_before_the_click_is_no_signal(self):
        html = typed_html("").replace('aria-controls="lb"', 'aria-controls="lb" aria-activedescendant="opt1"')
        self.assert_review(self.fill(html), UNCONFIRMED_NOTE)

    def test_a_box_that_already_showed_the_name_before_the_click_is_no_signal(self):
        # The list is open with recThing already in the box: nothing is typed, nothing changes.
        html = typed_html("", list_shown=True).replace('value=""', 'value="recThing"')
        self.assert_review(self.fill(html), UNCONFIRMED_NOTE)

    def test_a_pill_named_the_pick_already_on_the_page_does_not_block_a_real_pick(self):
        # The pill elsewhere was there before the click, so it was not shown by this pick.
        html = typed_html(LOCKS).replace('<div id="elsewhere"></div>', f'<div id="elsewhere">{PILL}</div>')
        self.assert_pass(self.fill(html), PASS_LOCKED)

    def test_more_new_pills_than_can_be_read_block_a_real_pick(self):
        many = "".join(f'<span class="slds-pill"><span class="slds-pill__label">recOther{i}</span></span>'
                       for i in range(guide_walk._PILL_SEARCH_MAX + 1))
        after = LOCKS + f"document.getElementById('elsewhere').insertAdjacentHTML('beforeend', {many!r});"
        self.assert_review(self.fill(typed_html(after)), UNREAD_NOTE)

    def test_a_new_pill_whose_label_cannot_be_read_blocks_a_real_pick(self):
        two_lines = ('<span class="slds-pill"><span class="slds-pill__label">recThing<br>recOther</span></span>')
        after = LOCKS + f"document.getElementById('elsewhere').insertAdjacentHTML('beforeend', {two_lines!r});"
        result = self.fill(typed_html(after))
        self.assert_review(result, UNREAD_NOTE)

    # Timing: a single re-render restarts the hold; the pill check comes first on every poll;
    # a box swapped during the final read is never PASS.

    def test_a_real_pick_that_re_renders_once_restarts_its_hold_and_passes_in_time(self):
        def rerender(n):
            if n == 1:
                self.page.evaluate("() => { const next = rv.cloneNode(true); next.value = 'recThing'; "
                                   "next.readOnly = true; rv.replaceWith(next); rv = next; }")
        # Reads at 0, 0.25 (new element: hold restarts), 0.5, 0.75, 1.0, 1.25 (holds 1.0 s): PASS
        # at the 1.25 s limit. Without the restart the hold would start at 0.5 and run out.
        result = self.fill_scripted(typed_html("rv.readOnly = true;"), on_poll=rerender, page_wait_s=1.25)
        self.assert_pass(result, PASS_LOCKED)

    def test_a_pill_that_shows_only_on_the_last_poll_is_review_not_pass(self):
        # Reads at 0, 0.25, 0.5 and 0.75 show no pill; the pill shows just before the 1.0 s poll,
        # on which the hold completes with a real signal. The pill check must still come first.
        def late_pill():
            self.page.evaluate("() => host.insertAdjacentHTML('beforeend', pillHtml)")
        self.assert_review(self.fill_scripted(typed_html(LOCKS), on_hold=late_pill))

    def test_the_box_resolving_elsewhere_on_the_final_read_is_never_pass(self):
        # On the final read (the 5th), before the box locator resolves again, a new box takes the
        # label; the held one stays on the page, connected, showing recThing and read-only.
        def swap(n, when, k):
            if n == 5 and when == "before" and k == 2:
                self.page.evaluate("""() => {
                  const next = rv.cloneNode(true); next.value = 'recThing'; next.readOnly = true;
                  rv.id = 'rv-old'; next.id = 'rv'; rv.after(next);
                }""")
        result = self.fill_scripted(typed_html(LOCKS), on_handle=swap)
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.locator("#rv-old").count(), 1)

    def test_the_box_removed_after_its_final_handle_is_never_pass(self):
        # On the final read, right after the box locator resolves the second time, the box is
        # removed from the page: the one evaluate reads it as the same element, not connected.
        def drop(n, when, k):
            if n == 5 and when == "after" and k == 2:
                self.page.evaluate("() => rv.remove()")
        result = self.fill_scripted(typed_html(LOCKS), on_handle=drop)
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.locator("#rv").count(), 0)

    # The text-field fallback (no combobox or searchbox role) follows the same rule.

    def test_a_plain_text_field_passes_on_its_typed_value(self):
        result = self.fill(text_html())
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.note, "Record Variable is recThing.")
        self.assertEqual(self.page.input_value("#rv"), "recThing")

    def test_a_plain_text_field_that_shows_a_pill_50_ms_after_typing_is_review_not_pass(self):
        html = text_html(on_input="setTimeout(() => host.insertAdjacentHTML('beforeend', pillHtml), 50);")
        result = self.fill(html)
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertEqual(result.note, TYPED_PILL_NOTE)
        self.assertFalse(result.freeze)

    def test_a_plain_text_field_that_shows_a_pill_at_once_is_review_not_pass(self):
        html = text_html(on_input="document.getElementById('elsewhere').insertAdjacentHTML('beforeend', pillHtml);")
        result = self.fill(html)
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertEqual(result.note, TYPED_PILL_NOTE)

    def test_a_text_field_that_autocompletes_from_a_list_is_review_and_nothing_is_typed(self):
        result = self.fill(text_html(attrs='aria-autocomplete="list"'))
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertEqual(result.note, MARKED_NOTE)
        self.assertFalse(result.freeze)
        self.assertEqual(self.page.evaluate("window.typed"), [])

    def test_a_lookup_like_text_field_already_showing_the_name_is_review_not_already_set(self):
        html = text_html(attrs='aria-haspopup="listbox"').replace('value=""', 'value="recThing"')
        result = self.fill(html)
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertEqual(result.note, MARKED_NOTE)

    def test_a_text_field_inside_a_lookup_host_is_review_and_nothing_is_typed(self):
        html = text_html().replace("<sample-field", "<sample-lookup").replace("</sample-field>", "</sample-lookup>")
        result = self.fill(html)
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertEqual(result.note, MARKED_NOTE)
        self.assertEqual(self.page.evaluate("window.typed"), [])

    # The walk goes on after each review result, and the run is inconclusive (same org).

    def walk_review(self, after_js, note):
        page = self.open(typed_html(after_js))
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
        self.assertEqual(outcome.notes.get("s2"), note)
        self.assertEqual(tuple(outcome.evidence.get("s2", ())), ())
        self.assertEqual(ran, ["s3"])
        report = tuple(
            ReportStep(item.step_id, 1, item.text, outcome.statuses[item.step_id],
                       tuple(outcome.evidence.get(item.step_id, ())), outcome.notes.get(item.step_id, ""))
            for item in steps
        )
        result = SimpleNamespace(signed_in=True, message="", report_steps=report)
        self.assertEqual(outcome_of(result), (INCONCLUSIVE, note))
        self.assertEqual(gate_outcome_of(result), INCONCLUSIVE)

    def test_walk_goes_on_after_a_delayed_pill_and_run_is_inconclusive(self):
        self.walk_review(pill_beside(400), REVIEW_NOTE)

    def test_walk_goes_on_after_typed_text_alone_and_run_is_inconclusive(self):
        self.walk_review("", UNCONFIRMED_NOTE)

    def test_walk_goes_on_after_a_re_rendering_field_and_run_is_inconclusive(self):
        self.walk_review(RERENDER_LOOP + LOCKS, UNSTEADY_NOTE)

    def test_walk_goes_on_after_a_flickering_value_and_run_is_inconclusive(self):
        self.walk_review(FLICKER + "rv.readOnly = true;", UNSTEADY_NOTE)


if __name__ == "__main__":
    unittest.main()
