"""V40: a lookup option is read by its visible text only (P006N002). A list named by
aria-controls that holds no option falls back to the dialog that holds the box. A lookup that
never shows its option is the step's own frozen UI_MISMATCH with safe facts. A dialog behind the
guide's own aria-modal dialog never covers the page, and Escape is pressed only with focus
inside the pop-up it closes."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.automate import FAILED, gate_outcome_of, outcome_of
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide
from workshop_validator.report import ReportStep

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
SMOKE_REASON = "Set RUN_BROWSER_SMOKE=1 to run the real-browser checks."

BUILDER_URL = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
STEP = "Record Variable: varWidgetRec"

ASSISTIVE_CSS = """
<style>
  .slds-assistive-text { position: absolute !important; margin: -1px !important; border: 0 !important;
    padding: 0 !important; width: 1px !important; height: 1px !important; overflow: hidden !important;
    clip: rect(0 0 0 0) !important; white-space: nowrap !important; }
  .slds-backdrop { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5); z-index: 1; }
  #dlg { position: relative; z-index: 2; background: white; }
  #tips { position: fixed; top: 0; right: 0; z-index: 0; background: white; }
</style>
"""


def screen_html(option="varWidgetRec", controls="lb", backdrop=False, option_html=None, option_style=""):
    """The guide's own Sample Screen dialog with a Record Variable lookup whose list is open."""
    if option_html is None:
        option_html = ('<span class="slds-assistive-text">Record</span><svg aria-hidden="true" '
                       f'width="12" height="12"></svg><strong>{option}</strong>')
    empty = '<div id="lb-empty"></div>' if controls == "lb-empty" else ""
    behind = """
<div class="slds-backdrop"></div>
<section id="tips" role="dialog" aria-labelledby="p">
  <h2 id="p">Sample Tips</h2><button><svg aria-hidden="true" width="10" height="10"></svg></button>
</section>""" if backdrop else ""
    return f"""{ASSISTIVE_CSS}
{behind}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <div class="slds-form-element">
    <label for="rv">Record Variable</label>
    <input id="rv" role="combobox" aria-expanded="true" aria-controls="{controls}"
           value="varWidgetRec" autocomplete="off">
    {empty}
    <div id="lb" role="listbox">
      <div role="presentation"><span>All Resources</span></div>
      <ul role="group" aria-labelledby="g1">
        <li role="presentation" id="g1"><span>Record Variables</span></li>
        <li role="option" id="opt1" style="{option_style}">{option_html}</li>
      </ul>
      <a href="#">Expand Search</a>
      <div role="option" id="opt2"><span>+ New Resource</span></div>
    </div>
  </div>
</div>
<script>
  window.chosen = [];
  window.escapes = 0;
  const box = document.getElementById("rv");
  const list = document.getElementById("lb");
  document.addEventListener("keydown", (e) => {{ if (e.key === "Escape") window.escapes += 1; }}, true);
  box.addEventListener("keydown", (e) => {{ if (e.key === "Escape") box.value = ""; }});
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    box.value = picked.querySelector("strong, span:not(.slds-assistive-text)").textContent;
    box.setAttribute("aria-expanded", "false");
    list.hidden = true;
  }});
  box.focus();
</script>
"""


SOFT_WRAP = "width: min-content"
LABEL_CASES_HTML = ASSISTIVE_CSS + """
<div role="listbox">
  <div role="option" id="mark-text" style="width: min-content"><mark>Account</mark> Name</div>
  <div role="option" id="mark-span" style="width: min-content"><mark>Account</mark> <span>Name</span></div>
  <div role="option" id="two-block"><span style="display:block">Account</span>
    <span style="display:block">Sample description line</span></div>
  <div role="option" id="br-line">Account<br>Sample description line</div>
  <div role="option" id="clip-rect"><span style="position:absolute; width:1px; height:1px; overflow:hidden;
    clip:rect(0 0 0 0)">Record</span><strong>Account</strong></div>
  <div role="option" id="clip-path"><span style="position:absolute; width:1px; height:1px; overflow:hidden;
    clip-path:inset(50%)">Record</span><strong>Account</strong></div>
  <div role="option" id="hidden-part"><span style="display:none">Record</span>Account</div>
</div>
"""

