"""V22: every click waits for load (bounded poll) before NEEDS_REVIEW. Synthetic pages only."""
import unittest

from tests.test_guide_walk import Clock, Page, inventory_of, step
from workshop_validator.guide_walk import walk_guide

HOME = "https://example.lightning.force.com/lightning/page/home"


class PollClock(Clock):
    """Fake clock; ON_SLEEP(n) runs after the n-th wait, so the page can change while the walk polls."""

    def __init__(self, on_sleep=None):
        super().__init__()
        self.sleeps = []
        self.on_sleep = on_sleep

    def sleep(self, seconds):
        self.now += seconds
        self.sleeps.append(seconds)
        if self.on_sleep is not None:
            self.on_sleep(len(self.sleeps))


def clicks(page):
    return [event for event in page.events if event[0] == "click"]


def walk(page, item, clock, timeout_s=90, interval_s=2, page_wait_s=45):
    return walk_guide(
        page, inventory_of(item), checks=(),
        timeout_s=timeout_s, interval_s=interval_s, clock=clock, sleeper=clock.sleep,
        page_wait_s=page_wait_s,
    )


def load_step():
    return step("t1", "Exercise 9: Widgets", "Task 1: Load items.", "1", "Click Load.")


def tab_page():
    """A Widgets span inside its own tab, next to another tab whose text also has Widgets."""
    page = Page(HOME, body="Widgets\nArchived Widgets")
    own, other = ("tab", "Widgets list"), ("tab", "Archived Widgets")
    page.roles.update({own, other})
    page.tab_of[("text", "Widgets")] = own
    return page, own, other


def tab_step():
    return step("t2", "Exercise 9: Widgets", "Task 2: Open widgets.", "1", "Click Widgets.")


class ClickPollingTests(unittest.TestCase):
    def test_delayed_after_state_on_poll_three_passes_without_a_second_click(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if n == 3:
                page.url = "https://example.lightning.force.com/lightning/n/Load"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock)
        self.assertEqual(outcome.statuses["t1"], "PASS")
        self.assertEqual(outcome.evidence["t1"], ("t1:after-state:-url-changed",))
        self.assertEqual(clock.sleeps, [2, 2, 2])
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_nothing_changes_needs_review_after_page_wait_with_one_click(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})
        clock = PollClock()
        outcome = walk(page, load_step(), clock, timeout_s=90, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertIn("no change could be read", outcome.notes["t1"])
        # page_wait_s (10 s) ends the poll before timeout_s (90 s) and the step cap (40 s).
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_step_cap_shorter_than_page_wait_bounds_the_poll(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})
        clock = PollClock()
        # Step cap is min(page_wait_s + 30, 120) = 120 s, shorter than page_wait_s and timeout_s.
        outcome = walk(page, load_step(), clock, timeout_s=300, page_wait_s=200)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        # The poll stops the 5 s read margin before the 120 s cap, so the result is kept.
        self.assertEqual(sum(clock.sleeps), 115)
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_long_interval_poll_ends_inside_the_step_cap(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})
        clock = PollClock()
        outcome = walk(page, load_step(), clock, timeout_s=300, interval_s=7, page_wait_s=200)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [7] * 16 + [3])
        self.assertLess(clock.now, 120)
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_evidence_on_the_shortened_last_poll_passes(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if clock.now > 114:
                page.url = "https://example.lightning.force.com/lightning/n/Load"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, timeout_s=300, interval_s=7, page_wait_s=200)
        self.assertEqual(outcome.statuses["t1"], "PASS")
        self.assertEqual(outcome.evidence["t1"], ("t1:after-state:-url-changed",))
        self.assertEqual(clock.sleeps, [7] * 16 + [3])
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_saved_text_during_the_poll_is_not_evidence_for_a_non_save_click(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if n == 2:
                page.body = "Home\nRecord successfully saved"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_own_tab_selected_at_click_passes_without_waiting(self):
        page, own, _ = tab_page()

        def react(kind, name):
            if name == "Widgets":
                page.aria_states[(*own, "aria-selected")] = "true"

        page.on_click = react
        clock = PollClock()
        outcome = walk(page, tab_step(), clock)
        self.assertEqual(outcome.statuses["t2"], "PASS")
        self.assertEqual(outcome.evidence["t2"], ("t2:after-state:-Widgets-tab-selected",))
        self.assertEqual(clock.sleeps, [])

    def test_own_tab_selected_on_a_later_poll_passes_with_selected_evidence(self):
        page, own, _ = tab_page()

        def on_sleep(n):
            if n == 2:
                page.aria_states[(*own, "aria-selected")] = "true"

        clock = PollClock(on_sleep)
        outcome = walk(page, tab_step(), clock)
        self.assertEqual(outcome.statuses["t2"], "PASS")
        self.assertEqual(outcome.evidence["t2"], ("t2:after-state:-Widgets-tab-selected",))
        self.assertEqual(clock.sleeps, [2, 2])
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])

    def test_a_different_selected_tab_is_never_evidence(self):
        page, own, other = tab_page()
        page.aria_states[(*own, "aria-selected")] = "false"
        page.aria_states[(*other, "aria-selected")] = "true"
        clock = PollClock()
        outcome = walk(page, tab_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])

    def test_selected_outer_tab_around_the_clicked_inner_tab_is_not_evidence(self):
        page = Page(HOME, body="Widgets")
        inner, outer = ("tab", "Widgets inner"), ("tab", "Widgets section")
        page.roles.update({inner, outer})
        page.tab_of[("text", "Widgets")] = [inner, outer]
        page.aria_states[(*inner, "aria-selected")] = "false"
        page.aria_states[(*outer, "aria-selected")] = "true"
        clock = PollClock()
        outcome = walk(page, tab_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])


