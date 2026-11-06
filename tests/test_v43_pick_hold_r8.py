"""V43 r8 (W-119): typed text is never a value signal, the hold starts at the first signaled
read, every kept read checks the signal, and the hold watcher stops itself.

(1) After the walk typed into the box in a step, only read-only or selected can PASS: a page's
autocomplete can rewrite typed text and restore it (N4a, N4c, N4d). (2) A signal that lands a
little after the click starts the hold on that read (N5). (3) Every kept read after the hold
starts must read the same signal, and the watch must have run recently (H2: page timers paused;
H5: setInterval replaced). (4) The watch stops itself on a break and after a hard cap; every exit
path leaves no watch running. Real browser, synthetic names only, every request answered locally.
"""

import unittest
from unittest import mock

from workshop_validator import guide_walk
from tests import test_v43_pick_pass_paths as paths
from tests.test_v43_pick_pass_paths import (
    LOCKS, PASS_LOCKED, PASS_SELECTED, RO_OFF, RO_ON, SMOKE, SMOKE_REASON,
    UNCONFIRMED_NOTE, UNSTEADY_NOTE, flip, make_step, on_load, pill_beside, typed_html,
)

# The shared browser helpers, borrowed without running that file's tests again here.
_BASE = paths.PickPassPathBrowserTests

# N4a: inline completion. Typing recThing shows a longer suggestion; leaving the box (the option
# click) reverts it to the typed text. Nothing is picked.
INLINE = ("rv.addEventListener('input', () => { if (rv.value === 'recThing') rv.value = 'recThing Suggested'; });"
          "rv.addEventListener('blur', () => { rv.value = 'recThing'; });")
# N4c: the search box clears the typed text while an option is hovered and restores it on blur.
CLEARS = ("document.getElementById('opt1').addEventListener('mouseenter', () => { rv.value = ''; });"
          "rv.addEventListener('blur', () => { rv.value = 'recThing'; });")
# N4d: the box lowercases the typed text; the option click restores it 400 ms later (no pick).
LOWERS = "rv.addEventListener('input', () => { rv.value = rv.value.toLowerCase(); });"
RESTORE = "setTimeout(() => { rv.value = 'recThing'; }, 400);"
# The page replaces setInterval with a no-op, so a hold watch never runs (H5).
NO_INTERVAL = "window.setInterval = () => 0;"
# Counts the page's live intervals (window.liveWatch()) and every interval made (window.madeWatch).
COUNT = """window.liveSet = new Set();
window.madeWatch = 0;
const realSet = window.setInterval.bind(window);
const realClear = window.clearInterval.bind(window);
window.setInterval = (fn, ms, ...rest) => { const id = realSet(fn, ms, ...rest); window.liveSet.add(id);
  window.madeWatch += 1; return id; };
window.clearInterval = (id) => { window.liveSet.delete(id); realClear(id); };
window.liveWatch = () => window.liveSet.size;"""


def late_list(html, ms=600):
    """The list opens MS after typing, so the walk types in one poll and clicks in a later one."""
    return html.replace("list.hidden = false;", f"setTimeout(() => {{ list.hidden = false; }}, {ms});")


