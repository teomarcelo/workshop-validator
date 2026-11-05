"""V42: a lookup pick is proven by a selected pill in the field's own container (P006N002).

Lightning can replace the lookup input with a pill (an icon with screen-reader text, the label,
and a remove button), and can re-render the field around it. The pill counts only when it is the
one visible pill in the container that holds the box and its label, its visible label read
fail-closed equals the value, and any title agrees with that visible label.
"""

import os
import unittest
from types import SimpleNamespace

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from workshop_validator.automate import SUCCESS, gate_outcome_of, outcome_of
from workshop_validator.guide_inventory import GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide
from workshop_validator.report import ReportStep

SMOKE = sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1"
SMOKE_REASON = "Set RUN_BROWSER_SMOKE=1 to run the real-browser checks."

BUILDER_URL = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
STEP = "Record Variable: varWidgetRec"

CSS = """
<style>
  .slds-assistive-text { position: absolute !important; margin: -1px !important; border: 0 !important;
    padding: 0 !important; width: 1px !important; height: 1px !important; overflow: hidden !important;
    clip: rect(0 0 0 0) !important; white-space: nowrap !important; }
</style>
"""


def pill(label="varWidgetRec", title=None, label_html=None, icon_title=None):
    """SLDS-style pill markup: record icon with screen-reader text, the label link, a remove button.

    TITLE is the link's title (default the label; False leaves it out). ICON_TITLE titles the icon."""
    title = label if title is None else title
    label_html = label if label_html is None else label_html
    titled = "" if title is False else f' title="{title}"'
    icon = "" if icon_title is None else f' title="{icon_title}"'
    return (f'<span class="slds-pill slds-pill_link"><span class="slds-pill__icon_container"{icon}>'
            '<span class="slds-assistive-text">Record</span><svg aria-hidden="true" width="12" height="12">'
            f'</svg></span><a class="slds-pill__action"{titled}><span class="slds-pill__label">'
            f'{label_html}</span></a><button class="slds-button slds-pill__remove" title="Remove">'
            '<svg aria-hidden="true" width="10" height="10"></svg><span class="slds-assistive-text">Remove'
            '</span></button></span>')


