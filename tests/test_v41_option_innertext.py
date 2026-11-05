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


def lookup_html(option_html, css=ASSISTIVE_CSS):
    """The guide's own Sample Screen dialog with a Record Variable lookup whose list is open."""
    return f"""{css}
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


# Put inside an option of lookup_html: a pick closes the lookup's list, as the real lookup does.
CLOSE_ON_PICK = """
<script>
  document.getElementById("lb").addEventListener("click", () => {
    document.getElementById("rv").setAttribute("aria-expanded", "false");
    document.getElementById("lb").hidden = true;
  });
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
    """The line rules on the browser's reply, with no browser. A part is (text, start): start is
    the count of non-space characters before it, as the browser reports it."""

    class _Option:
        def __init__(self, text, parts=(), total=None):
            squeeze = lambda value: len("".join(value.split()))  # noqa: E731
            self.reply = {
                "text": text,
                "total": squeeze(text) if total is None else total,
                "parts": [{"text": t, "start": start, "size": squeeze(t)} for t, start in parts],
            }

        def evaluate(self, script, arg=None):
            if script is guide_walk._VISIBLE_LINES_JS:
                return self.reply
            raise NotImplementedError(script)

    def lines(self, text, parts=(), total=None):
        return guide_walk._option_lines(self._Option(text, parts, total))

    def test_line_that_is_only_assistive_text_is_dropped(self):
        self.assertEqual(self.lines("Record\nvarSampleRec", [("Record", 0)]), ["varSampleRec"])

    def test_assistive_lines_after_other_text_are_dropped(self):
        self.assertEqual(self.lines("Sample Icon\nRecord\nvarSampleRec", [("Sample Icon", 0), ("Record", 10)]),
                         ["varSampleRec"])
        self.assertEqual(self.lines("Variable\nRecord", [("Record", 8)]), ["Variable"])

    def test_only_the_parts_own_line_is_dropped(self):
        self.assertEqual(self.lines("Contact\nAccount\nContact", [("Contact", 14)]), ["Contact", "Account"])

    def test_leading_inline_assistive_text_is_cut_from_the_label_line(self):
        self.assertEqual(self.lines("Record varSampleRec\nSample row", [("Record", 0)]),
                         ["varSampleRec", "Sample row"])

    def test_assistive_text_on_its_own_line_is_not_also_cut_from_the_label(self):
        self.assertEqual(self.lines("Record\nRecord Sample", [("Record", 0)]), ["Record Sample"])

    def test_trailing_inline_hidden_text_is_cut_from_the_last_line(self):
        # Fail-closed rule: a part that ends the option is trailing content and is removed.
        self.assertEqual(self.lines("Recording Record", [("Record", 9)]), ["Recording"])

    def test_places_that_do_not_match_inner_text_make_the_option_unreadable(self):
        self.assertEqual(self.lines("Record\nvarSampleRec", [("Record", 0)], total=99), [])
        self.assertEqual(self.lines("varSampleRec", [], total=99), [])
        self.assertEqual(self.lines("varSampleRec", []), ["varSampleRec"])

    def test_hidden_part_in_the_middle_makes_the_option_unreadable(self):
        self.assertEqual(self.lines("Contact Account", [("tact", 3)]), [])
        self.assertEqual(self.lines("Sample\nRecord\nvarSampleRec", [("Record", 6)]), [])

    def test_leading_part_that_does_not_line_up_makes_the_option_unreadable(self):
        self.assertEqual(self.lines("varSampleRec Record", [("Record", 0)]), [])

    def test_nested_or_multi_line_part_makes_the_option_unreadable(self):
        option = self._Option("Recordx\nvarSampleRec", [("Recordx", 0)])
        option.reply["parts"].append({"text": "x", "start": 6, "size": 1, "nested": True})
        self.assertEqual(guide_walk._option_lines(option), [])
        option = self._Option("Record\nx varSampleRec", [])
        option.reply["parts"] = [{"text": "Record\nx", "start": 0, "size": 7}]
        self.assertEqual(guide_walk._option_lines(option), [])

    def test_no_label_is_invented(self):
        self.assertEqual(self.lines("Record", [("Record", 0)]), [])
        self.assertEqual(guide_walk._option_label(self._Option("Record", [("Record", 0)])), "")
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

    def test_visible_icon_line_then_assistive_line_is_unreadable(self):
        # aria-hidden text is seen, so the assistive part sits in the middle: fail closed.
        inner = ('<span aria-hidden="true" style="display:block">Sample Icon</span>'
                 '<span class="slds-assistive-text">Record</span><strong>varSampleRec</strong>')
        page = self.open(lookup_html(inner))
        self.assertEqual(guide_walk._option_lines(page.locator("#opt1")), [])

    def pick(self, option_html, value, css=ASSISTIVE_CSS):
        """Fill the Record Variable lookup with VALUE in the guide's own Sample Screen dialog."""
        page = self.open(lookup_html(option_html, css))
        state = guide_walk.WalkState(page=page, timeout_s=3, interval_s=0.2, page_wait_s=1.5, page_poll_s=0.2)
        guide_walk._open_guide_dialog(state, "Sample Screen")
        return guide_walk.attempt_step(state, make_step("rv", f"Record Variable: {value}"))

    def test_unrendered_hidden_duplicate_never_removes_the_visible_label(self):
        inner = ('<span aria-hidden="true" style="display:none">Contact</span>'
                 '<span style="display:block">Contact</span><span style="display:block">Account</span>')
        result = self.pick(inner, "Account")
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(guide_walk._option_label(self.page.locator("#opt1")), "Contact")
        self.assertEqual(guide_walk._option_lines(self.page.locator("#opt1")), ["Contact", "Account"])

    def test_trailing_aria_hidden_duplicate_is_seen_and_kept(self):
        # aria-hidden text is visible to the eye: its line stays, and the label is still Contact.
        inner = ('<span style="display:block">Contact</span><span style="display:block">Account</span>'
                 '<span aria-hidden="true" style="display:block">Contact</span>')
        result = self.pick(inner, "Account")
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(guide_walk._option_lines(self.page.locator("#opt1")), ["Contact", "Account", "Contact"])

    def test_trailing_assistive_duplicate_removes_only_its_own_line(self):
        inner = ('<span style="display:block">Contact</span><span style="display:block">Account</span>'
                 '<span class="slds-assistive-text">Contact</span>')
        result = self.pick(inner, "Account")
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(guide_walk._option_lines(self.page.locator("#opt1")), ["Contact", "Account"])

    def test_unrendered_hidden_prefix_is_never_cut_from_the_label(self):
        page = self.open('<div role="listbox"><div role="option" id="o">'
                         '<span aria-hidden="true" style="display:none">Acc</span>Account</div></div>')
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), ["Account"])
        self.assertEqual(guide_walk._option_label(page.locator("#o")), "Account")

    def test_assistive_line_is_dropped_next_to_unrendered_hidden_text(self):
        page = self.open(ASSISTIVE_CSS + '<div role="listbox"><div role="option" id="o">'
                         '<span aria-hidden="true" style="display:none">Sample Icon</span>'
                         '<span class="slds-assistive-text">Record</span><strong>varSampleRec</strong></div></div>')
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), ["varSampleRec"])

    def test_trailing_inline_hidden_text_is_never_kept(self):
        page = self.open('<div role="listbox"><div role="option" id="o">'
                         'Recording <span class="slds-assistive-text">Record</span></div></div>')
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), ["Recording"])

    def assert_unreadable_and_not_clicked(self, inner, value, css=ASSISTIVE_CSS):
        result = self.pick(inner, value, css)
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertTrue(result.freeze)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(guide_walk._option_lines(self.page.locator("#opt1")), [])

    def test_hidden_part_when_totals_do_not_add_up_is_never_clicked(self):
        self.assert_unreadable_and_not_clicked(
            '<span class="slds-assistive-text">varSampleRec</span>'
            '<strong style="text-transform:uppercase">varOther&szlig;</strong>', "varSampleRec")
        # The fixture really shifts the count: the uppercase sharp s renders as two letters.
        self.assertIn("VAROTHERSS", self.page.locator("#opt1").evaluate("el => el.innerText"))

    def test_totals_that_do_not_add_up_fail_closed_without_any_hidden_part(self):
        self.assert_unreadable_and_not_clicked(
            '<strong style="text-transform:uppercase">varOther&szlig;</strong>', "VAROTHERSS")

    def test_hidden_part_with_a_line_break_is_never_clicked(self):
        self.assert_unreadable_and_not_clicked(
            '<span class="slds-assistive-text">varSampleRec<br>x</span>varOtherRec', "varSampleRec")

    def test_nested_hidden_part_is_never_clicked(self):
        self.assert_unreadable_and_not_clicked(
            '<span aria-hidden="true">varSampleRec<span class="slds-assistive-text">x</span></span>varOtherRec',
            "varSampleRec")

    def test_inline_nested_hidden_part_is_never_clicked(self):
        # No stylesheet, so no line break: only the nested part makes the option unreadable.
        self.assert_unreadable_and_not_clicked(
            '<span class="slds-assistive-text">varSampleRec<span class="slds-assistive-text">x</span></span>'
            'varOtherRec', "varOtherRec", css="")

    def test_aria_hidden_trailing_text_is_seen_and_never_clicked(self):
        result = self.pick('Account <span aria-hidden="true">Contact</span>', "Account")
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(guide_walk._option_label(self.page.locator("#opt1")), "Account Contact")

    def test_aria_hidden_leading_text_is_seen_and_never_clicked(self):
        result = self.pick('<span aria-hidden="true">Big</span> Account', "Account")
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), [])
        self.assertEqual(guide_walk._option_label(self.page.locator("#opt1")), "Big Account")

    def test_off_screen_text_is_never_clicked(self):
        self.assert_unreadable_and_not_clicked(
            '<span style="position:absolute; left:-9999px">Record</span><strong>varSampleRec</strong>', "Record")

    def test_shadow_root_text_is_never_clicked(self):
        self.assert_unreadable_and_not_clicked(
            '<span id="sh"></span>Contact<script>document.getElementById("sh")'
            '.attachShadow({mode: "open"}).innerHTML = "Account ";</script>', "Contact")
        self.assertEqual(self.page.locator("#opt1").evaluate("el => el.innerText").strip(), "Contact")

    def test_invisible_styles_make_the_option_unreadable(self):
        for style in ("opacity:0", "font-size:0", "color:transparent", "color:rgba(0, 0, 0, 0)"):
            with self.subTest(style):
                self.assert_unreadable_and_not_clicked(
                    f'<span style="{style}">Record</span>varSampleRec', "varSampleRec")

    def test_clip_path_assistive_text_is_removed_and_the_option_is_picked(self):
        inner = ('<span style="position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%)">'
                 'Record</span><strong>varSampleRec</strong>')
        page = self.open(lookup_html(inner))
        self.assertEqual(guide_walk._option_lines(page.locator("#opt1")), ["varSampleRec"])

    def test_hidden_part_in_the_middle_of_a_label_is_never_clicked(self):
        self.assert_unreadable_and_not_clicked('Con<span class="slds-assistive-text">tact</span> Account', "Con")

    def test_visible_aria_hidden_line_is_kept(self):
        page = self.open('<div role="listbox"><div role="option" id="o">'
                         '<span aria-hidden="true" style="display:block">Sample Icon</span>'
                         '<span style="display:block">Variable</span></div></div>')
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), ["Sample Icon", "Variable"])

    def test_nested_shadow_root_text_is_never_clicked(self):
        # The host's shadow holds a <span> (a <b> cannot host a shadow) whose own shadow root holds
        # text that innerText never shows.
        self.assert_unreadable_and_not_clicked(
            '<span id="sh"></span>Contact<script>'
            'const inner = document.getElementById("sh").attachShadow({mode: "open"});'
            'inner.innerHTML = "<span></span>";'
            'inner.querySelector("span").attachShadow({mode: "open"}).innerHTML = "Account ";'
            '</script>', "Contact")
        self.assertEqual(self.page.locator("#opt1").evaluate("el => el.innerText").strip(), "Contact")

    def test_slot_and_style_shadow_host_is_still_picked(self):
        inner = ('<span id="sh">varSampleRec</span><script>document.getElementById("sh")'
                 '.attachShadow({mode: "open"}).innerHTML = "<style>:host { color: navy; }</style><slot></slot>";'
                 '</script>' + CLOSE_ON_PICK)
        result = self.pick(inner, "varSampleRec")
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])

    def test_text_cut_away_by_an_overflow_clip_is_never_clicked(self):
        for style in ("height:0; overflow:hidden", "max-height:0; overflow:hidden", "height:0; overflow:clip"):
            with self.subTest(style):
                self.assert_unreadable_and_not_clicked(
                    f'<div style="{style}">Record</div><div>varSampleRec</div>', "Record")

    def test_text_moved_outside_the_option_box_is_never_clicked(self):
        self.assert_unreadable_and_not_clicked(
            '<div style="position:relative; left:-500px">Record</div><div>varSampleRec</div>', "Record")

    def test_text_outside_the_option_box_on_screen_is_never_clicked(self):
        # The option is narrow; the text sits on screen but wholly to the right of the option's box.
        css = ASSISTIVE_CSS + "<style>#opt1, #opt2 { width: 200px; }</style>"
        self.assert_unreadable_and_not_clicked(
            '<div style="position:relative; left:400px">Record</div><div>varSampleRec</div>', "Record", css)

    def test_text_outside_the_option_box_below_the_fold_is_never_clicked(self):
        page = self.open('<div style="height:3000px"></div><div role="listbox"><div role="option" id="o">'
                         '<div style="position:relative; left:-500px">Record</div><div>varSampleRec</div>'
                         '</div></div>')
        self.assertGreater(page.locator("#o").evaluate("el => el.getBoundingClientRect().top"),
                           page.evaluate("innerHeight"))
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), [])

    def test_below_the_fold_normal_option_is_read_and_picked(self):
        page = self.open('<div style="height:3000px"></div><div role="listbox"><div role="option" id="o">'
                         '<div>varSampleRec</div><div>Sample description</div></div></div>')
        self.assertGreater(page.locator("#o").evaluate("el => el.getBoundingClientRect().top"),
                           page.evaluate("innerHeight"))
        self.assertEqual(guide_walk._option_lines(page.locator("#o")), ["varSampleRec", "Sample description"])
        result = self.pick('<div>varSampleRec</div>' + CLOSE_ON_PICK, "varSampleRec",
                           css=ASSISTIVE_CSS + '<div style="height:3000px"></div>')
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])

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
