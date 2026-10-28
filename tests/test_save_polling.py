"""Test that polling for delayed banners only happens for Save/Submit controls."""
import unittest
from types import SimpleNamespace

from workshop_validator.guide_walk import walk_guide, _act_click
from workshop_validator.guide_inventory import GuideInventory, GuideStep


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


def inventory_of(*items):
    return GuideInventory(1, tuple(items), (), ())


class FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class FakePage:
    def __init__(self, url, roles=None):
        self.url = url
        self.body = ""
        self.roles = set(roles or [])
        self.values = {}
        self.events = []
        self.page_title = ""
        self.spinning = False
        self._status_elements = []
        self._alert_elements = []
        self._frames = None

    @property
    def frames(self):
        if self._frames is not None:
            return self._frames
        return [self]

    def title(self):
        return self.page_title

    def locator(self, selector):
        if selector == "body":
            return FakeLoc(self, "body")
        if "spinner" in selector or "progressbar" in selector:
            return FakeLoc(self, "spinner")
        if "[role='status']" in selector:
            return FakeMultiLoc(self, self._status_elements)
        if "[role='alert']" in selector:
            return FakeMultiLoc(self, self._alert_elements)
        return FakeLoc(self, "css", selector)

    def get_by_role(self, role, name=None, exact=True):
        return FakeLoc(self, role, name or "")

    def get_by_label(self, name):
        return FakeLoc(self, "label", name)

    def content(self):
        return self.body or ""


class FakeLoc:
    def __init__(self, page, kind, name=""):
        self.page = page
        self.kind = kind
        self.name = name

    def count(self):
        if self.kind == "body":
            return 1
        if self.kind == "spinner":
            return 1 if self.page.spinning else 0
        if self.kind == "label":
            return 1 if self.name in self.page.values else 0
        return 1 if (self.kind, self.name) in self.page.roles else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.count() > 0

    def is_enabled(self):
        return True

    def click(self, timeout=None):
        self.page.events.append(("click", self.kind, self.name))
        if hasattr(self.page, "on_click"):
            self.page.on_click(self.kind, self.name)

    def fill(self, value, timeout=None):
        self.page.values[self.name] = value

    def input_value(self, timeout=None):
        return self.page.values.get(self.name, "")

    def inner_text(self, timeout=None):
        if self.kind == "body":
            return self.page.body
        return self.name if self.name else ""

    def get_attribute(self, name):
        return None


class FakeMultiLoc:
    def __init__(self, page, elements):
        self.page = page
        self.elements = list(elements)

    def all(self):
        return self.elements

    def count(self):
        return len(self.elements)


class FakeElement:
    def __init__(self, text, visible=True):
        self._text = text
        self._visible = visible

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text


