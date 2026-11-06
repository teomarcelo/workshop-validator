"""V43 r9 (W-119 (1)): every fill into a labelled field counts as typed in that step, recorded
before the fill is tried.

A fill that writes the text and then raises still typed, so the value signal stays off and the
N4a, N4c and N4d autocomplete shapes are NEEDS_REVIEW. A plain text field the walk types into on
one poll, that gains a combobox role on a later poll of the same step, also typed. Real picks
signalled by read-only or selected still PASS. The edges of the one-second hold at the 0.2 s poll
are pinned. Real browser, synthetic names only, every request answered locally.
"""

import unittest
from unittest import mock

from tests import test_v43_pick_pass_paths as paths
from tests.test_v43_pick_hold_r8 import CLEARS, INLINE, LOWERS, RESTORE
from tests.test_v43_pick_pass_paths import (
    LOCKS, PASS_LOCKED, PASS_SELECTED, PILL, RO_OFF, RO_ON, SMOKE, SMOKE_REASON, UNCONFIRMED_NOTE,
    flip, on_load, typed_html,
)

try:
    from playwright.sync_api import Locator
except ImportError:  # pragma: no cover - the browser tests are skipped without Playwright.
    Locator = None

_BASE = paths.PickPassPathBrowserTests


def late_role_html(after_js="", on_second_input=False):
    """A plain text field (no role, no list named) that lowercases what is typed. 300 ms after the
    first input it becomes a combobox that names a list, and the list opens. The option click
    closes the list and restores the typed text 400 ms later (no pick), then runs AFTER_JS.
    ON_SECOND_INPUT: the role comes with the second input instead (the walk types again on its
    second poll), so at a short poll the role never lands between two reads of one poll."""
    delay = "inputs === 2" if on_second_input else "inputs === 1"
    wait = 0 if on_second_input else 300
    return f"""
<div id="dlg" role="dialog" aria-modal="true" aria-labelledby="t">
  <h2 id="t">Sample Screen</h2>
  <section aria-label="Fields">
    <sample-field id="host-rv">
      <label for="rv">Record Variable</label>
      <input id="rv" type="text" value="">
    </sample-field>
    <div id="lb" role="listbox" hidden>
      <div role="option" id="opt1">recThing</div>
    </div>
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
  let inputs = 0;
  const becomeCombobox = () => {{
    rv.setAttribute("role", "combobox");
    rv.setAttribute("aria-controls", "lb");
    rv.setAttribute("aria-expanded", "true");
    list.hidden = false;
  }};
  rv.addEventListener("input", () => {{
    window.typed.push(rv.value);
    rv.value = rv.value.toLowerCase();
    inputs += 1;
    if (!({delay})) return;
    if ({wait}) setTimeout(becomeCombobox, {wait}); else becomeCombobox();
  }});
  list.addEventListener("click", (event) => {{
    const picked = event.target.closest("[role=option]");
    if (!picked) return;
    window.chosen.push(picked.id);
    list.hidden = true;
    rv.setAttribute("aria-expanded", "false");
    setTimeout(() => {{ rv.value = "recThing"; }}, 400);
    {after_js}
  }});
</script>
"""


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class PickTypedR9BrowserTests(unittest.TestCase):
    setUpClass = classmethod(_BASE.setUpClass.__func__)
    tearDownClass = classmethod(_BASE.tearDownClass.__func__)
    open = _BASE.open
    fill = _BASE.fill
    assert_clicked = _BASE.assert_clicked
    assert_review = _BASE.assert_review
    assert_pass = _BASE.assert_pass

    def fill_raising(self, html, poll=0.2):
        """Fill, where every Locator.fill writes the text and then raises."""
        real = Locator.fill
        raised = []

        def writes_then_raises(loc, value, *args, **kwargs):
            real(loc, value, *args, **kwargs)
            raised.append(value)
            raise RuntimeError("synthetic fill error after writing")

        with mock.patch.object(Locator, "fill", writes_then_raises):
            result = self.fill(html, poll=poll)
        self.assertIn("recThing", raised)
        self.assertIn("recThing", self.page.evaluate("window.typed"))
        return result

    # A fill that writes and then raises still typed: the value signal is off.

    def test_n4a_after_a_fill_that_wrote_then_raised_is_review_at_0_2_s(self):
        self.assert_review(self.fill_raising(on_load(typed_html(""), INLINE)), UNCONFIRMED_NOTE)

    def test_n4a_after_a_fill_that_wrote_then_raised_is_review_at_2_s(self):
        self.assert_review(self.fill_raising(on_load(typed_html(""), INLINE), poll=2.0), UNCONFIRMED_NOTE)

    def test_n4c_after_a_fill_that_wrote_then_raised_is_review_at_0_2_s(self):
        self.assert_review(self.fill_raising(on_load(typed_html(""), CLEARS)), UNCONFIRMED_NOTE)

    def test_n4c_after_a_fill_that_wrote_then_raised_is_review_at_2_s(self):
        self.assert_review(self.fill_raising(on_load(typed_html(""), CLEARS), poll=2.0), UNCONFIRMED_NOTE)

    def test_n4d_after_a_fill_that_wrote_then_raised_is_review_at_0_2_s(self):
        self.assert_review(self.fill_raising(on_load(typed_html(RESTORE), LOWERS)), UNCONFIRMED_NOTE)

    def test_n4d_after_a_fill_that_wrote_then_raised_is_review_at_2_s(self):
        self.assert_review(self.fill_raising(on_load(typed_html(RESTORE), LOWERS), poll=2.0), UNCONFIRMED_NOTE)

    def test_a_real_read_only_pick_after_a_fill_that_wrote_then_raised_passes_at_2_s(self):
        self.assert_pass(self.fill_raising(typed_html(LOCKS), poll=2.0), PASS_LOCKED)

    # A plain field typed on one poll that becomes a combobox on a later poll still typed.

    def test_a_field_that_gains_a_combobox_role_after_typing_is_review_at_0_2_s(self):
        self.assert_review(self.fill(late_role_html(on_second_input=True)), UNCONFIRMED_NOTE)
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing", "recThing"])

    def test_a_field_that_gains_a_combobox_role_after_typing_is_review_at_2_s(self):
        self.assert_review(self.fill(late_role_html(), poll=2.0), UNCONFIRMED_NOTE)
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])

    def test_a_field_that_gains_a_combobox_role_then_turns_read_only_passes_at_2_s(self):
        self.assert_pass(self.fill(late_role_html("rv.readOnly = true;"), poll=2.0), PASS_LOCKED)

    def test_a_field_that_gains_a_combobox_role_then_marks_the_option_selected_passes_at_2_s(self):
        result = self.fill(late_role_html("picked.setAttribute('aria-selected', 'true');"), poll=2.0)
        self.assert_pass(result, PASS_SELECTED)

    # The one-second hold edges at the 0.2 s poll (code review finding 4).

    def test_read_only_on_at_300_ms_off_at_1500_ms_is_review_at_0_2_s(self):
        self.assert_review(self.fill(typed_html(flip(RO_ON, RO_OFF, 300, 1500))), UNCONFIRMED_NOTE)

    def test_read_only_on_at_300_ms_off_at_2500_ms_passes_at_0_2_s(self):
        self.assert_pass(self.fill(typed_html(flip(RO_ON, RO_OFF, 300, 2500))), PASS_LOCKED)


del _BASE  # Not a test class of this file.


if __name__ == "__main__":
    unittest.main()