RESOURCE_TYPE_EMPTY_HTML = """
<div role="dialog" aria-modal="true" aria-labelledby="nr-t">
  <h2 id="nr-t">New Resource</h2>
  <label for="rt">Resource Type</label>
  <input id="rt" role="combobox" aria-expanded="true" aria-controls="rt-list" autocomplete="off">
  <div id="rt-list" role="listbox"></div>
  <div role="listbox" aria-label="Sample Other List"><div role="option" id="other-var">Variable</div></div>
</div>
"""


WELCOME_HTML = """
<label for="f">Sample Field</label><input id="f" value="keep">
<div id="welcome" role="dialog" aria-label="Sample Welcome"><p>Welcome to the sample page.</p></div>
<button id="go" hidden>Sample Go</button>
<script>
  window.escapes = 0;
  window.goClicks = 0;
  document.getElementById("go").addEventListener("click", () => { window.goClicks += 1; });
  const field = document.getElementById("f");
  const welcome = document.getElementById("welcome");
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    window.escapes += 1;
    if (welcome.contains(document.activeElement)) {
      welcome.remove();
      document.getElementById("go").hidden = false;
    }
  }, true);
  field.addEventListener("keydown", (e) => { if (e.key === "Escape") field.value = ""; });
  field.focus();
</script>
"""


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


class _RaisingOption:
    """An option whose browser read fails. Its inner text holds a screen-reader-only word."""

    def evaluate(self, script, arg=None):
        raise RuntimeError("detached")

    def inner_text(self, timeout=None):
        return "Record varWidgetRec"


