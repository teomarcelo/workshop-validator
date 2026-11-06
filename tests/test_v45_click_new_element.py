"""V45 (P006N003): a click passes when a new element, by identity, shows the clicked name exactly.

Real-browser tests on synthetic HTML: a dialog with a field list of buttons on the left, an empty
canvas in the middle and a properties panel on the right. The clicked button gets only a styling
class (no aria state), and the placed field is not a button, link, tab, combobox, textbox or
heading, so before V45 no evidence could be read and the step was NEEDS_REVIEW.
"""

import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.automate import INCONCLUSIVE, SUCCESS, gate_outcome_of, outcome_of
from workshop_validator.report import ReportStep
from tests.test_guide_walk import step

from types import SimpleNamespace


PAGE = """<!DOCTYPE html><html><head><style>
  body { margin: 0; font: 14px sans-serif; }
  #shell { display: flex; gap: 8px; padding: 8px; }
  #palette, #canvas, #props { border: 1px solid #ccc; min-height: 240px; }
  #palette { width: 180px; } #canvas { width: 300px; } #props { width: 260px; }
  .picked { background: #cde; }
</style></head><body>
<div role="dialog" aria-label="Edit Sample Screen" id="shell">
  <div id="palette">__PALETTE__</div>
  <div id="canvas">__CANVAS__</div>
  <div id="props">__PROPS__</div>
</div>
<script>
  const canvas = document.getElementById('canvas');
  const props = document.getElementById('props');
  const add = (holder, html) => { const box = document.createElement('div'); box.innerHTML = html; holder.appendChild(box); };
  const place = () => {
    add(canvas, '<div class="cmp"><label for="c1"><abbr title="required">*</abbr> Name</label><input id="c1" type="text"></div>');
    add(props, '<h2>Field Information</h2><label for="fl">Field Label</label><input id="fl" type="text" value="Name">');
  };
  const actions = {
    place,
    text: () => add(canvas, '<div class="cmp"><span class="req">*</span> <span class="lbl">Name</span></div>'),
    nothing: () => {},
    reveal: () => { document.getElementById('old').style.display = 'block'; },
    stage: () => add(canvas, '<div class="cmp"><span class="lbl">Stage</span></div>'),
    substring: () => {
      add(canvas, '<div class="cmp"><span class="lbl">Account Name</span></div>');
      add(props, '<label for="fl">Field Label</label><input id="fl" type="text" value="Account Name">');
    },
    insource: () => {
      const li = document.createElement('li');
      li.innerHTML = '<span class="hl">Name</span>';
      document.getElementById('fields').appendChild(li);
    },
    delayed: () => setTimeout(place, 800),
    invisible: () => add(canvas, '<span style="visibility:hidden">Name</span>'),
    clipped: () => add(canvas, '<div style="height:0;overflow:hidden"><span>Name</span></div>'),
    faint: () => add(canvas, '<span style="opacity:0.05">Name</span>'),
    transparent: () => add(canvas, '<span style="color:transparent">Name</span>'),
    offscreen: () => add(canvas, '<span style="position:absolute;left:-9999px">Name</span>'),
    tooltip: () => add(document.body, '<div role="tooltip"><span>Name</span></div>'),
    rerendercanvas: () => { canvas.innerHTML = canvas.innerHTML; },
    clonecanvas: () => { const box = document.getElementById('canvas'); box.replaceWith(box.cloneNode(true)); },
    rerenderprops: () => { props.innerHTML = props.innerHTML; },
    laterrerender: () => setTimeout(() => { canvas.innerHTML = canvas.innerHTML; }, 1500),
    hover: () => {},
    hoverlate: () => {},
    hoverstream: () => {},
    wrap: () => { const c = document.querySelector('#canvas .cmp'); c.innerHTML = '<div>' + c.innerHTML + '</div>'; },
    bold: () => { document.querySelector('#canvas .lbl').innerHTML = '<b>Name</b>'; },
    body: () => setTimeout(() => add(document.body, 'Name'), 1200),
    late: () => setTimeout(() => add(canvas, '<span>Name</span>'), 6000),
    scroll: () => { canvas.innerHTML = canvas.innerHTML; document.getElementById('far').scrollIntoView(); },
    chip: () => {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.textContent = 'Name';
      document.getElementById('fields').prepend(chip);
    },
    rerender: () => {
      const list = document.getElementById('fields');
      list.replaceWith(list.cloneNode(true));
      place();
    },
  };
  document.getElementById('f-name').addEventListener('mouseenter', () => {
    if (window.mode === 'hover') add(canvas, '<span>Name</span>');
    if (window.mode === 'hoverlate') setTimeout(() => add(canvas, '<span>Name</span>'), 300);
    if (window.mode === 'hoverstream') setInterval(() => add(canvas, '<span>Name</span>'), 150);
  });
  document.getElementById('f-name').addEventListener('click', (event) => {
    event.currentTarget.classList.add('picked');
    actions[window.mode]();
  });
</script>
</body></html>"""