@unittest.skipUnless(SMOKE, SMOKE_REASON)
class PickHoldR8BrowserTests(unittest.TestCase):
    setUpClass = classmethod(_BASE.setUpClass.__func__)
    tearDownClass = classmethod(_BASE.tearDownClass.__func__)
    open = _BASE.open
    fill = _BASE.fill
    fill_scripted = _BASE.fill_scripted
    assert_clicked = _BASE.assert_clicked
    assert_review = _BASE.assert_review
    assert_pass = _BASE.assert_pass

    def scripted(self, html, on_poll, interval, page_wait_s, page_clock=False):
        """Fill with a stepped clock of INTERVAL seconds. ON_POLL(n) runs after the n-th closed
        read of the pick (1-based), before the next poll."""
        page = self.open(html, page_clock=page_clock)
        now = [100.0]
        reads = [0]
        due = []

        def sleeper(seconds):
            now[0] += seconds
            while due:
                due.pop(0)()
            page.wait_for_timeout(20)

        state = guide_walk.WalkState(page=page, timeout_s=page_wait_s + 3, interval_s=interval,
                                     page_wait_s=page_wait_s, page_poll_s=interval, clock=lambda: now[0],
                                     sleeper=sleeper)
        guide_walk._open_guide_dialog(state, "Sample Screen")
        real = guide_walk._closed_read

        def closed_read(*args, **kwargs):
            out = real(*args, **kwargs)
            if out[0] is not None:
                reads[0] += 1
                n = reads[0]
                due.append(lambda: on_poll(n))
            return out

        guide_walk._closed_read = closed_read
        self.addCleanup(setattr, guide_walk, "_closed_read", real)
        return guide_walk.attempt_step(state, make_step("rv", "Record Variable: recThing"))

    def live(self):
        return self.page.evaluate("window.liveWatch()"), self.page.evaluate("window.madeWatch")

    # (1) Autocomplete after typing: the value changing to the name is not a signal.

    def test_n4a_inline_completion_reverted_on_blur_is_review_at_0_2_s(self):
        self.assert_review(self.fill(on_load(typed_html(""), INLINE)), UNCONFIRMED_NOTE)

    def test_n4a_inline_completion_reverted_on_blur_is_review_at_2_s(self):
        self.assert_review(self.fill(on_load(typed_html(""), INLINE), poll=2.0), UNCONFIRMED_NOTE)

    def test_n4c_search_box_cleared_on_hover_restored_on_blur_is_review_at_0_2_s(self):
        self.assert_review(self.fill(on_load(typed_html(""), CLEARS)), UNCONFIRMED_NOTE)

    def test_n4c_search_box_cleared_on_hover_restored_on_blur_is_review_at_2_s(self):
        self.assert_review(self.fill(on_load(typed_html(""), CLEARS), poll=2.0), UNCONFIRMED_NOTE)

    def test_n4d_lowercased_then_restored_after_the_click_is_review_at_0_2_s(self):
        self.assert_review(self.fill(on_load(typed_html(RESTORE), LOWERS)), UNCONFIRMED_NOTE)

    def test_n4d_lowercased_then_restored_after_the_click_is_review_at_2_s(self):
        self.assert_review(self.fill(on_load(typed_html(RESTORE), LOWERS), poll=2.0), UNCONFIRMED_NOTE)

    def test_n4d_typed_in_an_earlier_poll_of_the_step_is_still_review_at_2_s(self):
        # The list opens 600 ms after typing: the walk types on one poll and clicks on a later
        # one, where the box shows recthing and nothing is typed. It still typed in this step.
        result = self.fill(on_load(late_list(typed_html(RESTORE)), LOWERS), poll=2.0)
        self.assert_review(result, UNCONFIRMED_NOTE)
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])

    def test_a_real_pick_that_turns_the_box_read_only_after_typing_passes_at_2_s(self):
        self.assert_pass(self.fill(typed_html(LOCKS), poll=2.0), PASS_LOCKED)
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])

    def test_a_real_pick_that_marks_the_option_selected_after_typing_passes_at_2_s(self):
        result = self.fill(typed_html("picked.setAttribute('aria-selected', 'true');"), poll=2.0)
        self.assert_pass(result, PASS_SELECTED)
        self.assertEqual(self.page.evaluate("window.typed"), ["recThing"])

    # (2) N5: the signal lands 300 ms after the click; the hold starts on that read.

    def test_n5_read_only_300_ms_after_the_click_passes_at_2_s(self):
        self.assert_pass(self.fill(typed_html("setTimeout(() => { rv.readOnly = true; }, 300);"), poll=2.0),
                         PASS_LOCKED)

    def test_n5_selected_300_ms_after_the_click_passes_at_2_s(self):
        after = "setTimeout(() => { picked.setAttribute('aria-selected', 'true'); }, 300);"
        self.assert_pass(self.fill(typed_html(after), poll=2.0), PASS_SELECTED)

    def test_n5_read_only_300_ms_after_the_click_passes_at_0_2_s(self):
        self.assert_pass(self.fill(typed_html("setTimeout(() => { rv.readOnly = true; }, 300);")), PASS_LOCKED)

    # (3) Every kept read checks the signal; a watch that did not run is no hold.

    def h2(self, interval, page_wait_s):
        """Page timers pause after the first read; read-only is off on polls 2 and 3, then on.
        The pause jumps only 20 ms, so the watch's last run is recent and only the reads see
        read-only off."""
        def on_poll(n):
            if n == 1:
                self.page.clock.pause_at(self.page.evaluate("Date.now()") + 20)
                self.page.evaluate("() => { rv.readOnly = false; }")
            if n == 3:
                self.page.evaluate("() => { rv.readOnly = true; }")
        return self.scripted(typed_html(LOCKS), on_poll, interval, page_wait_s, page_clock=True)

    def test_h2_timers_paused_read_only_off_on_two_polls_then_on_is_review_at_0_25_s(self):
        self.assert_review(self.h2(0.25, 2.0), UNSTEADY_NOTE)
        self.assertTrue(self.page.evaluate("document.getElementById('rv').readOnly"))

    def test_h2_timers_paused_read_only_off_on_two_polls_then_on_is_review_at_2_s(self):
        self.assert_review(self.h2(2.0, 6.0), UNSTEADY_NOTE)
        self.assertTrue(self.page.evaluate("document.getElementById('rv').readOnly"))

    def test_a_read_with_the_signal_off_breaks_the_hold_even_when_the_watch_reads_unbroken(self):
        # The watch is made to read unbroken on every read (as when the page changes the box
        # between two of its runs): only the reads see read-only off on polls 2 and 3. The break
        # is final, so read-only back on from poll 4 is still not a hold.
        real = guide_walk._hold

        def blind(held, option, kind, mode):
            out = real(held, option, kind, mode)
            return True if mode == "read" else out

        def on_poll(n):
            if n == 1:
                self.page.evaluate("() => { rv.readOnly = false; }")
            if n == 3:
                self.page.evaluate("() => { rv.readOnly = true; }")

        with mock.patch.object(guide_walk, "_hold", blind):
            result = self.scripted(typed_html(LOCKS), on_poll, 0.25, 2.0)
        self.assert_review(result, UNSTEADY_NOTE)
        self.assertTrue(self.page.evaluate("document.getElementById('rv').readOnly"))

    def test_h5_page_replaces_set_interval_is_review_at_0_2_s(self):
        html = on_load(typed_html(LOCKS + flip(RO_OFF, RO_ON, 500, 700)), NO_INTERVAL)
        self.assert_review(self.fill(html), UNSTEADY_NOTE)

    def test_h5_page_replaces_set_interval_is_review_at_2_s(self):
        html = on_load(typed_html(LOCKS + flip(RO_OFF, RO_ON, 500, 700)), NO_INTERVAL)
        self.assert_review(self.fill(html, poll=2.0), UNSTEADY_NOTE)

    def test_a_watch_that_fails_to_start_is_review_on_the_final_read(self):
        real = guide_walk._hold

        def no_watch(held, option, kind, mode):
            return False if mode == "watch" else real(held, option, kind, mode)

        with mock.patch.object(guide_walk, "_hold", no_watch):
            result = self.fill(typed_html(LOCKS))
        self.assert_review(result, UNSTEADY_NOTE)

    # (4) Watch lifetime: no watch outlives the pick, on any exit path.

    def assert_no_watch_left(self, made_at_least=1):
        live, made = self.live()
        self.assertGreaterEqual(made, made_at_least)
        self.assertEqual(live, 0)

    def test_no_watch_left_after_a_pass(self):
        self.assert_pass(self.fill(on_load(typed_html(LOCKS), COUNT)), PASS_LOCKED)
        self.assert_no_watch_left()

    def test_no_watch_left_after_a_broken_hold(self):
        self.assert_review(self.fill(on_load(typed_html(RO_ON + flip(RO_OFF, RO_ON, 300, 600)), COUNT)),
                           UNSTEADY_NOTE)
        self.assert_no_watch_left()

    def test_no_watch_left_after_the_pill_branch(self):
        result = self.fill(on_load(typed_html(LOCKS + pill_beside(400)), COUNT))
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assert_no_watch_left()

    def test_no_watch_left_after_a_re_render(self):
        def rerender(n):
            if n == 1:
                self.page.evaluate("() => { const next = rv.cloneNode(true); next.value = 'recThing'; "
                                   "next.readOnly = true; rv.replaceWith(next); rv = next; }")
        result = self.fill_scripted(on_load(typed_html(LOCKS), COUNT), on_poll=rerender, page_wait_s=1.25)
        self.assert_pass(result, PASS_LOCKED)
        self.assert_no_watch_left(made_at_least=2)

    def test_no_watch_left_after_the_box_is_removed(self):
        result = self.fill(on_load(typed_html(LOCKS + "setTimeout(() => { rv.remove(); }, 400);"), COUNT))
        self.assertNotEqual(result.status, "PASS", result.note)
        self.assert_no_watch_left()

    def test_no_watch_left_after_the_pick_runs_out_of_time(self):
        result = self.fill_scripted(on_load(typed_html(LOCKS), COUNT), page_wait_s=0.5)
        self.assertEqual(result.status, "NEEDS_REVIEW", result.note)
        self.assert_no_watch_left()

    def test_no_watch_left_after_a_crash(self):
        def crash(n):
            if n == 2:
                raise RuntimeError("synthetic crash")
        try:
            self.scripted(on_load(typed_html(LOCKS), COUNT), crash, 0.25, 2.0)
        except RuntimeError:
            pass
        self.assert_no_watch_left()

    def test_a_broken_watch_stops_itself_when_stop_is_never_called(self):
        real = guide_walk._hold

        def no_stop(held, option, kind, mode):
            return False if mode == "stop" else real(held, option, kind, mode)

        with mock.patch.object(guide_walk, "_hold", no_stop):
            result = self.fill(on_load(typed_html(RO_ON + flip(RO_OFF, RO_ON, 300, 600)), COUNT))
        self.assert_review(result, UNSTEADY_NOTE)
        self.assert_no_watch_left()

    def test_a_watch_past_its_hard_cap_is_no_hold_and_stops_itself(self):
        real = guide_walk._hold

        def no_stop(held, option, kind, mode):
            return False if mode == "stop" else real(held, option, kind, mode)

        with mock.patch.object(guide_walk, "_HOLD_CAP_MS", 300), mock.patch.object(guide_walk, "_hold", no_stop):
            result = self.fill(on_load(typed_html(LOCKS), COUNT))
        self.assert_review(result, UNSTEADY_NOTE)
        self.assert_no_watch_left()


del _BASE  # Not a test class of this file.


if __name__ == "__main__":
    unittest.main()