def add_dialog(page, title):
    dialog = ("dialog", f"{title}\nA short synthetic note for a new org")
    page.roles.update({dialog, ("h2", title)})
    page.children[dialog] = [("h2", title)]


class PollDialogTests(unittest.TestCase):
    """W-083: during the poll, a new dialog is evidence only when the step names it."""

    def test_unrelated_dialog_on_poll_two_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if n == 2:
                add_dialog(page, "Welcome Tour")

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_dialog_the_step_names_on_poll_two_passes(self):
        page = Page(HOME, body="Home", roles={("button", "Load Items")})

        def on_sleep(n):
            if n == 2:
                add_dialog(page, "Load Items")

        clock = PollClock(on_sleep)
        item = step("t1", "Exercise 9: Widgets", "Task 1: Load items.", "1", "Click Load Items.")
        outcome = walk(page, item, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "PASS")
        self.assertEqual(outcome.evidence["t1"], ("t1:after-state:-dialog-opened",))
        self.assertEqual(clock.sleeps, [2, 2])
        self.assertEqual(clicks(page), [("click", "button", "Load Items")])


    def test_empty_dialog_on_poll_two_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if n == 2:
                page.roles.add(("dialog", ""))

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_short_unnamed_dialog_on_poll_two_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if n == 2:
                page.roles.add(("dialog", "Hi"))

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)

    def test_dialog_title_inside_a_longer_step_word_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Download")})

        def on_sleep(n):
            if n == 2:
                add_dialog(page, "Load")

        clock = PollClock(on_sleep)
        item = step("t1", "Exercise 9: Widgets", "Task 1: Get items.", "1", "Click Download.")
        outcome = walk(page, item, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)

    def test_named_dialog_open_before_the_click_is_not_new_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load Items")})
        add_dialog(page, "Load Items")

        def on_sleep(n):
            if n == 2:
                page.roles.add(("dialog", ""))

        clock = PollClock(on_sleep)
        item = step("t1", "Exercise 9: Widgets", "Task 1: Load items.", "1", "Click Load Items.")
        outcome = walk(page, item, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load Items")])


LOAD_ITEMS = step("t1", "Exercise 9: Widgets", "Task 1: Load items.", "1", "Click Load Items.")


class PollDialogVisibilityTests(unittest.TestCase):
    """A dialog is evidence only when it is visible, newly counted and has a real title."""

    def test_hidden_named_dialog_next_to_a_visible_unrelated_one_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load Items")})

        def on_sleep(n):
            if n == 2:
                add_dialog(page, "Welcome Tour")
                add_dialog(page, "Load Items")
                page.hidden.add(("dialog", "Load Items\nA short synthetic note for a new org"))

        clock = PollClock(on_sleep)
        outcome = walk(page, LOAD_ITEMS, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load Items")])

    def test_named_dialog_that_replaces_one_already_counted_is_not_a_new_dialog(self):
        page = Page(HOME, body="Home", roles={("button", "Load Items")})
        add_dialog(page, "Getting Started")

        def on_sleep(n):
            if n == 2:
                page.roles.discard(("dialog", "Getting Started\nA short synthetic note for a new org"))
                page.roles.discard(("h2", "Getting Started"))
                add_dialog(page, "Load Items")

        clock = PollClock(on_sleep)
        outcome = walk(page, LOAD_ITEMS, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load Items")])

    def test_untitled_dialog_reading_only_a_role_word_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("tab", "Files")})

        def on_sleep(n):
            if n == 2:
                page.roles.add(("dialog", "Tab"))

        clock = PollClock(on_sleep)
        item = step("t1", "Exercise 9: Widgets", "Task 1: Open files.", "1", "Click the Files tab.")
        outcome = walk(page, item, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)

    def test_untitled_dialog_reading_only_a_long_filler_word_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if n == 2:
                page.roles.add(("dialog", "Click"))

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)

    def test_untitled_dialog_with_a_title_under_four_letters_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Run Job")})

        def on_sleep(n):
            if n == 2:
                page.roles.add(("dialog", "Run"))

        clock = PollClock(on_sleep)
        item = step("t1", "Exercise 9: Widgets", "Task 1: Run the job.", "1", "Click Run Job.")
        outcome = walk(page, item, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)

    def test_untitled_dialog_whose_body_names_the_target_is_not_evidence(self):
        # W-084: only the heading is read; body text never names a dialog.
        page = Page(HOME, body="Home", roles={("button", "Load Items")})

        def on_sleep(n):
            if n == 2:
                page.roles.add(("dialog", "Load Items"))

        clock = PollClock(on_sleep)
        outcome = walk(page, LOAD_ITEMS, clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load Items")])


def poll_walk(page, item, dialog_title=None, on_poll_two=None):
    """Walk ITEM; on the second wait add a dialog headed DIALOG_TITLE, or run ON_POLL_TWO."""

    def on_sleep(n):
        if n == 2:
            if dialog_title is not None:
                add_dialog(page, dialog_title)
            if on_poll_two is not None:
                on_poll_two()

    clock = PollClock(on_sleep)
    return walk(page, item, clock, page_wait_s=10), clock


class PollDialogHeadingTests(unittest.TestCase):
    """W-084: a poll dialog is evidence only when its heading equals a step target name."""

    def assert_needs_review(self, page, item, outcome, clock, kind, name):
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", kind, name)])

    def test_heading_that_is_part_of_the_target_word_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Loader")})
        item = step("t1", "Exercise 9: Widgets", "Task 1: Load.", "1", "Click Loader.")
        outcome, clock = poll_walk(page, item, dialog_title="Load")
        self.assert_needs_review(page, item, outcome, clock, "button", "Loader")

    def test_heading_that_is_only_the_role_word_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("tab", "Files")})
        item = step("t1", "Exercise 9: Widgets", "Task 1: Open files.", "1", "Click the Files tab.")
        outcome, clock = poll_walk(page, item, dialog_title="Tab")
        self.assert_needs_review(page, item, outcome, clock, "tab", "Files")

    def test_heading_equal_to_a_target_under_four_letters_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "New")})
        item = step("t1", "Exercise 9: Widgets", "Task 1: Add one.", "1", "Click New.")
        outcome, clock = poll_walk(page, item, dialog_title="New")
        self.assert_needs_review(page, item, outcome, clock, "button", "New")

    def test_heading_equal_to_a_filler_word_target_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Panel")})
        item = step("t1", "Exercise 9: Widgets", "Task 1: Open it.", "1", "Click Panel.")
        outcome, clock = poll_walk(page, item, dialog_title="Panel")
        self.assert_needs_review(page, item, outcome, clock, "button", "Panel")

    def test_punctuation_only_heading_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "----")})
        item = step("t1", "Exercise 9: Widgets", "Task 1: Add one.", "1", 'Click "----".')
        outcome, clock = poll_walk(page, item, dialog_title="----")
        self.assert_needs_review(page, item, outcome, clock, "button", "----")

    def test_heading_equal_to_the_target_passes(self):
        page = Page(HOME, body="Home", roles={("button", "Load Items")})
        outcome, clock = poll_walk(page, LOAD_ITEMS, dialog_title="Load Items")
        self.assertEqual(outcome.statuses["t1"], "PASS")
        self.assertEqual(outcome.evidence["t1"], ("t1:after-state:-dialog-opened",))
        self.assertEqual(clock.sleeps, [2, 2])

    def test_native_dialog_accepted_during_the_poll_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def accept():
            page._accepted_dialog = "Synthetic alert text"

        outcome, clock = poll_walk(page, load_step(), on_poll_two=accept)
        self.assert_needs_review(page, load_step(), outcome, clock, "button", "Load")


