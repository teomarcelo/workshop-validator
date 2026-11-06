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
  <div id="palette">
    <ul role="list" id="fields">
      <li><button type="button" id="f-name">Name</button></li>
      <li><button type="button" id="f-stage">Stage</button></li>
    </ul>
  </div>
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
    rerender: () => {
      const list = document.getElementById('fields');
      list.replaceWith(list.cloneNode(true));
      place();
    },
  };
  document.getElementById('f-name').addEventListener('click', (event) => {
    event.currentTarget.classList.add('picked');
    actions[window.mode]();
  });
</script>
</body></html>"""


def _click_step():
    return step("P1N1", "Exercise 1: Sample", "Task 1: Sample", "1",
                "Scroll to find the Name field. Click it once to place it in the middle panel.")


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

    def click(self, mode, canvas="", props=""):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(PAGE.replace("__CANVAS__", canvas).replace("__PROPS__", props))
        page.evaluate("(mode) => { window.mode = mode; }", mode)
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=3, page_poll_s=0.2)
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


class NewElementReadFailsClosedTests(unittest.TestCase):
    """The reader never passes on a failed, missing or odd read (no browser needed)."""

    class Handle:
        def __init__(self, raw=None, error=False):
            self.raw, self.error = raw, error

        def evaluate(self, script, arg=None):
            if self.error:
                raise RuntimeError("read failed")
            return self.raw

    def test_unreadable_reads_are_false(self):
        good_text = {"text": "Name", "total": 4, "unclear": False, "parts": []}
        cases = [
            (None, "tok"),
            (self.Handle(error=True), "tok"),
            (self.Handle(raw={"state": "unproven"}), "tok"),
            (self.Handle(raw={"state": "gone", "texts": [good_text]}), "tok"),
            (self.Handle(raw="read"), "tok"),
            (self.Handle(raw={"state": "read", "texts": [good_text]}), ""),
            (self.Handle(raw={"state": "read", "texts": [dict(good_text, unclear=True)]}), "tok"),
            (self.Handle(raw={"state": "read", "texts": [{"text": "Name\nStage", "total": 9,
                                                          "unclear": False, "parts": []}]}), "tok"),
            (self.Handle(raw={"state": "read", "values": ["Account Name"]}), "tok"),
        ]
        for handle, token in cases:
            with self.subTest(raw=getattr(handle, "raw", None), token=token):
                self.assertFalse(guide_walk._new_element_shows(handle, token, "Name"))

    def test_exact_reads_are_true(self):
        good_text = {"text": "Name", "total": 4, "unclear": False, "parts": []}
        self.assertTrue(guide_walk._new_element_shows(
            self.Handle(raw={"state": "read", "texts": [good_text]}), "tok", "Name"))
        self.assertTrue(guide_walk._new_element_shows(
            self.Handle(raw={"state": "read", "values": ["Name"]}), "tok", "Name"))


if __name__ == "__main__":
    unittest.main()