LIST_PALETTE = """<ul role="list" id="fields">
      <li><button type="button" id="f-name">Name</button></li>
      <li><button type="button" id="f-stage">Stage</button></li>
    </ul>"""

# Two buttons with no list role or list tag around them: too few to be a palette (W-132).
DIV_PALETTE = """<div id="fields">
      <div><button type="button" id="f-name">Name</button></div>
      <div><button type="button" id="f-stage">Stage</button></div>
    </div>"""

# The same plain div palette with three named buttons.
DIV3_PALETTE = """<div id="fields">
      <div><button type="button" id="f-name">Name</button></div>
      <div><button type="button" id="f-stage">Stage</button></div>
      <div><button type="button" id="f-amount">Amount</button></div>
    </div>"""

# A plain div palette of [Name][i] rows: one row holds two controls, never a palette alone.
ROW_PALETTE = """<div id="fields">
      <div><button type="button" id="f-name">Name</button><button type="button" aria-label="Info">i</button></div>
      <div><button type="button" id="f-stage">Stage</button><button type="button" aria-label="Info">i</button></div>
      <div><button type="button" id="f-amount">Amount</button><button type="button" aria-label="Info">i</button></div>
    </div>"""

# Canvas content far below the first screen, so it is out of view at the mark.
FAR_CANVAS = '<div style="height:2000px"></div><div class="cmp"><span class="lbl" id="far">Name</span></div>'

DIALOG = 'role="dialog" aria-label="Edit Sample Screen"'

# One button alone: no list and no ancestor with two controls of its role.
LONE_PALETTE = """<div id="fields"><button type="button" id="f-name">Name</button></div>"""

NO_SOURCE_NOTE = "No source region was found around the clicked control"
NO_SCOPE_NOTE = "No dialog or main region was found around the clicked control"
MOVING_NOTE = "The name count was still changing after the hover"


def _click_step():
    return step("P1N1", "Exercise 1: Sample", "Task 1: Sample", "1",
                "Click Name once to add it to the board.")


