"""V33: New Resource sets Resource Type to Variable for real, read from the page, or stops
with a safe diagnostic. Enter is pressed only on an open combobox whose highlighted option is Variable."""

import os
import re
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator import guide_walk
from tests.test_guide_walk import Clock, Loc, Page, as_builder, step

BUILDER_URL = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
UNSET = "Resource Type could not be set to Variable."


def variable_step():
    return step("var", "Exercise 9: Sample", "Task 1: Sample.", "3",
                "Create a record variable to hold sample data.")


class _BoxLoc:
    """The Resource Type combobox: its shown value, its ARIA states and the keys it receives."""

    def __init__(self, page):
        self.page = page

    def count(self):
        return 1 if self.page.box else 0

    @property
    def first(self):
        return self

    def nth(self, index):
        return self

    def is_visible(self):
        return self.count() > 0

    def get_attribute(self, name, timeout=None):
        return self.page.box_attrs.get(name)

    def input_value(self, timeout=None):
        raise RuntimeError("Not an input element")

    def inner_text(self, timeout=None):
        return self.page.box_value

    def click(self, timeout=None):
        self.page.events.append(("click", "combobox", "Resource Type"))
        self.page.box_attrs["aria-expanded"] = "true"

    def press(self, key, timeout=None):
        self.page.keys.append(key)
        if key == "Enter" and self.page.on_enter is not None:
            self.page.on_enter()


class _TextsLoc:
    def __init__(self, texts):
        self.texts = list(texts)

    def count(self):
        return len(self.texts)

    @property
    def first(self):
        return self

    def inner_text(self, timeout=None):
        return self.texts[0]


class _OptionsLoc:
    def __init__(self, page):
        self.page = page

    def count(self):
        return len(self.page.options)

    def nth(self, index):
        return Loc(self.page, "option", self.page.options[index])


class ResourcePage(Page):
    """A synthetic Flow Builder with New Resource open on a Resource Type combobox."""

    def __init__(self, options=("Variable", "Constant", "Formula")):
        super().__init__(BUILDER_URL)
        as_builder(self)
        self.roles.add(("button", "New Resource"))
        self.box = True
        self.box_value = "Select an Option"
        self.box_attrs = {"aria-expanded": "false"}
        self.ids = {}
        self.options = list(options)
        for name in self.options:
            self.roles.add(("option", name))
        self.on_enter = None

    def select_variable(self, api_name=True):
        self.box_value = "Variable"
        self.box_attrs["aria-expanded"] = "false"
        if api_name:
            self.roles.add(("textbox", "API Name"))

    def highlight(self, text, copies=1):
        self.box_attrs["aria-activedescendant"] = "sample-option-2"
        self.ids["sample-option-2"] = [text] * copies

    def get_by_role(self, role, name=None, exact=True):
        if role == "combobox" and name == "Resource Type":
            return _BoxLoc(self)
        if role == "option" and not name:
            return _OptionsLoc(self)
        return super().get_by_role(role, name, exact)

    def locator(self, selector):
        match = re.fullmatch(r'\[id="(.*)"\]', selector)
        if match:
            return _TextsLoc(self.ids.get(match.group(1), []))
        return super().locator(selector)


def state_for(page, page_wait_s=0, on_sleep=None):
    clock = Clock()

    def sleep(seconds):
        clock.sleep(seconds)
        if on_sleep is not None:
            on_sleep(clock.now)

    return guide_walk.WalkState(
        page=page, timeout_s=0, interval_s=1, clock=clock, sleeper=sleep,
        page_wait_s=page_wait_s, page_poll_s=1,
    )


def clicks(page):
    return [event[1:] for event in page.events if event[0] == "click"]