class PollTabBeforeStateTests(unittest.TestCase):
    """W-084: the clicked element's own tab must turn selected with the click."""

    def test_own_tab_already_selected_before_the_click_is_not_evidence(self):
        page, own, _ = tab_page()
        page.aria_states[(*own, "aria-selected")] = "true"
        clock = PollClock()
        outcome = walk(page, tab_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])

    def test_own_tab_that_turns_selected_on_the_poll_passes(self):
        page, own, _ = tab_page()
        page.aria_states[(*own, "aria-selected")] = "false"

        def on_sleep(n):
            if n == 1:
                page.aria_states[(*own, "aria-selected")] = "true"

        clock = PollClock(on_sleep)
        outcome = walk(page, tab_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "PASS")
        self.assertEqual(outcome.evidence["t2"], ("t2:after-state:-Widgets-tab-selected",))
        self.assertEqual(clock.sleeps, [2])


class PollUrlTitleTests(unittest.TestCase):
    """During the poll, URL or title evidence needs a real page change that names the target."""

    def test_unchanged_title_and_query_only_url_change_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})
        page.page_title = "Load | Salesforce"

        def on_sleep(n):
            if n == 2:
                page.url = HOME + "?t=123"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "button", "Load")])

    def test_path_that_already_named_the_target_before_the_click_is_not_evidence(self):
        before = "https://example.lightning.force.com/lightning/n/Load"
        page = Page(before, body="Home", roles={("button", "Load")})
        page.page_title = "Home | Salesforce"

        def on_sleep(n):
            if n == 2:
                page.url = before + "#t=123"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)

    def test_target_only_in_the_query_of_a_new_path_is_not_evidence(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})

        def on_sleep(n):
            if n == 2:
                page.url = "https://example.lightning.force.com/lightning/n/Other?view=load"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertEqual(clock.sleeps, [2] * 5)

    def test_title_that_changes_to_name_the_target_passes(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})
        page.page_title = "Home | Salesforce"

        def on_sleep(n):
            if n == 2:
                page.url = HOME + "?t=123"
                page.page_title = "Load | Salesforce"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "PASS")
        self.assertEqual(outcome.evidence["t1"], ("t1:after-state:-url-changed",))
        self.assertEqual(clock.sleeps, [2, 2])

    def test_new_path_naming_the_target_passes(self):
        page = Page(HOME, body="Home", roles={("button", "Load")})
        page.page_title = "Load | Salesforce"

        def on_sleep(n):
            if n == 2:
                page.url = "https://example.lightning.force.com/lightning/n/Load"

        clock = PollClock(on_sleep)
        outcome = walk(page, load_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t1"], "PASS")
        self.assertEqual(outcome.evidence["t1"], ("t1:after-state:-url-changed",))
        self.assertEqual(clock.sleeps, [2, 2])


class PollCapMarginTests(unittest.TestCase):
    def test_poll_ends_at_least_the_margin_before_the_step_cap(self):
        from workshop_validator.guide_walk import _POLL_CAP_MARGIN_S

        page = Page(HOME, body="Home", roles={("button", "Load")})
        clock = PollClock()
        # Step cap is 120 s here; a real final read takes time, so the margin covers it.
        outcome = walk(page, load_step(), clock, timeout_s=300, interval_s=7, page_wait_s=200)
        self.assertEqual(outcome.statuses["t1"], "NEEDS_REVIEW")
        self.assertGreaterEqual(_POLL_CAP_MARGIN_S, 2.0)
        self.assertLessEqual(clock.now, 120 - _POLL_CAP_MARGIN_S)
        self.assertLessEqual(clock.now, 118)
        self.assertEqual(clicks(page), [("click", "button", "Load")])


class _NoTab:
    """A locator with no match that records reads; a real one waits its timeout on a read."""

    def __init__(self, reads):
        self.reads = reads

    def count(self):
        return 0

    @property
    def first(self):
        return self

    def get_attribute(self, name, timeout=None):
        self.reads.append(name)
        raise TimeoutError("no element")


class _Target:
    def __init__(self, reads):
        self.reads = reads

    def count(self):
        return 1

    def nth(self, index):
        return self

    def is_visible(self):
        return True

    def locator(self, selector):
        return _NoTab(self.reads)


class ClickedTabReadTests(unittest.TestCase):
    def test_control_without_an_own_tab_is_not_read_and_not_evidence(self):
        from workshop_validator.guide_walk import _clicked_tab_selected

        reads = []
        self.assertFalse(_clicked_tab_selected(_Target(reads)))
        self.assertEqual(reads, [])

    def test_hidden_clicked_element_is_not_read_and_not_evidence(self):
        from workshop_validator.guide_walk import _clicked_tab_selected

        reads = []
        self.assertFalse(_clicked_tab_selected(_HiddenTarget(reads)))
        self.assertEqual(reads, [])


class _SelectedTab:
    """A selected role=tab that records each read."""

    def __init__(self, reads):
        self.reads = reads

    def count(self):
        return 1

    @property
    def first(self):
        return self

    def get_attribute(self, name, timeout=None):
        self.reads.append(name)
        return "true"


class _HiddenTarget(_Target):
    """The clicked element is in the DOM but not visible; its own tab reads as selected."""

    def is_visible(self):
        return False

    @property
    def first(self):
        return self

    def locator(self, selector):
        return _SelectedTab(self.reads)



class _DetachingStates(dict):
    """aria states that raise for one key once DETACHED is set, like a read of a detached element."""

    def __init__(self, key):
        super().__init__()
        self.key = key
        self.detached = False

    def get(self, key, default=None):
        if self.detached and key == self.key:
            raise TimeoutError("element is not attached to the DOM")
        return super().get(key, default)


class ClickedTabGuardTests(unittest.TestCase):
    """V25: the nearest own tab only, and a failed read after the click is never evidence."""

    def test_outer_tab_turning_selected_while_the_inner_own_tab_stays_unselected_is_not_evidence(self):
        page = Page(HOME, body="Widgets")
        inner, outer = ("tab", "Widgets inner"), ("tab", "Widgets section")
        page.roles.update({inner, outer})
        page.tab_of[("text", "Widgets")] = [inner, outer]
        page.aria_states[(*inner, "aria-selected")] = "false"
        page.aria_states[(*outer, "aria-selected")] = "false"

        def react(kind, name):
            if name == "Widgets":
                page.aria_states[(*outer, "aria-selected")] = "true"

        page.on_click = react
        clock = PollClock()
        outcome = walk(page, tab_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("t2", ()), ())
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])

    def test_tab_read_that_fails_after_the_click_is_not_evidence(self):
        page, own, _ = tab_page()
        page.aria_states = _DetachingStates((*own, "aria-selected"))
        page.aria_states[(*own, "aria-selected")] = "false"

        def react(kind, name):
            if name == "Widgets":
                page.aria_states.detached = True

        page.on_click = react
        clock = PollClock()
        outcome = walk(page, tab_step(), clock, page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.evidence.get("t2", ()), ())
        self.assertEqual(clock.sleeps, [2] * 5)
        self.assertEqual(clicks(page), [("click", "text", "Widgets")])

    def test_tab_read_that_works_before_and_after_the_click_passes(self):
        page, own, _ = tab_page()
        page.aria_states = _DetachingStates((*own, "aria-selected"))
        page.aria_states[(*own, "aria-selected")] = "false"

        def react(kind, name):
            if name == "Widgets":
                page.aria_states[(*own, "aria-selected")] = "true"

        page.on_click = react
        outcome = walk(page, tab_step(), PollClock(), page_wait_s=10)
        self.assertEqual(outcome.statuses["t2"], "PASS")
        self.assertEqual(outcome.evidence["t2"], ("t2:after-state:-Widgets-tab-selected",))