def screen_html(shown=None, home="rv", form_element=True, compound=False, sibling=True,
                other_label="Sample Other Field", other_input=True, other_pill="", other_extra="", required=False):
    """The guide's own Sample Screen dialog, Fields section, with a Record Variable lookup host.

    Clicking the option re-renders the Record Variable host with SHOWN (pill markup, default one
    pill labelled varWidgetRec) in place of the input. With HOME "ot" SHOWN goes into the other
    field's host instead (HOME "none": nowhere); with HOME "new" a new field labelled Sample Other Field holding SHOWN
    is added next to it. FORM_ELEMENT wraps each field in an .slds-form-element inside its host.
    COMPOUND puts the fields in one .slds-form-element_compound wrapper, and the pick then
    replaces the whole Record Variable host. SIBLING adds the other field: with OTHER_LABEL (None
    for no label), OTHER_INPUT, OTHER_PILL already shown before the pick, and OTHER_EXTRA markup.
    REQUIRED puts an SLDS required marker (*) before every field label: aria-hidden before the
    pick (so the lookup is still found by its name, as on the real page), plain after it. A
    string REQUIRED is the marker markup shown after the pick instead.
    """
    shown = pill() if shown is None else shown
    mark = '<abbr class="slds-required" title="required" aria-hidden="true">*</abbr>' if required else ""
    mark_after = '<abbr class="slds-required" title="required">*</abbr>' if required is True else (required or "")

    def field(key, name, inner):
        body = ((f'<label class="slds-form-element__label" for="{key}">{mark}{name}</label>' if name else "")
                + f'<div class="slds-form-element__control">{inner}</div>')
        if form_element:
            body = f'<div class="slds-form-element">{body}</div>'
        return f'<sample-grouped-combobox id="host-{key}">{body}</sample-grouped-combobox>'

    record = field("rv", "Record Variable", """
      <div class="slds-combobox_container">
        <input id="rv" role="combobox" aria-expanded="true" aria-controls="lb" value="varWidgetRec"
               autocomplete="off">
      </div>
      <div id="lb" role="listbox">
        <ul role="group" aria-labelledby="g1">
          <li role="presentation" id="g1"><span>Record Variables</span></li>
          <li role="option" id="opt1"><span class="slds-assistive-text">Record</span>
            <svg aria-hidden="true" width="12" height="12"></svg><strong>varWidgetRec</strong></li>
        </ul>
      </div>""")
    other_box = '<div class="slds-combobox_container"><input id="ot" autocomplete="off"></div>'
    other = field("ot", other_label, (other_box if other_input else "") + other_pill + other_extra) if sibling else ""
    opening = '<div class="slds-form-element_compound">' if compound else ""
    closing = "</div>" if compound else ""
    return f"""{CSS}
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    {opening}{record}
    {other}{closing}
  </section>
</div>
<script>
  window.chosen = [];
  const list = document.getElementById("lb");
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    const host = document.getElementById("host-rv");
    const parent = host.parentElement;
    const home = {home!r};
    const formed = {str(bool(form_element)).lower()};
    const wrap = (name, inner) => {{
      const body = '<label class="slds-form-element__label">' + {mark_after!r} + name + '</label>' +
        '<div class="slds-form-element__control">' + inner + '</div>';
      return formed ? '<div class="slds-form-element">' + body + '</div>' : body;
    }};
    if (home === "rv") {{
      host.innerHTML = wrap("Record Variable", {shown!r});
      return;
    }}
    if ({str(bool(compound)).lower()}) host.outerHTML = '<sample-grouped-combobox id="host-rv">' +
      wrap("Record Variable", "") + "</sample-grouped-combobox>";
    else host.innerHTML = wrap("Record Variable", "");
    if (home === "new") {{
      parent.insertAdjacentHTML("beforeend", '<sample-grouped-combobox id="host-new">' +
        wrap("Sample Other Field", {shown!r}) + "</sample-grouped-combobox>");
    }} else if (home === "ot") {{
      document.querySelector("#host-ot .slds-form-element__control").insertAdjacentHTML("beforeend", {shown!r});
    }}
  }});
  document.getElementById("rv").focus();
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


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class LookupPillBrowserTests(unittest.TestCase):
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
        # An earlier guide step opened Sample Screen; it is the guide's own dialog.
        guide_walk._open_guide_dialog(state, "Sample Screen")
        return guide_walk.attempt_step(state, make_step("rv", STEP))

    def assert_not_pass(self, result):
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])

    def test_pill_in_the_rerendered_field_is_pass_evidence_naming_the_pill(self):
        result = self.fill(screen_html())
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.evidence_ids, ("rv:pill",))
        self.assertIn("one selected pill labelled varWidgetRec", result.note)
        self.assertEqual(self.page.evaluate("window.chosen"), ["opt1"])
        self.assertEqual(self.page.locator("#host-rv .slds-pill__label").inner_text(), "varWidgetRec")

    def test_pill_in_a_host_with_no_form_element_is_pass_evidence(self):
        result = self.fill(screen_html(form_element=False))
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.evidence_ids, ("rv:pill",))

    def test_pill_with_another_label_is_a_mismatch(self):
        result = self.fill(screen_html(shown=pill("varOtherRec")))
        self.assertEqual(result.status, "UI_MISMATCH", result.note)
        self.assertIn("Record Variable did not show varWidgetRec", result.note)
        self.assert_not_pass(result)

    def test_two_pills_are_not_pass(self):
        result = self.fill(screen_html(shown=pill() + pill()))
        self.assert_not_pass(result)

    def test_pill_in_another_fields_container_is_not_pass(self):
        result = self.fill(screen_html(home="ot"))
        self.assert_not_pass(result)
        self.assertEqual(self.page.locator("#host-ot .slds-pill__label").inner_text(), "varWidgetRec")

    def test_pill_in_another_field_of_a_shared_wrapper_is_not_pass(self):
        # The Record Variable host is replaced; the wrapper that holds both fields is never its container.
        cases = (("Sample Other Field", True), ("Sample Other Field", False), (None, True))
        for other_label, other_input in cases:
            with self.subTest(other_label=other_label, other_input=other_input):
                result = self.fill(screen_html(home="ot", compound=True, other_label=other_label,
                                               other_input=other_input))
                self.assert_not_pass(result)
                self.assertEqual(self.page.locator("#host-ot .slds-pill__label").inner_text(), "varWidgetRec")
                self.assertEqual(self.page.locator("#rv").count(), 0)

    def test_pill_another_field_already_showed_in_a_shared_wrapper_is_not_pass(self):
        # The other lookup already shows the value as a pill, with no input; the pick shows nothing here.
        result = self.fill(screen_html(home="none", compound=True, other_input=False, other_pill=pill()))
        self.assert_not_pass(result)
        self.assertEqual(self.page.locator("#host-ot .slds-pill__label").inner_text(), "varWidgetRec")

    def test_unlabelled_pill_already_in_a_shared_wrapper_is_not_pass(self):
        # A pill shown before the pick is never this pick's evidence, even with no other label nearby.
        result = self.fill(screen_html(home="none", compound=True, other_label=None, other_input=False,
                                       other_pill=pill()))
        self.assert_not_pass(result)

    def test_pill_in_a_same_labelled_other_field_of_a_shared_wrapper_is_not_pass(self):
        # Another field with the same label text: the wrapper that holds its label is never this field.
        result = self.fill(screen_html(home="ot", compound=True, other_label="Record Variable",
                                       other_input=False))
        self.assert_not_pass(result)

    def test_pill_in_a_wrapper_with_a_control_labelled_by_another_element_is_not_pass(self):
        # The other field has no label element: its combobox is labelled by a span (aria-labelledby).
        named = ('<span id="ot-name">Sample Other Field</span>'
                 '<div role="combobox" aria-labelledby="ot-name" aria-expanded="false"></div>')
        result = self.fill(screen_html(home="ot", compound=True, other_label=None, other_input=False,
                                       other_extra=named))
        self.assert_not_pass(result)
        self.assertEqual(self.page.locator("#host-ot .slds-pill__label").inner_text(), "varWidgetRec")

    def test_pill_in_a_field_rendered_after_the_pick_is_not_pass(self):
        # A new field appears in the wrapper with the pill; that pill's own field is not Record Variable.
        result = self.fill(screen_html(home="new", compound=True, sibling=False))
        self.assert_not_pass(result)
        self.assertEqual(self.page.locator("#host-new .slds-pill__label").inner_text(), "varWidgetRec")

    def test_required_marker_is_not_part_of_the_field_name(self):
        result = self.fill(screen_html(required=True))
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.evidence_ids, ("rv:pill",))
        self.assertEqual(self.page.locator("#host-rv abbr.slds-required").count(), 1)
        self.assertIsNone(self.page.locator("#host-rv abbr.slds-required").get_attribute("aria-hidden"))

    def test_required_assistive_text_is_not_part_of_the_field_name(self):
        result = self.fill(screen_html(required='<span class="slds-assistive-text">Required</span>'))
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.evidence_ids, ("rv:pill",))

    def test_required_class_with_other_text_stays_in_the_field_name(self):
        # Only a "*" marker is dropped; other text in a required-styled element is part of the name.
        result = self.fill(screen_html(required='<span class="slds-required">Sample </span>'))
        self.assert_not_pass(result)

    def test_pill_in_another_required_field_is_not_pass(self):
        result = self.fill(screen_html(home="new", compound=True, sibling=False, required=True))
        self.assert_not_pass(result)
        self.assertEqual(self.page.locator("#host-new abbr.slds-required").count(), 1)

    def test_icon_title_is_not_compared_with_the_label(self):
        result = self.fill(screen_html(shown=pill(icon_title="Record")))
        self.assertEqual(result.status, "PASS", result.note)
        self.assertEqual(result.evidence_ids, ("rv:pill",))

    def test_title_alone_never_stands_in_for_the_visible_label(self):
        result = self.fill(screen_html(shown=pill("varOtherRec", title="varWidgetRec")))
        self.assert_not_pass(result)

    def test_title_that_differs_from_the_visible_label_is_not_pass(self):
        result = self.fill(screen_html(shown=pill("varWidgetRec", title="varOtherRec")))
        self.assert_not_pass(result)

    def test_pill_label_with_unreadable_text_is_not_pass(self):
        unclear = 'varWidgetRec<br><span style="opacity:0">Sample hidden line</span>'
        result = self.fill(screen_html(shown=pill(title=False, label_html=unclear)))
        self.assert_not_pass(result)

    def test_walk_records_the_pill_pick_as_pass_through_automate(self):
        page = self.open(screen_html())
        steps = (make_step("s1", "Add a Sample Screen."), make_step("s2", STEP))
        checks = (GuideCheck("opened", lambda item: item.step_id == "s1", opened_screen),)
        outcome = walk_guide(page, SimpleNamespace(steps=steps), checks=checks, timeout_s=3, interval_s=0.2,
                             page_wait_s=1.5, page_poll_s=0.2)
        self.assertEqual(outcome.statuses["s2"], "PASS", outcome.notes.get("s2"))
        self.assertEqual(tuple(outcome.evidence.get("s2", ())), ("s2:pill",))
        result = SimpleNamespace(signed_in=True, message="", report_steps=report_steps(outcome, steps))
        self.assertEqual(outcome_of(result), (SUCCESS, ""))
        self.assertEqual(gate_outcome_of(result), SUCCESS)


if __name__ == "__main__":
    unittest.main()