class ResourceTypeTests(unittest.TestCase):
    def on_option(self, page, react):
        page.on_click = lambda kind, name: react() if (kind, name) == ("option", "Variable") else None

    def test_option_click_that_selects_continues_to_api_name(self):
        page = ResourcePage()
        self.on_option(page, page.select_variable)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.evidence_ids, ("var:new-resource",))
        self.assertIn(("option", "Variable"), clicks(page))
        self.assertEqual(page.keys, [])

    def test_shown_value_variable_counts_before_api_name_appears(self):
        page = ResourcePage()
        self.on_option(page, lambda: page.select_variable(api_name=False))

        def late_api_name(now):
            if now >= 15:
                page.roles.add(("textbox", "API Name"))

        result = guide_walk.run_record_variable(
            state_for(page, page_wait_s=30, on_sleep=late_api_name), variable_step()
        )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(page.keys, [])

    def test_click_does_nothing_and_highlighted_variable_gets_one_enter(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("  Variable \n")
        page.on_enter = page.select_variable
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(page.keys, ["Enter"])
        self.assertIn(("option", "Variable"), clicks(page))

    def test_enter_that_does_not_select_is_a_mismatch_after_one_press(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Variable")
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertTrue(result.note.startswith(UNSET + " Diagnostic:"))
        self.assertEqual(page.keys, ["Enter"])

    def test_highlighted_constant_gets_no_enter_and_reports_the_diagnostic(self):
        page = ResourcePage(options=("Variable", "Constant", "Sample Other"))
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Constant")
        page.on_enter = page.select_variable
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertEqual(result.evidence_ids, ())
        self.assertEqual(
            result.note,
            UNSET + " Diagnostic: control role combobox; aria-expanded true; "
            "option Variable present yes, visible yes; 3 options; labels: Variable, Constant, other.",
        )
        self.assertEqual(page.keys, [])

    def test_highlighted_variable_on_a_closed_combobox_gets_no_enter(self):
        page = ResourcePage()
        page.highlight("Variable")
        page.on_enter = page.select_variable
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("aria-expanded false", result.note)
        self.assertEqual(page.keys, [])

    def test_highlight_id_that_matches_two_elements_gets_no_enter(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Variable", copies=2)
        page.on_enter = page.select_variable
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertEqual(page.keys, [])

    def test_nothing_selectable_is_a_mismatch_with_the_diagnostic(self):
        page = ResourcePage(options=())
        page.box = False
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertEqual(
            result.note,
            UNSET + " Diagnostic: control role none; aria-expanded none; "
            "option Variable present no, visible no; 0 options; labels: none.",
        )
        self.assertEqual(page.keys, [])

    def test_diagnostic_keeps_at_most_six_labels(self):
        names = ("Variable", "Constant", "Formula", "Text Template", "Stage", "Choice", "Record Choice Set")
        page = ResourcePage(options=names)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertIn("7 options; labels: Variable, Constant, Formula, Text Template, Stage, Choice.",
                      result.note)


COMBOBOX_HTML = """
<span id="lbl">Resource Type</span>
<button id="box" role="combobox" aria-labelledby="lbl" aria-expanded="false"
        aria-controls="list">Select an Option</button>
<div id="list" role="listbox" hidden>
  <div role="option" id="opt-variable">Variable</div>
  <div role="option" id="opt-constant">Constant</div>
</div>
<div id="fields"></div>
<script>
  const box = document.getElementById("box");
  const list = document.getElementById("list");
  window.clickSelects = false;
  window.enters = 0;
  function choose(option) {
    box.textContent = option.textContent;
    box.setAttribute("aria-expanded", "false");
    list.hidden = true;
    if (option.textContent === "Variable") {
      document.getElementById("fields").innerHTML = '<label for="api">API Name</label><input id="api">';
    }
  }
  box.addEventListener("click", () => {
    list.hidden = false;
    box.setAttribute("aria-expanded", "true");
    box.setAttribute("aria-activedescendant", window.highlight);
  });
  list.addEventListener("click", (event) => {
    if (window.clickSelects) choose(event.target);
  });
  box.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    window.enters += 1;
    choose(document.getElementById(box.getAttribute("aria-activedescendant")));
  });
</script>
"""


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Set RUN_BROWSER_SMOKE=1 to run the real-browser Resource Type checks.",
)
class ResourceTypeBrowserTests(unittest.TestCase):
    """A real combobox whose option click does nothing; only Enter on the highlighted option selects."""

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

    def choose(self, highlight, click_selects=False):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content(COMBOBOX_HTML)
        page.evaluate(f"window.highlight = {highlight!r}; window.clickSelects = {str(click_selects).lower()}")
        state = guide_walk.WalkState(page=page, timeout_s=2, interval_s=0.2, page_wait_s=1, page_poll_s=0.2)
        result = guide_walk._choose_resource_variable(state)
        return result, page.evaluate("window.enters"), guide_walk._api_name_ready(page)

    def test_option_click_that_selects_needs_no_enter(self):
        result, enters, ready = self.choose("opt-variable", click_selects=True)
        self.assertIsNone(result)
        self.assertEqual(enters, 0)
        self.assertTrue(ready)

    def test_dead_click_with_highlighted_variable_is_selected_by_enter(self):
        result, enters, ready = self.choose("opt-variable")
        self.assertIsNone(result)
        self.assertEqual(enters, 1)
        self.assertTrue(ready)

    def test_highlighted_constant_gets_no_enter_and_stops(self):
        result, enters, ready = self.choose("opt-constant")
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertEqual(
            result.note,
            UNSET + " Diagnostic: control role combobox; aria-expanded true; "
            "option Variable present yes, visible yes; 2 options; labels: Variable, Constant.",
        )
        self.assertEqual(enters, 0)
        self.assertFalse(ready)


if __name__ == "__main__":
    unittest.main()