def _gate(status, note):
    steps = (ReportStep("P0N1", 1, "Earlier step", "PASS", note="ok"),
             ReportStep("P1N1", 1, "Click step", status, note=note))
    run = SimpleNamespace(signed_in=True, message="", report_steps=steps)
    return outcome_of(run)[0], gate_outcome_of(run)


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class ClickNewElementBrowserTests(unittest.TestCase):

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

    def click(self, mode, canvas="", props="", palette=LIST_PALETTE, region=DIALOG, timeout_s=3):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(PAGE.replace("__PALETTE__", palette).replace("__CANVAS__", canvas)
                         .replace("__PROPS__", props).replace(DIALOG, region))
        page.evaluate("(mode) => { window.mode = mode; }", mode)
        state = guide_walk.WalkState(page=page, timeout_s=timeout_s, interval_s=0.2,
                                     page_wait_s=timeout_s, page_poll_s=0.2)
        state.step_deadline = state.clock() + 60
        result = guide_walk._act_click(state, _click_step(), "Name")
        self.assertIsNotNone(result)
        self.assertEqual(page.locator("#f-name.picked").count(), 1, "the Name button was clicked")
        return result

    def assert_needs_review(self, result):
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assertIn("Clicked Name, but no change could be read from the page.", result.note)
        self.assertTrue(result.freeze)
        self.assertEqual(_gate(result.status, result.note), (INCONCLUSIVE, INCONCLUSIVE))

    def assert_new_element_pass(self, result):
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.evidence_ids, ("P1N1:after-state:-new-element-shows-Name",))
        self.assertIn("new-element-click", result.suspect)
        self.assertEqual(_gate(result.status, result.note), (SUCCESS, SUCCESS))

    # Fails on 5bda7b9 (NEEDS_REVIEW there).
    def test_real_placement_passes(self):
        self.assert_new_element_pass(self.click("place"))

    # Fails on 5bda7b9 (NEEDS_REVIEW there): a new canvas label read by its visible text.
    def test_new_canvas_label_by_visible_text_passes(self):
        self.assert_new_element_pass(self.click("text"))

    # Fails on 5bda7b9 (NEEDS_REVIEW there).
    def test_placement_after_800_ms_passes(self):
        self.assert_new_element_pass(self.click("delayed"))

    def test_click_that_does_nothing_needs_review(self):
        self.assert_needs_review(self.click("nothing"))

    def test_name_already_shown_before_the_click_needs_review(self):
        canvas = '<div class="cmp"><span class="lbl">Name</span></div>'
        props = '<label for="fl">Field Label</label><input id="fl" type="text" value="Name">'
        self.assert_needs_review(self.click("nothing", canvas=canvas, props=props))

    def test_hidden_element_shown_by_the_click_is_not_new_and_needs_review(self):
        canvas = '<div class="cmp" id="old" style="display:none"><span class="lbl">Name</span></div>'
        self.assert_needs_review(self.click("reveal", canvas=canvas))

    def test_new_element_with_another_name_needs_review(self):
        self.assert_needs_review(self.click("stage"))

    def test_new_element_with_the_name_as_a_substring_needs_review(self):
        self.assert_needs_review(self.click("substring"))

    def test_new_element_only_inside_the_source_list_needs_review(self):
        self.assert_needs_review(self.click("insource"))

    def test_new_element_with_visibility_hidden_needs_review(self):
        self.assert_needs_review(self.click("invisible"))

    def test_new_element_clipped_by_its_container_needs_review(self):
        self.assert_needs_review(self.click("clipped"))

    def test_new_element_at_near_zero_opacity_needs_review(self):
        self.assert_needs_review(self.click("faint"))

    # Only the PR 61 visible-text rules see transparent text as not shown.
    def test_new_element_with_transparent_text_needs_review(self):
        self.assert_needs_review(self.click("transparent"))

    def test_new_element_off_screen_needs_review(self):
        self.assert_needs_review(self.click("offscreen"))

    def test_new_tooltip_with_the_name_needs_review(self):
        self.assert_needs_review(self.click("tooltip"))

    def test_source_list_re_rendered_needs_review(self):
        self.assert_needs_review(self.click("rerender"))

    # Fails on 0dbccac (PASS there): new nodes, old text.
    def test_canvas_showing_the_name_re_rendered_needs_review(self):
        canvas = '<div class="cmp"><span class="lbl">Name</span></div>'
        self.assert_needs_review(self.click("rerendercanvas", canvas=canvas))

    # Fails on 0dbccac (PASS there).
    def test_canvas_showing_the_name_replaced_by_a_clone_needs_review(self):
        canvas = '<div class="cmp"><span class="lbl">Name</span></div>'
        self.assert_needs_review(self.click("clonecanvas", canvas=canvas))

    # Fails on 0dbccac (PASS there).
    def test_props_panel_showing_the_name_re_rendered_needs_review(self):
        props = '<label for="fl">Field Label</label><input id="fl" type="text" value="Name">'
        self.assert_needs_review(self.click("rerenderprops", props=props))

    # Fails on 0dbccac (PASS there): the poll must not take an unrelated later re-render.
    def test_click_does_nothing_and_a_timer_re_renders_later_needs_review(self):
        canvas = '<div class="cmp"><span class="lbl">Name</span></div>'
        self.assert_needs_review(self.click("laterrerender", canvas=canvas))

    # Fails on 0dbccac (PASS there): what the hover added is before-state.
    def test_hover_added_name_and_click_places_nothing_needs_review(self):
        self.assert_needs_review(self.click("hover"))

    def test_real_placement_from_a_plain_div_palette_passes(self):
        self.assert_new_element_pass(self.click("place", palette=DIV3_PALETTE))

    # W-132: two controls are not a palette, so there is no source region.
    def test_two_button_plain_div_palette_needs_review_with_a_diagnostic(self):
        result = self.click("place", palette=DIV_PALETTE)
        self.assert_needs_review(result)
        self.assertIn(NO_SOURCE_NOTE, result.note)

    def test_real_placement_in_a_main_region_passes(self):
        self.assert_new_element_pass(self.click("place", region='role="main"'))

    def test_no_dialog_or_main_region_needs_review_with_a_diagnostic(self):
        result = self.click("place", region='class="plain"')
        self.assert_needs_review(result)
        self.assertIn(NO_SCOPE_NOTE, result.note)

    # W-132 tests below fail on ca1084d (PASS there).
    def test_canvas_name_re_rendered_inside_an_extra_wrapper_needs_review(self):
        canvas = '<div class="cmp"><span class="lbl">Name</span></div>'
        self.assert_needs_review(self.click("wrap", canvas=canvas))

    def test_canvas_name_re_rendered_as_bold_needs_review(self):
        canvas = '<div class="cmp"><span class="lbl">Name</span></div>'
        self.assert_needs_review(self.click("bold", canvas=canvas))

    def test_hover_adds_the_name_after_300_ms_and_click_does_nothing_needs_review(self):
        self.assert_needs_review(self.click("hoverlate"))

    def test_hover_keeps_adding_the_name_needs_review_with_a_diagnostic(self):
        result = self.click("hoverstream")
        self.assert_needs_review(result)
        self.assertIn(MOVING_NOTE, result.note)

    def test_timer_adds_the_name_outside_the_dialog_needs_review(self):
        self.assert_needs_review(self.click("body"))

    def test_timer_adds_the_name_in_the_dialog_after_6_s_needs_review(self):
        self.assert_needs_review(self.click("late", timeout_s=8))

    def test_name_scrolled_out_of_view_then_re_rendered_into_view_needs_review(self):
        self.assert_needs_review(self.click("scroll", canvas=FAR_CANVAS))

    def test_name_chip_inside_a_row_palette_needs_review(self):
        self.assert_needs_review(self.click("chip", palette=ROW_PALETTE))

    def test_real_placement_from_a_row_palette_passes(self):
        self.assert_new_element_pass(self.click("place", palette=ROW_PALETTE))

    def test_plain_div_palette_click_that_does_nothing_needs_review(self):
        self.assert_needs_review(self.click("nothing", palette=DIV3_PALETTE))

    def test_no_source_region_needs_review_with_a_diagnostic(self):
        result = self.click("place", palette=LONE_PALETTE)
        self.assert_needs_review(result)
        self.assertIn(NO_SOURCE_NOTE, result.note)

    def test_list_palette_has_no_source_diagnostic(self):
        result = self.click("nothing")
        self.assert_needs_review(result)
        self.assertNotIn(NO_SOURCE_NOTE, result.note)