class TestSavePolling(unittest.TestCase):
    def test_non_save_controls_do_not_poll_and_return_immediately(self):
        """Non-Save/Submit controls (like Done) don't enter the polling loop."""
        page = FakePage("https://example.lightning.force.com/lightning/page/home",
                        roles=[("button", "Done")])
        page.body = "Settings page"

        # Mock that clicking Done shows aria-selected=true
        mock_loc = SimpleNamespace()
        mock_loc.get_attribute = lambda attr: "true" if attr == "aria-selected" else None

        from workshop_validator import guide_walk as g
        from unittest.mock import patch

        clock = FakeClock()
        sleeps = []

        def fake_sleep(state, seconds):
            sleeps.append(seconds)
            clock.now += seconds

        state = SimpleNamespace(page=page, page_wait_s=90, step_deadline=None, clock=clock, timeout_s=90, interval_s=2, sleeper=None)

        with patch.object(g, "_control_visible", return_value=True), \
             patch.object(g, "_locator_for_name", return_value=mock_loc), \
             patch.object(g, "_click_locator", return_value=True), \
             patch.object(g, "_in_setup", return_value=False), \
             patch.object(g, "_read_page_text", return_value=""), \
             patch.object(g, "_visible_names_snapshot", return_value=frozenset()), \
             patch.object(g, "_visible_match", return_value=mock_loc), \
             patch.object(g, "_count_visible_dialogs", return_value=0), \
             patch.object(g, "_read_status_messages", return_value=frozenset()), \
             patch.object(g, "_words_visible", return_value=False), \
             patch.object(g, "_scoped_name_counts", return_value={}), \
             patch.object(g, "_sleep", side_effect=fake_sleep):
            result = g._act_click(state, step("a", "E1", "T1", "1", "Click Done."), "Done")

        # Should PASS with aria-selected evidence without polling
        self.assertEqual(result.status, "PASS")
        # Note contains evidence description
        self.assertIn("Clicked Done", result.note)
        # Should not have slept (no polling)
        self.assertEqual(sleeps, [])

    def test_save_control_polls_when_no_immediate_evidence(self):
        """Save control polls for delayed banner when there's no immediate evidence."""
        page = FakePage("https://example.lightning.force.com/lightning/page/home",
                        roles=[("button", "Save")])
        page.body = "Settings page"

        clock = FakeClock()
        sleeps = []

        def fake_sleep(state, seconds):
            sleeps.append(seconds)
            clock.now += seconds
            # Add banner after 2 seconds
            if clock.now >= 2:
                elem = FakeElement("Your changes have been successfully saved.")
                page._status_elements.append(elem)

        # The poll runs only through an injected sleeper (here routed to the patched _sleep).
        state = SimpleNamespace(page=page, page_wait_s=90, step_deadline=None, clock=clock, timeout_s=90, interval_s=2,
                                sleeper=lambda seconds: None)

        from workshop_validator import guide_walk as g
        from unittest.mock import patch

        mock_loc = SimpleNamespace()
        mock_loc.get_attribute = lambda attr: None

        with patch.object(g, "_control_visible", return_value=True), \
             patch.object(g, "_locator_for_name", return_value=mock_loc), \
             patch.object(g, "_click_locator", return_value=True), \
             patch.object(g, "_in_setup", return_value=False), \
             patch.object(g, "_read_page_text", return_value=""), \
             patch.object(g, "_visible_names_snapshot", return_value=frozenset()), \
             patch.object(g, "_visible_match", return_value=mock_loc), \
             patch.object(g, "_count_visible_dialogs", return_value=0), \
             patch.object(g, "_words_visible", return_value=False), \
             patch.object(g, "_scoped_name_counts", return_value={}), \
             patch.object(g, "_sleep", side_effect=fake_sleep):
            # Mock _read_status_messages to return empty on first call, then the message
            call_count = [0]
            def fake_status_messages(page):
                call_count[0] += 1
                if call_count[0] > 1 and page._status_elements:
                    return frozenset(["Your changes have been successfully saved."])
                return frozenset()

            with patch.object(g, "_read_status_messages", side_effect=fake_status_messages), \
                 patch.object(g, "_classify_save_message", return_value="success"):
                result = g._act_click(state, step("a", "E1", "T1", "1", "Click Save."), "Save")

        # Should PASS after polling found the banner
        self.assertEqual(result.status, "PASS")
        # Note contains evidence description
        self.assertIn("Clicked Save", result.note)
        # Should have slept once (polling happened)
        self.assertEqual(sleeps, [2])


def _no_evidence_patches(g, patch, mock_loc, words_visible=False):
    return [
        patch.object(g, "_control_visible", return_value=True),
        patch.object(g, "_locator_for_name", return_value=mock_loc),
        patch.object(g, "_click_locator", return_value=True),
        patch.object(g, "_in_setup", return_value=False),
        patch.object(g, "_read_page_text", return_value=""),
        patch.object(g, "_visible_names_snapshot", return_value=frozenset()),
        patch.object(g, "_visible_match", return_value=mock_loc),
        patch.object(g, "_count_visible_dialogs", return_value=0),
        patch.object(g, "_read_status_messages", return_value=frozenset()),
        patch.object(g, "_words_visible", return_value=words_visible),
        patch.object(g, "_scoped_name_counts", return_value={}),
    ]


