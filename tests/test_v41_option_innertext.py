"""V41: an option's label is the first line of the browser's own innerText (P005N008).

Lines that are only screen-reader or hidden text are dropped, and such a text sitting inline
at the start of the label line is cut off. Text inside a custom element whose childNodes are
patched (as Lightning's synthetic shadow does) is still read. On a miss the failure snapshot
holds the options' structure with every text replaced by its length."""

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
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
SMOKE_REASON = "Set RUN_BROWSER_SMOKE=1 to run the real-browser checks."
BUILDER_URL = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"

ASSISTIVE_CSS = """
<style>
  .slds-assistive-text { position: absolute !important; margin: -1px !important; border: 0 !important;
    padding: 0 !important; width: 1px !important; height: 1px !important; overflow: hidden !important;
    clip: rect(0 0 0 0) !important; white-space: nowrap !important; }
</style>
"""

# A Lightning-style custom element: its text is rendered through an open shadow root's slot,
# and childNodes is patched to hide its real children, as synthetic shadow does.
CUSTOM_ELEMENT_JS = """
<script>
  customElements.define("sample-formatted-text", class extends HTMLElement {
    connectedCallback() {
      if (this.ready) return;
      this.ready = true;
      this.attachShadow({ mode: "open" }).innerHTML = "<slot></slot>";
      const text = document.createElement("span");
      text.textContent = this.getAttribute("value");
      this.appendChild(text);
      const none = document.createDocumentFragment().childNodes;
      Object.defineProperty(this, "childNodes", { get() { return none; } });
    }
  });
</script>
"""


def slds_option(label, description="Sample description"):
    return ('<span class="slds-media__body">'
            '<span class="slds-listbox__option-text slds-listbox__option-text_entity" '
            f'style="display:block">{label}</span>'
            f'<span class="slds-listbox__option-meta" style="display:block">{description}</span></span>')


def custom_option(label, description="Sample description"):
    return slds_option(f'<sample-formatted-text value="{label}"></sample-formatted-text>', description)


def new_resource_html(options):
    """A New Resource dialog whose Resource Type list holds OPTIONS: (label, inner HTML) pairs."""
    items = "".join(
        f'<div role="option" id="nr-{i}" data-label="{label}">{inner}</div>'
        for i, (label, inner) in enumerate(options)
    )
    return f"""{ASSISTIVE_CSS}{CUSTOM_ELEMENT_JS}
<div role="dialog" aria-label="New Resource">
  <span id="nr-lbl">Resource Type</span>
  <button id="nr-box" role="combobox" aria-labelledby="nr-lbl" aria-expanded="false"
          aria-controls="nr-list">Select...</button>
  <div id="nr-list" role="listbox" hidden>{items}</div>
  <div id="nr-fields"></div>
</div>
<script>
  window.chosen = [];
  const box = document.getElementById("nr-box");
  const list = document.getElementById("nr-list");
  box.addEventListener("click", () => {{
    list.hidden = false;
    box.setAttribute("aria-expanded", "true");
  }});
  list.addEventListener("click", (event) => {{
    const option = event.target.closest("[role=option]");
    if (!option) return;
    const label = option.dataset.label;
    window.chosen.push(label);
    box.textContent = label;
    box.setAttribute("aria-expanded", "false");
    list.hidden = true;
    if (label === "Variable") {{
      document.getElementById("nr-fields").innerHTML = '<label for="nr-api">API Name</label><input id="nr-api">';
    }}
  }});
</script>
"""


def lookup_html(option_html):
    """The guide's own Sample Screen dialog with a Record Variable lookup whose list is open."""
    return f"""{ASSISTIVE_CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <label for="rv">Record Variable</label>
  <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="varSampleRec" autocomplete="off">
  <div id="lb" role="listbox">
    <div role="option" id="opt1">{option_html}</div>
    <div role="option" id="opt2"><span>+ New Resource</span></div>
  </div>
</div>
<script>
  window.chosen = [];
  document.getElementById("lb").addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (picked) window.chosen.push(picked.id);
  }});
</script>
"""


def make_step(step_id, text):
    return GuideStep(step_id, 1, "Exercise 9: Sample", "Task 1: Sample.", "1", text,
                     (90.0, 10.0, 400.0, 24.0), None, ())


