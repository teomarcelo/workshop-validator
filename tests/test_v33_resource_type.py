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
        if getattr(self.page, "box_input", None) is None:
            raise RuntimeError("Not an input element")
        return self.page.box_input

    def inner_text(self, timeout=None):
        if getattr(self.page, "box_input", None) is not None:
            return ""  # an input has no inner text; its value is the shown value
        return self.page.box_value

    def click(self, timeout=None):
        self.page.events.append(("click", "combobox", "Resource Type"))
        self.page.box_attrs["aria-expanded"] = "true"

    def press(self, key, timeout=None):
        self.page.keys.append(key)
        if key == "Enter" and self.page.on_enter is not None:
            self.page.on_enter()


class _OtherBox:
    """Another control the walk must never read or press: a hidden copy, or a longer name."""

    def __init__(self, name, visible, value="Variable"):
        self.name = name
        self.visible = visible
        self.value = value
        self.attrs = {"aria-expanded": "true", "aria-activedescendant": "sample-option-2"}
        self.reads = 0
        self.keys = []

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def nth(self, index):
        return self

    def is_visible(self):
        return self.visible

    def get_attribute(self, name, timeout=None):
        self.reads += 1
        return self.attrs.get(name)

    def input_value(self, timeout=None):
        self.reads += 1
        return self.value

    def inner_text(self, timeout=None):
        self.reads += 1
        return self.value

    def click(self, timeout=None):
        self.keys.append("click")

    def press(self, key, timeout=None):
        self.keys.append(key)


class _BoxesLoc:
    """Every control a role-and-name lookup matches, in document order."""

    def __init__(self, items):
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]

    @property
    def first(self):
        return self.items[0]


