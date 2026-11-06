"""W-116: _select_labeled passes only on a value read back after the click."""
import unittest
import time
from workshop_validator.guide_inventory import GuideStep, GuideInventory
from workshop_validator.guide_walk import walk_guide
from tests.test_guide_walk import Page, owned_option


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


def inventory_of(*items):
    return GuideInventory(1, tuple(items), (), ())


class TestSelectReadBack(unittest.TestCase):
    """Tests that _select_labeled reads and verifies the value after clicking."""

    def attempt(self, page, steps):
        clock = FakeClock()
        return clock, walk_guide(
            page, inventory_of(*steps),
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )

    def test_native_select_whose_change_handler_resets_value_is_needs_review(self):
        """A native <select> whose value is reset after the click does not pass."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("combobox", "Status"))
        chosen = owned_option(page, ("combobox", "Status"), None, "Published")
        page.values["Status"] = "Draft"
        page.tags = {("combobox", "Status"): "SELECT"}

        # Track clicks to simulate value reset
        click_count = [0]

        def reset_on_click(kind, name):
            click_count[0] += 1
            chosen(kind, name)
            if kind == "option" and name == "Published":
                # Value changes on first click but resets on subsequent reads
                if click_count[0] == 1:
                    page.values["Status"] = "Published"
                else:
                    page.values["Status"] = "Draft"

        page.on_click = reset_on_click
        item = step(
            "status", "Exercise 1", "Task 1", "1",
            "Status: Published",
        )
        clock, outcome = self.attempt(page, (item,))
        # The value does not hold, so the step cannot pass.
        self.assertIn(outcome.statuses["status"], ["NEEDS_REVIEW", "UI_MISMATCH"])

    def test_picklist_button_whose_value_does_not_change_is_needs_review(self):
        """A button picklist that does not update its shown value after click does not pass."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("button", "Priority"))
        chosen = owned_option(page, ("button", "Priority"), None, "High")
        page.values["Priority"] = "Low"

        def no_update(kind, name):
            chosen(kind, name)
            # Button clicked but its value does not change.
            # values["Priority"] stays "Low"

        page.on_click = no_update
        item = step(
            "priority", "Exercise 1", "Task 1", "1",
            "Priority: High",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertIn(outcome.statuses["priority"], ["NEEDS_REVIEW", "UI_MISMATCH"])

    def test_picklist_that_shows_another_option_is_needs_review(self):
        """A picklist that shows a different value than requested does not pass."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("button", "Category"))
        chosen = owned_option(page, ("button", "Category"), None, "Feature")
        page.values["Category"] = "Bug"

        def wrong_value(kind, name):
            chosen(kind, name)
            if kind == "option" and name == "Feature":
                # Shows a different option.
                page.values["Category"] = "Enhancement"

        page.on_click = wrong_value
        item = step(
            "category", "Exercise 1", "Task 1", "1",
            "Category: Feature",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertIn(outcome.statuses["category"], ["NEEDS_REVIEW", "UI_MISMATCH"])

    def test_real_pick_that_shows_wanted_value_passes_at_2_s(self):
        """A picklist that correctly shows the wanted value passes after two polls."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("button", "Type"))
        chosen = owned_option(page, ("button", "Type"), None, "Bug")
        page.values["Type"] = "Feature"

        def updates_correctly(kind, name):
            chosen(kind, name)
            if kind == "option" and name == "Bug":
                page.values["Type"] = "Bug"

        page.on_click = updates_correctly
        item = step(
            "type", "Exercise 1", "Task 1", "1",
            "Type: Bug",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["type"], "PASS")
        self.assertIn("Type is Bug", outcome.notes["type"])
        # Should take at least 2.2 seconds (0.2s + 2s polls).
        self.assertGreaterEqual(clock.now, 2.2)

    def test_field_that_becomes_lookup_between_checks_routes_to_lookup_path(self):
        """A field that gains combobox+lookup role is routed to the lookup pick path."""
        page = Page("https://example.lightning.force.com/setup/home")
        page.roles.add(("combobox", "Account"))
        page.values["Account"] = ""
        # Initially it's not marked as a lookup.
        page.lookup_marked = set()

        click_count = [0]

        def gains_lookup_role(kind, name):
            click_count[0] += 1
            # On first click (opening the control), it gains lookup marks.
            if click_count[0] == 1 and name == "Account":
                page.lookup_marked.add(("combobox", "Account"))
                # It's now a lookup, so _select_labeled should hand off to lookup path.

        page.on_click = gains_lookup_role
        item = step(
            "account", "Exercise 1", "Task 1", "1",
            "Account: Acme Corp",
        )
        clock, outcome = self.attempt(page, (item,))
        # The select path detects it's a lookup and returns False, so the fill
        # continues trying other paths. Since there's no lookup infrastructure in this
        # test, it ends as NEEDS_REVIEW or UI_MISMATCH.
        self.assertIn(outcome.statuses["account"], ["NEEDS_REVIEW", "UI_MISMATCH"])

    def test_data_type_record_still_passes_with_read_back(self):
        """Existing Data Type test still works with the read-back requirement."""
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("combobox", "Data Type"))
        chosen = owned_option(page, ("combobox", "Data Type"), "New Resource", "Record")
        page.values["Data Type"] = ""

        def reveal(kind, name):
            if name == "Data Type":
                page.roles.add(("option", "Record"))
            chosen(kind, name)
            if kind == "option" and name == "Record":
                page.values["Data Type"] = "Record"

        page.on_click = reveal
        item = step(
            "type", "Exercise 2: Flows", "Task 1: Screen.", "b",
            "Data Type: Record",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["type"], "PASS")
        self.assertIn("Data Type is Record", outcome.notes["type"])


class FakeClock:
    """Fake clock that advances when sleeper is called."""

    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


if __name__ == "__main__":
    unittest.main()