class SafeReadTests(unittest.TestCase):
    def test_option_that_cannot_be_read_in_the_browser_has_no_label(self):
        self.assertEqual(guide_walk._option_label(_RaisingOption()), "")
        self.assertEqual(guide_walk._option_lines(_RaisingOption()), [])

    def test_popup_that_cannot_be_read_is_never_focused_for_escape(self):
        self.assertFalse(guide_walk._focus_inside(_RaisingOption()))
        self.assertFalse(guide_walk._focus_inside(None))

    def test_typed_prefix_kind_is_named_without_the_option_text(self):
        self.assertEqual(guide_walk._option_kind("varWidgetRec", "varWidgetRec"), "match")
        self.assertEqual(guide_walk._option_kind("varWidgetRecord", "varWidgetRec"), "typed-prefix")
        self.assertEqual(guide_walk._option_kind("varWidget", "varWidgetRec"), "typed-prefix")
        self.assertEqual(guide_walk._option_kind("RecordvarWidgetRec", "varWidgetRec"), "other")
        self.assertEqual(guide_walk._option_kind("", "varWidgetRec"), "other")


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class RecordVariablePickBrowserTests(unittest.TestCase):
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

    def fill(self, html, text=STEP):
        page = self.open(html)
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=2, page_poll_s=0.2)
        # An earlier guide step opened Sample Screen; it is the guide's own dialog.
        guide_walk._open_guide_dialog(state, "Sample Screen")
        return guide_walk.attempt_step(state, make_step("rv", text))

    def test_lookup_miss_under_a_covering_modal_pop_up_keeps_the_pop_up_rule(self):
        cover = ('<div id="cover" role="dialog" aria-modal="true" aria-label="Sample Welcome">'
                 '<p>Welcome to the sample page.</p></div>')
        result = self.fill(screen_html(option="varOtherRec").replace("<script>", cover + "<script>", 1))
        self.assertEqual(result.status, "ENV_BLOCKED", result.note)
        self.assertIn('"Sample Welcome"', result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])

    def walk(self, html, second, snapshot_dir=None):
        page = self.open(html)
        steps = (make_step("s1", "Add a Sample Screen."), make_step("s2", second), make_step("s3", second))
        checks = (GuideCheck("opened", lambda item: item.step_id == "s1", opened_screen),)
        outcome = walk_guide(page, SimpleNamespace(steps=steps), checks=checks, timeout_s=3, interval_s=0.2,
                             page_wait_s=1.5, page_poll_s=0.2, snapshot_dir=snapshot_dir)
        return outcome, steps

    def test_option_with_screen_reader_text_is_picked_by_its_visible_line(self):
        result = self.fill(screen_html())
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        # W-112: the box shows only the text the walk typed; nothing names the pick.
        self.assertEqual(result.note, 'Record Variable option varWidgetRec was clicked. The field shows the typed text; nothing on the page confirms the pick, so please confirm.')
        self.assertFalse(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])
        self.assertEqual(self.page.locator("#rv").input_value(), "varWidgetRec")
        self.assertEqual(self.page.evaluate("window.escapes"), 0)

    def test_option_label_skips_assistive_and_hidden_parts(self):
        page = self.open(screen_html())
        self.assertEqual(guide_walk._option_lines(page.locator("#opt1")), ["varWidgetRec"])
        self.assertEqual(guide_walk._option_label(page.locator("#opt2")), "+ New Resource")

    def test_named_list_with_no_options_falls_back_to_the_dialog(self):
        result = self.fill(screen_html(controls="lb-empty"))
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        # W-112: the box shows only the text the walk typed; nothing names the pick.
        self.assertEqual(result.note, 'Record Variable option varWidgetRec was clicked. The field shows the typed text; nothing on the page confirms the pick, so please confirm.')
        self.assertFalse(result.freeze)
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])
        self.assertEqual(self.page.locator("#rv").input_value(), "varWidgetRec")

    def test_no_matching_option_is_a_frozen_mismatch_with_safe_facts(self):
        result = self.fill(screen_html(option="varOtherRec"))
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertTrue(result.freeze)
        self.assertIn("No visible option in the Record Variable list has the first line varWidgetRec", result.note)
        self.assertIn("Diagnostic: scope aria-controls; 2 options, 2 visible; "
                      "options: other (1 line), other (1 line).", result.note)
        self.assertNotIn("varOtherRec", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(self.page.evaluate("window.escapes"), 0)

    def test_lookup_miss_is_a_real_failure_and_its_facts_are_in_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome, steps = self.walk(screen_html(option="varOtherRec"), STEP, snapshot_dir=Path(tmp))
            snap = json.loads((Path(tmp) / outcome.snapshots["s2"]).read_text())
        self.assertEqual(outcome.statuses["s1"], "PASS")
        self.assertEqual(outcome.statuses["s2"], "UI_MISMATCH", outcome.notes.get("s2"))
        self.assertEqual(outcome.statuses["s3"], "NOT_RUN")
        self.assertIn("Diagnostic: scope aria-controls; 2 options, 2 visible", snap["note"])
        result = SimpleNamespace(signed_in=True, message="", report_steps=report_steps(outcome, steps))
        self.assertEqual(outcome_of(result)[0], FAILED)
        self.assertEqual(gate_outcome_of(result), FAILED)

    def test_lookup_miss_behind_a_background_dialog_is_the_steps_own_mismatch(self):
        outcome, steps = self.walk(screen_html(option="varOtherRec", backdrop=True), STEP)
        self.assertEqual(outcome.statuses["s2"], "UI_MISMATCH", outcome.notes.get("s2"))
        self.assertNotIn("Sample Tips", outcome.notes["s2"])
        self.assertEqual(self.page.evaluate("window.escapes"), 0)
        self.assertEqual(self.page.locator("#rv").input_value(), "varWidgetRec")
        self.assertTrue(self.page.locator("#tips").is_visible())
        result = SimpleNamespace(signed_in=True, message="", report_steps=report_steps(outcome, steps))
        self.assertEqual(outcome_of(result)[0], FAILED)
        self.assertEqual(gate_outcome_of(result), FAILED)

    def test_dialog_outside_the_guides_modal_never_covers_the_page(self):
        outcome, steps = self.walk(screen_html(backdrop=True), "Click Sample Missing.")
        self.assertEqual(outcome.statuses["s1"], "PASS")
        self.assertEqual(outcome.statuses["s2"], "UI_MISMATCH", outcome.notes.get("s2"))
        self.assertIn("Could not find Sample Missing", outcome.notes["s2"])
        self.assertNotIn("covered the page", outcome.notes["s2"])
        self.assertEqual(self.page.evaluate("window.escapes"), 0)
        self.assertEqual(self.page.locator("#rv").input_value(), "varWidgetRec")
        result = SimpleNamespace(signed_in=True, message="", report_steps=report_steps(outcome, steps))
        self.assertEqual(outcome_of(result)[0], FAILED)
        self.assertEqual(gate_outcome_of(result), FAILED)

    def test_popup_that_cannot_take_focus_gets_no_escape(self):
        page = self.open(WELCOME_HTML)
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=1, page_poll_s=0.2)
        result = guide_walk.attempt_step(state, make_step("go", "Click Sample Go."))
        self.assertEqual(result.status, "ENV_BLOCKED", result.note)
        self.assertIn("Escape pressed no", result.note)
        self.assertEqual(page.evaluate("window.escapes"), 0)
        self.assertEqual(page.locator("#f").input_value(), "keep")
        self.assertTrue(page.locator("#welcome").is_visible())

    def test_popup_that_takes_focus_is_closed_with_escape_and_the_step_resumes(self):
        page = self.open(WELCOME_HTML.replace('aria-label="Sample Welcome"', 'aria-label="Sample Welcome" tabindex="-1"'))
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=1, page_poll_s=0.2)
        result = guide_walk.attempt_step(state, make_step("go", "Click Sample Go."))
        self.assertNotEqual(result.status, "ENV_BLOCKED", result.note)
        self.assertEqual(page.evaluate("window.goClicks"), 1)
        self.assertEqual(page.evaluate("window.escapes"), 1)
        self.assertFalse(page.locator("#welcome").is_visible())
        self.assertEqual(page.locator("#f").input_value(), "keep")
        self.assertIn('closed "Sample Welcome" pop-up', " ".join(state.popup_notes))


    def test_soft_wrap_never_splits_an_option_label(self):
        page = self.open(LABEL_CASES_HTML)
        for ident in ("mark-text", "mark-span"):
            with self.subTest(ident):
                # The fixture really wraps: the option is at least two mark-heights tall.
                self.assertTrue(page.locator(f"#{ident}").evaluate(
                    "el => el.getBoundingClientRect().height >= 1.8 * el.querySelector('mark').getBoundingClientRect().height"))
                self.assertEqual(guide_walk._option_lines(page.locator(f"#{ident}")), ["Account Name"])
                self.assertEqual(guide_walk._option_label(page.locator(f"#{ident}")), "Account Name")

    def test_assistive_text_class_is_skipped_without_the_slds_stylesheet(self):
        page = self.open('<div role="listbox"><div role="option" id="cls">'
                         '<span class="slds-assistive-text">Record</span>Account</div></div>')
        self.assertEqual(guide_walk._option_lines(page.locator("#cls")), ["Account"])

    def test_hard_boundaries_still_split_lines(self):
        page = self.open(LABEL_CASES_HTML)
        for ident in ("two-block", "br-line"):
            with self.subTest(ident):
                self.assertEqual(guide_walk._option_lines(page.locator(f"#{ident}")),
                                 ["Account", "Sample description line"])

    def test_clipped_and_hidden_screen_reader_text_is_skipped(self):
        page = self.open(LABEL_CASES_HTML)
        for ident in ("clip-rect", "clip-path", "hidden-part"):
            with self.subTest(ident):
                self.assertEqual(guide_walk._option_lines(page.locator(f"#{ident}")), ["Account"])

    def test_soft_wrapped_longer_option_is_never_clicked_for_its_first_word(self):
        for inner in ("<mark>varWidgetRec</mark> Copy", "<mark>varWidgetRec</mark> <span>Copy</span>"):
            with self.subTest(inner):
                result = self.fill(screen_html(option_html=inner, option_style=SOFT_WRAP))
                self.assertEqual(result.status, "UI_MISMATCH", result.note)
                self.assertTrue(result.freeze)
                self.assertIn("options: typed-prefix (1 line), other (1 line).", result.note)
                self.assertEqual(self.page.evaluate("window.chosen"), [])

    def test_two_line_block_option_is_still_picked_by_its_first_line(self):
        inner = ('<strong style="display:block">varWidgetRec</strong>'
                 '<span style="display:block">Sample record row</span>')
        result = self.fill(screen_html(option_html=inner))
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        # W-112: the box shows only the text the walk typed; nothing names the pick.
        self.assertEqual(result.note, 'Record Variable option varWidgetRec was clicked. The field shows the typed text; nothing on the page confirms the pick, so please confirm.')
        self.assertFalse(result.freeze)
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])
        self.assertEqual(self.page.locator("#rv").input_value(), "varWidgetRec")

    def test_empty_resource_type_list_is_not_replaced_by_another_list_in_the_dialog(self):
        page = self.open(RESOURCE_TYPE_EMPTY_HTML)
        root, _role, box = guide_walk._resource_type_box(page)
        self.assertIsNotNone(box)
        self.assertEqual(guide_walk._resource_listbox(root, box).get_attribute("id"), "rt-list")
        self.assertEqual(guide_walk._variable_options(page), [])

if __name__ == "__main__":
    unittest.main()