class _Heading:
    def __init__(self, texts):
        self.texts = texts

    def count(self):
        return len(self.texts)

    @property
    def first(self):
        return self

    def inner_text(self, timeout=None):
        return self.texts[0]


class _HeadedDialog:
    """A visible dialog whose headings are given per selector."""

    def __init__(self, headings):
        self.headings = headings

    def count(self):
        return 1

    def nth(self, index):
        return self

    def is_visible(self):
        return True

    def locator(self, selector):
        return _Heading(self.headings.get(selector, []))


class _DialogRoot:
    def __init__(self, dialog):
        self.dialog = dialog
        self.frames = []

    def locator(self, selector):
        return self.dialog if selector == "[role='dialog']" else _Heading([])


class TargetDialogHeadingOrderTests(unittest.TestCase):
    """V25: an empty heading does not hide a real heading after it."""

    def test_empty_first_heading_followed_by_a_named_one_reads_the_named_one(self):
        from workshop_validator.guide_walk import _target_dialogs

        dialog = _HeadedDialog({"h1": ["  "], "h2": ["Load Items"], "[role='heading']": [""]})
        named = _target_dialogs(_DialogRoot(dialog), frozenset({"load items"}))
        self.assertEqual(named, frozenset({"load items"}))

    def test_dialog_with_only_empty_headings_is_not_named(self):
        from workshop_validator.guide_walk import _target_dialogs

        dialog = _HeadedDialog({"h1": [""], "h2": ["  "]})
        self.assertEqual(_target_dialogs(_DialogRoot(dialog), frozenset({"load items"})), frozenset())


if __name__ == "__main__":
    unittest.main()