class NewElementReadFailsClosedTests(unittest.TestCase):
    """The reader never passes on a failed, missing or odd read (no browser needed)."""

    class Handle:
        def __init__(self, raw=None, error=False):
            self.raw, self.error = raw, error

        def evaluate(self, script, arg=None):
            if self.error:
                raise RuntimeError("read failed")
            return self.raw

    GOOD = {"text": "Name", "total": 4, "unclear": False, "parts": []}

    def text(self, raw=None, fresh=True):
        return {"kind": "text", "raw": self.GOOD if raw is None else raw, "fresh": fresh}

    def value(self, value="Name", fresh=True):
        return {"kind": "value", "value": value, "fresh": fresh}

    def read(self, after, before=()):
        return self.Handle(raw={"state": "read", "before": list(before), "after": list(after)})

    def test_unreadable_reads_are_false(self):
        good = self.text()
        cases = [
            (None, "tok"),
            (self.Handle(error=True), "tok"),
            (self.Handle(raw={"state": "unproven"}), "tok"),
            (self.Handle(raw={"state": "late", "before": [], "after": [good]}), "tok"),
            (self.Handle(raw={"state": "gone", "before": [], "after": [good]}), "tok"),
            (self.Handle(raw="read"), "tok"),
            (self.read([good]), ""),
            (self.read([self.text(dict(self.GOOD, unclear=True))]), "tok"),
            (self.read([self.text({"text": "Name\nStage", "total": 9, "unclear": False, "parts": []})]), "tok"),
            (self.read([self.value("Account Name")]), "tok"),
            # The count cannot be read: fail closed.
            (self.Handle(raw={"state": "read", "after": [good]}), "tok"),
            (self.Handle(raw={"state": "read", "before": None, "after": [good]}), "tok"),
            (self.Handle(raw={"state": "read", "before": [], "after": None}), "tok"),
            (self.Handle(raw={"state": "read", "before": "0", "after": [good]}), "tok"),
            # A re-render: new nodes, same count.
            (self.read([good], before=[self.text(fresh=False)]), "tok"),
            (self.read([self.value()], before=[self.value(fresh=False)]), "tok"),
            # The count went up, but no element is new by identity.
            (self.read([self.text(fresh=False)]), "tok"),
            (self.read([self.text(fresh="yes")]), "tok"),
            # The only new element does not show the name exactly.
            (self.read([self.text(fresh=False), self.value("Account Name")]), "tok"),
        ]
        for handle, token in cases:
            with self.subTest(raw=getattr(handle, "raw", None), token=token):
                self.assertFalse(guide_walk._new_element_shows(handle, token, "Name"))

    def test_exact_reads_are_true(self):
        self.assertTrue(guide_walk._new_element_shows(self.read([self.text()]), "tok", "Name"))
        self.assertTrue(guide_walk._new_element_shows(self.read([self.value()]), "tok", "Name"))
        self.assertTrue(guide_walk._new_element_shows(
            self.read([self.text(fresh=False), self.text()], before=[self.text(fresh=False)]), "tok", "Name"))