class TestSavePollingClock(unittest.TestCase):
    """V10: the Save poll never falls back to the real clock or time.sleep."""

    def run_click(self, state, name, words_visible=False):
        from contextlib import ExitStack
        from unittest.mock import patch
        from workshop_validator import guide_walk as g

        mock_loc = SimpleNamespace(get_attribute=lambda attr: None)

        def no_real_time(*args, **kwargs):
            raise AssertionError("the Save poll used the real clock or time.sleep")

        with ExitStack() as stack:
            for item in _no_evidence_patches(g, patch, mock_loc, words_visible):
                stack.enter_context(item)
            stack.enter_context(patch.object(g.time, "sleep", side_effect=no_real_time))
            stack.enter_context(patch.object(g.time, "monotonic", side_effect=no_real_time))
            return g._act_click(state, step("a", "E1", "T1", "1", f"Click {name}."), name)

    def test_save_without_sleeper_or_page_wait_does_not_use_real_time(self):
        page = FakePage("https://example.lightning.force.com/lightning/page/home")
        state = SimpleNamespace(page=page, page_wait_s=90, step_deadline=None, clock=lambda: 0.0, timeout_s=90, interval_s=2, sleeper=None)
        result = self.run_click(state, "Save")
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertTrue(result.freeze)

    def test_save_poll_uses_page_wait_not_real_clock(self):
        page = FakePage("https://example.lightning.force.com/lightning/page/home")
        waits = []
        page.wait_for_timeout = lambda ms: waits.append(ms)
        state = SimpleNamespace(page=page, page_wait_s=90, step_deadline=None, clock=lambda: 0.0, timeout_s=6, interval_s=2, sleeper=None)
        result = self.run_click(state, "Submit")
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(waits, [2000, 2000, 2000])

    def test_save_poll_ends_by_waited_time_with_frozen_clock(self):
        page = FakePage("https://example.lightning.force.com/lightning/page/home")
        sleeps = []
        reads = []

        def frozen_clock():
            reads.append(1)
            if len(reads) > 50:
                raise AssertionError("the Save poll is timed by a clock that never moves")
            return 0.0

        state = SimpleNamespace(page=page, page_wait_s=90, step_deadline=None, clock=frozen_clock, timeout_s=4, interval_s=1,
                                sleeper=sleeps.append)
        result = self.run_click(state, "Save")
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertEqual(sleeps, [1, 1, 1, 1])

    def test_names_containing_save_poll_as_plain_clicks(self):
        """V22: every click polls (bounded) before NEEDS_REVIEW; names containing Save are plain clicks."""
        for name in ("Save to list view", "Saved items", "Submit for approval"):
            with self.subTest(name=name):
                sleeps = []
                clock = FakeClock()

                def sleeper(seconds):
                    sleeps.append(seconds)
                    clock.now += seconds

                page = FakePage("https://example.lightning.force.com/lightning/page/home")
                state = SimpleNamespace(page=page, page_wait_s=90, step_deadline=None, clock=clock, timeout_s=90, interval_s=2,
                                        sleeper=sleeper)
                result = self.run_click(state, name)
                self.assertEqual(result.status, "NEEDS_REVIEW")
                self.assertEqual(sleeps, [2.0] * 45)

    def test_new_status_message_is_save_only_evidence_during_the_poll(self):
        """V6 stays: a new success message counts only for Save or Submit, also on a later poll."""
        from contextlib import ExitStack
        from unittest.mock import patch
        from workshop_validator import guide_walk as g

        for name, expected in (("Save to list view", "NEEDS_REVIEW"), ("Save", "PASS")):
            with self.subTest(name=name):
                clock = FakeClock()

                def sleeper(seconds):
                    clock.now += seconds

                def messages(page):
                    return frozenset({"Widget Alpha was created."}) if clock.now >= 4 else frozenset()

                page = FakePage("https://example.lightning.force.com/lightning/page/home")
                state = SimpleNamespace(page=page, page_wait_s=90, step_deadline=None, clock=clock,
                                        timeout_s=10, interval_s=2, sleeper=sleeper)
                mock_loc = SimpleNamespace(get_attribute=lambda attr: None)
                with ExitStack() as stack:
                    for item in _no_evidence_patches(g, patch, mock_loc):
                        stack.enter_context(item)
                    stack.enter_context(patch.object(g, "_read_status_messages", side_effect=messages))
                    stack.enter_context(patch.object(g, "_classify_save_message", return_value="success"))
                    result = g._act_click(state, step("a", "E1", "T1", "1", f"Click {name}."), name)
                self.assertEqual(result.status, expected)


if __name__ == "__main__":
    unittest.main()