def _css_string(text):
    """The id a CSS [id="..."] string names. An unescaped quote is an invalid selector."""
    out, index = [], 0
    while index < len(text):
        char = text[index]
        if char == '"':
            raise ValueError("invalid selector")
        if char == "\\":
            digits = re.match(r"[0-9a-fA-F]{1,6} ?", text[index + 1:])
            if digits:
                out.append(chr(int(digits.group(0).strip(), 16)))
                index += 1 + len(digits.group(0))
                continue
            out.append(text[index + 1:index + 2])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


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
        self.box_role = "combobox"
        self.others = []  # _OtherBox controls ahead of the real one in document order
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

    def highlight(self, text, copies=1, option_id="sample-option-2"):
        self.box_attrs["aria-activedescendant"] = option_id
        self.ids[option_id] = [text] * copies

    def get_by_role(self, role, name=None, exact=True):
        if role == self.box_role and name and (name == "Resource Type" or not exact):
            # Playwright: exact matches the whole name; otherwise a case-insensitive substring.
            found = [
                other for other in self.others
                if other.name == name or (not exact and name.lower() in other.name.lower())
            ]
            if self.box and (name == "Resource Type" or name.lower() in "resource type"):
                found.append(_BoxLoc(self))
            return _BoxesLoc(found)
        if role == "option" and not name:
            return _OptionsLoc(self)
        return super().get_by_role(role, name, exact)

    def locator(self, selector):
        match = re.fullmatch(r'\[id="(.*)"\]', selector, re.S)
        if match:
            return _TextsLoc(self.ids.get(_css_string(match.group(1)), []))
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


    def hide_api_name(self, page, textbox=False):
        page.values["API Name"] = ""
        page.hidden.add(("label", "API Name"))
        if textbox:
            page.roles.add(("textbox", "API Name"))
            page.hidden.add(("textbox", "API Name"))

    def test_hidden_api_name_label_does_not_count_as_variable(self):
        page = ResourcePage()
        self.hide_api_name(page)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertTrue(result.note.startswith(UNSET + " Diagnostic:"))
        self.assertIn(("option", "Variable"), clicks(page))
        self.assertEqual(page.keys, [])

    def test_hidden_api_name_label_and_textbox_do_not_count(self):
        page = ResourcePage()
        self.hide_api_name(page, textbox=True)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.note.startswith(UNSET))
        self.assertFalse(guide_walk._api_name_ready(page))

    def test_api_name_label_shown_after_selection_continues(self):
        page = ResourcePage()
        self.hide_api_name(page)

        def reveal():
            page.select_variable(api_name=False)
            page.hidden.discard(("label", "API Name"))

        self.on_option(page, reveal)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.evidence_ids, ("var:new-resource",))
        self.assertEqual(page.keys, [])

    def test_visible_api_name_in_a_later_frame_counts_past_a_hidden_one(self):
        hidden_frame = ResourcePage()
        self.hide_api_name(hidden_frame)
        shown_frame = ResourcePage()
        shown_frame.values["API Name"] = ""
        page = ResourcePage()
        page.frames = [hidden_frame, shown_frame]
        self.assertTrue(guide_walk._api_name_ready(page))
        shown_frame.hidden.add(("label", "API Name"))
        self.assertFalse(guide_walk._api_name_ready(page))

    def open_on_variable(self, page, **highlight):
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Variable", **highlight)
        page.on_enter = page.select_variable

    def test_highlighted_option_that_only_contains_variable_gets_no_enter(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Sample Variable Two")
        page.on_enter = page.select_variable
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertTrue(result.note.startswith(UNSET + " Diagnostic:"))
        self.assertEqual(page.keys, [])

    def test_shown_value_that_only_contains_variable_is_not_variable(self):
        page = ResourcePage()
        self.on_option(page, lambda: setattr(page, "box_value", "Variables"))
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.freeze)
        self.assertTrue(result.note.startswith(UNSET + " Diagnostic:"))
        self.assertFalse(guide_walk._resource_is_variable(page))

    def test_page_that_keeps_loading_after_the_option_click_is_not_run_and_gets_no_enter(self):
        page = ResourcePage()
        self.open_on_variable(page)
        self.on_option(page, lambda: setattr(page, "spinning", True))
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "NOT_RUN")
        self.assertIn("still loading after about 0 seconds", result.note)
        self.assertIn("not marked failed", result.note)
        self.assertFalse(result.freeze)
        self.assertEqual(page.keys, [])

    def test_page_that_keeps_loading_after_enter_is_not_run(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Variable")
        page.on_enter = lambda: setattr(page, "spinning", True)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "NOT_RUN")
        self.assertIn("still loading after about 0 seconds", result.note)
        self.assertIn("not marked failed", result.note)
        self.assertFalse(result.freeze)
        self.assertEqual(page.keys, ["Enter"])

    def test_hidden_copy_of_the_control_is_never_read_or_pressed(self):
        page = ResourcePage()
        copy = _OtherBox("Resource Type", visible=False)
        page.others = [copy]
        self.open_on_variable(page)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(page.keys, ["Enter"])
        self.assertEqual((copy.reads, copy.keys), (0, []))

    def test_control_with_a_longer_name_is_never_read_or_pressed(self):
        page = ResourcePage()
        other = _OtherBox("Resource Type Filter", visible=True)
        page.others = [other]
        self.open_on_variable(page)
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(page.keys, ["Enter"])
        self.assertEqual((other.reads, other.keys), (0, []))

    def timed_state(self, page, left, timeout_s=0):
        state = state_for(page, page_wait_s=30)
        state.timeout_s = timeout_s
        state.step_deadline = state.clock() + left
        return state

    def test_resource_type_wait_is_cut_to_the_step_time_left(self):
        page = ResourcePage()
        state = self.timed_state(page, left=4)
        result = guide_walk.run_record_variable(state, variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertTrue(result.note.startswith(UNSET))
        self.assertLessEqual(state.clock(), 5)

    def test_wait_after_enter_is_cut_to_the_step_time_left(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Variable")
        state = self.timed_state(page, left=15)
        result = guide_walk.run_record_variable(state, variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertEqual(page.keys, ["Enter"])
        self.assertLessEqual(state.clock(), 16)

    def test_page_that_keeps_loading_stops_at_the_step_time_left(self):
        page = ResourcePage()
        self.on_option(page, lambda: setattr(page, "spinning", True))
        state = self.timed_state(page, left=4, timeout_s=90)
        result = guide_walk.run_record_variable(state, variable_step())
        self.assertEqual(result.status, "NOT_RUN")
        self.assertIn("still loading after about 4 seconds", result.note)
        self.assertFalse(result.freeze)
        self.assertLessEqual(state.clock(), 5)

    def test_diagnostic_says_a_present_variable_option_is_not_visible(self):
        page = ResourcePage()
        page.hidden.add(("option", "Variable"))
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("option Variable present yes, visible no;", result.note)

    def test_resource_type_shown_as_a_button_is_read(self):
        page = ResourcePage()
        page.box_role = "button"
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertIn("Diagnostic: control role button; aria-expanded false;", result.note)
        page.box_value = "Variable"
        self.assertTrue(guide_walk._resource_is_variable(page))

    def test_resource_type_input_value_variable_counts(self):
        page = ResourcePage()
        page.box_input = "Select an Option"
        self.assertFalse(guide_walk._resource_is_variable(page))
        page.box_input = "Variable"
        self.assertTrue(guide_walk._resource_is_variable(page))

    def test_highlighted_id_with_a_quote_and_backslash_is_found(self):
        page = ResourcePage()
        self.open_on_variable(page, option_id='sample"opt\\2')
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertEqual(page.keys, ["Enter"])

    def test_no_highlighted_id_gets_no_enter_even_with_an_empty_id_element(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.ids[""] = ["Variable"]
        page.on_enter = page.select_variable
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertEqual(page.keys, [])

    def test_visible_api_name_already_shown_clicks_nothing_in_resource_type(self):
        page = ResourcePage()
        page.roles.add(("textbox", "API Name"))
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertNotIn(("option", "Variable"), clicks(page))
        self.assertNotIn(("combobox", "Resource Type"), clicks(page))
        self.assertEqual(page.keys, [])
        self.assertTrue(result.evidence_ids, "PASS must have evidence")

    def test_diagnostic_echoes_aria_expanded_only_as_true_false_or_none(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "sample-data"
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertIn("; aria-expanded none;", result.note)
        self.assertNotIn("sample-data", result.note)

    def test_page_loading_20s_after_option_click_continues_when_timeout_allows(self):
        page = ResourcePage()
        self.on_option(page, lambda: setattr(page, "spinning", True))

        def on_sleep(now):
            if now >= 20:
                page.spinning = False
                page.select_variable()

        state = state_for(page, page_wait_s=90, on_sleep=on_sleep)
        state.timeout_s = 90
        state.step_deadline = state.clock() + 120
        result = guide_walk.run_record_variable(state, variable_step())
        self.assertEqual(result.status, "PASS")
        self.assertGreaterEqual(state.clock(), 20)
        self.assertLessEqual(state.clock(), 30)

    def test_pick_wait_caps_at_10_seconds_when_page_wait_and_time_left_are_longer(self):
        page = ResourcePage()
        state = self.timed_state(page, left=120, timeout_s=90)
        state.page_wait_s = 90
        result = guide_walk.run_record_variable(state, variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertGreaterEqual(state.clock(), 10)
        self.assertLessEqual(state.clock(), 12)

    def test_no_enter_when_aria_expanded_is_missing(self):
        page = ResourcePage()
        page.box_attrs["aria-expanded"] = "true"
        page.highlight("Variable")
        page.on_enter = page.select_variable
        del page.box_attrs["aria-expanded"]
        result = guide_walk.run_record_variable(state_for(page), variable_step())
        self.assertEqual(result.status, "UI_MISMATCH")
        self.assertEqual(page.keys, [])


COMBOBOX_HTML = """
<span id="lbl">Resource Type</span>
<button id="box" role="combobox" aria-labelledby="lbl" aria-expanded="false"
        aria-controls="list">Select an Option</button>
<div id="list" role="listbox" hidden>
  <div role="option" id="opt-variable">Variable</div>
  <div role="option" id="opt-constant">Constant</div>
</div>
<div id="stale" hidden><label for="api-stale">API Name</label><input id="api-stale"></div>
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

    def choose(self, highlight, click_selects=False, extra="", variable_id=None):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        self.page = page
        page.set_content(extra + COMBOBOX_HTML)
        if variable_id is not None:
            page.evaluate("id => { document.getElementById('opt-variable').id = id; }", variable_id)
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

    def test_hidden_copy_and_longer_named_control_are_never_used(self):
        extra = """
<span id="lbl-f">Resource Type Filter</span>
<button id="filter" role="combobox" aria-labelledby="lbl-f" aria-expanded="true"
        aria-activedescendant="opt-variable">Variable</button>
<button id="copy" role="combobox" aria-label="Resource Type" aria-expanded="true"
        aria-activedescendant="opt-variable" hidden>Variable</button>
<script>
  window.otherKeys = 0;
  for (const id of ["filter", "copy"]) {
    document.getElementById(id).addEventListener("keydown", () => { window.otherKeys += 1; });
  }
</script>
"""
        result, enters, ready = self.choose("opt-variable", extra=extra)
        self.assertIsNone(result)
        self.assertEqual(enters, 1)
        self.assertTrue(ready)
        self.assertEqual(self.page.evaluate("window.otherKeys"), 0)

    def test_highlighted_id_with_a_quote_and_backslash_gets_enter(self):
        tricky = 'opt-"v\\2'
        result, enters, ready = self.choose(tricky, variable_id=tricky)
        self.assertIsNone(result)
        self.assertEqual(enters, 1)
        self.assertTrue(ready)


if __name__ == "__main__":
    unittest.main()