class NewElementSuspectFlagTests(unittest.TestCase):
    """W-132: every PASS on the new-element path is flagged, so step-verifier always checks it."""

    def test_new_element_evidence_is_flagged(self):
        flags = guide_walk._detect_suspect_flags(
            _click_step(), "after-state: new element shows Name", frozenset(), frozenset())
        self.assertIn("new-element-click", flags)

    def test_other_evidence_is_not_flagged_as_new_element(self):
        flags = guide_walk._detect_suspect_flags(
            _click_step(), "after-state: dialog opened", frozenset(), frozenset())
        self.assertNotIn("new-element-click", flags)


class MarkSettleTests(unittest.TestCase):
    """W-132: the mark is taken only once the in-scope name count has settled after the hover."""

    class Held:
        def __init__(self, counts):
            self.counts, self.reads = list(counts), 0

        def evaluate(self, script, arg=None):
            index = min(self.reads, len(self.counts) - 1)
            self.reads += 1
            count = self.counts[index]
            if isinstance(count, str):
                return {"state": count}
            return {"state": "marked", "count": count}

        def dispose(self):
            pass

    class Target:
        def __init__(self, held):
            self.held, self.hovers = held, 0

        def hover(self, timeout=None):
            self.hovers += 1

        def element_handle(self, timeout=None):
            return self.held

    def mark(self, counts):
        held = self.Held(counts)
        target = self.Target(held)
        started = guide_walk.time.monotonic()
        result = guide_walk._mark_before_click(target, "Name")
        self.assertEqual(target.hovers, 1, "one hover only")
        self.assertLessEqual(guide_walk.time.monotonic() - started, 2.5, "at most about 2 s added")
        return result, held

    def test_stable_count_is_marked(self):
        (held, token, diag), fake = self.mark([0])
        self.assertIs(held, fake)
        self.assertTrue(token)
        self.assertEqual(diag, "")
        self.assertGreaterEqual(fake.reads, 5, "the count was read for about 500 ms")

    def test_count_that_changes_then_settles_is_marked_after_it_settles(self):
        (held, token, diag), fake = self.mark([0, 0, 0, 1])
        self.assertIs(held, fake)
        self.assertGreaterEqual(fake.reads, 9, "500 ms of stable reads after the change")

    def test_moving_count_is_never_marked(self):
        (held, token, diag), _fake = self.mark(list(range(200)))
        self.assertIsNone(held)
        self.assertEqual(token, "")
        self.assertIn("still changing after the hover", diag)

    def test_no_scope_and_no_source_are_not_marked(self):
        for state, words in (("no-scope", "No dialog or main region"), ("no-source", "No source region")):
            with self.subTest(state=state):
                (held, token, diag), _fake = self.mark([state])
                self.assertIsNone(held)
                self.assertIn(words, diag)

    def test_odd_reads_are_not_marked(self):
        class Raw(self.Held):
            def __init__(self, raw):
                super().__init__([0])
                self.raw = raw

            def evaluate(self, script, arg=None):
                return self.raw

        for raw in (None, "marked", {"state": "unproven"}, {"state": "marked"},
                    {"state": "marked", "count": True}, {"state": "marked", "count": "0"}):
            with self.subTest(raw=raw):
                self.assertEqual(guide_walk._mark_before_click(self.Target(Raw(raw)), "Name"), (None, "", ""))

if __name__ == "__main__":
    unittest.main()