def texts_in(shape):
    """Every string value anywhere in a shape (keys excluded)."""
    if isinstance(shape, dict):
        return [text for value in shape.values() for text in texts_in(value)]
    if isinstance(shape, list):
        return [text for item in shape for text in texts_in(item)]
    return [shape] if isinstance(shape, str) else []


def lengths_in(shape):
    if isinstance(shape, dict):
        own = [shape["text"]] if "text" in shape else []
        return own + [n for value in shape.values() for n in lengths_in(value)]
    if isinstance(shape, list):
        return [n for item in shape for n in lengths_in(item)]
    return []


class OptionLinesTests(unittest.TestCase):
    """The line rules on the browser's reply, with no browser."""

    class _Option:
        def __init__(self, text, parts=()):
            self.reply = {"text": text, "parts": [{"text": t, "leading": lead} for t, lead in parts]}

        def evaluate(self, script, arg=None):
            if script is guide_walk._VISIBLE_LINES_JS:
                return self.reply
            raise NotImplementedError(script)

    def lines(self, text, parts=()):
        return guide_walk._option_lines(self._Option(text, parts))

    def test_line_that_is_only_assistive_text_is_dropped(self):
        self.assertEqual(self.lines("Record\nvarSampleRec", [("Record", True)]), ["varSampleRec"])

    def test_assistive_lines_that_are_not_leading_are_dropped(self):
        self.assertEqual(self.lines("Sample Icon\nRecord\nvarSampleRec", [("Sample Icon", True), ("Record", False)]),
                         ["varSampleRec"])
        self.assertEqual(self.lines("Variable\nRecord", [("Record", False)]), ["Variable"])

    def test_leading_inline_assistive_text_is_cut_from_the_label_line(self):
        self.assertEqual(self.lines("Record varSampleRec\nSample row", [("Record", True)]),
                         ["varSampleRec", "Sample row"])

    def test_assistive_text_on_its_own_line_is_not_also_cut_from_the_label(self):
        self.assertEqual(self.lines("Record\nRecord Sample", [("Record", True)]), ["Record Sample"])

    def test_text_that_is_not_leading_is_never_cut(self):
        self.assertEqual(self.lines("Recording Record", [("Record", False)]), ["Recording Record"])

    def test_no_label_is_invented(self):
        self.assertEqual(self.lines("Record", [("Record", True)]), [])
        self.assertEqual(guide_walk._option_label(self._Option("Record", [("Record", True)])), "")
        self.assertEqual(guide_walk._option_label(self._Option("\n\nVariable\nSample")), "Variable")

    def test_unexpected_reply_has_no_lines(self):
        reply = SimpleNamespace(evaluate=lambda script, arg=None: ["Variable"])
        self.assertEqual(guide_walk._option_lines(reply), [])


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class OptionInnerTextBrowserTests(unittest.TestCase):
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
        page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
        page.goto(BUILDER_URL)
        page.wait_for_timeout(200)
        self.page = page
        return page

    def choose(self, options, page_wait_s=3):
        page = self.open(new_resource_html(options))
        state = guide_walk.WalkState(page=page, timeout_s=6, interval_s=0.2, page_wait_s=page_wait_s,
                                     page_poll_s=0.2)
        return guide_walk._choose_resource_variable(state)

    def show_list(self):
        """The pick closed the list; a closed list has no layout, so show it again to read lines."""
        self.page.evaluate("document.getElementById('nr-list').hidden = false")

    def test_slds_two_line_variable_option_is_picked(self):
        result = self.choose([("Variable", slds_option("Variable")), ("Constant", slds_option("Constant"))])
        self.assertIsNone(result, result and result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), ["Variable"])
        self.show_list()
        self.assertEqual(guide_walk._option_lines(self.page.locator("#nr-0")), ["Variable", "Sample description"])

    def test_label_inside_a_custom_element_is_read_and_picked(self):
        result = self.choose([("Variable", custom_option("Variable")), ("Constant", custom_option("Constant"))])
        self.assertIsNone(result, result and result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), ["Variable"])
        self.show_list()
        self.assertEqual(guide_walk._option_lines(self.page.locator("#nr-1")), ["Constant", "Sample description"])

    def test_inline_assistive_text_before_the_label_is_cut(self):
        page = self.open('<div role="listbox"><div role="option" id="o">'
                         '<span class="slds-assistive-text">Record</span> varSampleRec</div></div>')
        self.assertEqual(page.locator("#o").evaluate("el => el.innerText").strip(), "Record varSampleRec")
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), ["varSampleRec"])

    def test_assistive_line_is_dropped_and_the_label_keeps_its_own_words(self):
        page = self.open(lookup_html('<span class="slds-assistive-text">Record</span><strong>Record Sample</strong>'))
        self.assertEqual(guide_walk._option_lines(page.locator("#opt1")), ["Record Sample"])

    def test_icon_line_then_assistive_line_are_both_dropped_and_the_option_is_picked(self):
        inner = ('<span aria-hidden="true" style="display:block">Sample Icon</span>'
                 '<span class="slds-assistive-text">Record</span><strong>varSampleRec</strong>')
        page = self.open(lookup_html(inner))
        self.assertEqual(guide_walk._option_lines(page.locator("#opt1")), ["varSampleRec"])

    def test_visible_aria_hidden_line_is_dropped(self):
        page = self.open('<div role="listbox"><div role="option" id="o">'
                         '<span aria-hidden="true" style="display:block">Sample Icon</span>'
                         '<span style="display:block">Variable</span></div></div>')
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), ["Variable"])

    def test_resource_type_miss_saves_the_option_structure_with_no_text(self):
        def pick(state, item):
            missed = guide_walk._choose_resource_variable(state)
            return missed or CheckResult("PASS", "Variable chosen.", (f"{item.step_id}:chosen",))

        options = [("Constant", custom_option("Constant")), ("Formula", slds_option("Formula"))]
        page = self.open(new_resource_html(options))
        steps = (make_step("s1", "Set Resource Type to Variable."),)
        checks = (GuideCheck("pick", lambda item: item.step_id == "s1", pick),)
        with tempfile.TemporaryDirectory() as tmp:
            outcome = walk_guide(page, SimpleNamespace(steps=steps), checks=checks, timeout_s=3,
                                 interval_s=0.2, page_wait_s=1, page_poll_s=0.2, snapshot_dir=Path(tmp))
            snap = json.loads((Path(tmp) / outcome.snapshots["s1"]).read_text())
        self.assertEqual(outcome.statuses["s1"], "UI_MISMATCH", outcome.notes.get("s1"))
        shapes = snap["option_shapes"]
        self.assertEqual(len(shapes), 2)
        self.assertEqual(shapes[0]["role"], "option")
        self.assertIn("sample-formatted-text", json.dumps(shapes))
        self.assertIn("slds-listbox__option-meta", json.dumps(shapes))
        self.assertIn(len("Constant"), lengths_in(shapes[0]))
        self.assertIn(len("Sample description"), lengths_in(shapes[0]))
        dumped = json.dumps(shapes)
        for word in ("Constant", "Formula", "Sample description"):
            self.assertNotIn(word, dumped)
        for text in texts_in(shapes):
            self.assertNotIn(text, ("Constant", "Formula", "Sample description"))
        self.assertEqual(page.evaluate("window.chosen"), [])

    def test_lookup_miss_saves_the_option_structure_with_no_text(self):
        def opened(state, item):
            guide_walk._open_guide_dialog(state, "Sample Screen")
            return CheckResult("PASS", "Sample Screen is open.", (f"{item.step_id}:opened",))

        page = self.open(lookup_html('<span class="slds-assistive-text">Record</span><strong>varOtherRec</strong>'))
        steps = (make_step("s1", "Add a Sample Screen."), make_step("s2", "Record Variable: varSampleRec"))
        checks = (GuideCheck("opened", lambda item: item.step_id == "s1", opened),)
        with tempfile.TemporaryDirectory() as tmp:
            outcome = walk_guide(page, SimpleNamespace(steps=steps), checks=checks, timeout_s=3,
                                 interval_s=0.2, page_wait_s=1.5, page_poll_s=0.2, snapshot_dir=Path(tmp))
            snap = json.loads((Path(tmp) / outcome.snapshots["s2"]).read_text())
        self.assertEqual(outcome.statuses["s2"], "UI_MISMATCH", outcome.notes.get("s2"))
        shapes = snap["option_shapes"]
        self.assertEqual([shape["role"] for shape in shapes], ["option", "option"])
        self.assertIn("slds-assistive-text", json.dumps(shapes))
        self.assertIn(len("varOtherRec"), lengths_in(shapes[0]))
        self.assertNotIn("varOtherRec", json.dumps(shapes))
        self.assertNotIn("New Resource", json.dumps(shapes))
        self.assertEqual(page.evaluate("window.chosen"), [])


if __name__ == "__main__":
    unittest.main()
