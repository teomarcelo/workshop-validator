"""V28: a row's own actions button found by its exact label, and a note on why it was not found."""

import unittest
from unittest import mock

from workshop_validator.guide_walk import walk_guide
from tests.test_guide_walk import Clock, Loc, Page, inventory_of, step

URL = "https://example.lightning.force.com/lightning/o/ContentDocument/home"
STEP_TEXT = "Click the dropdown arrow to the far right of {row} and click Download."


def files_page(rows, popup=()):
    """rows: {row name: [button names]}; each row also holds a link with its own name."""
    page = Page(URL)
    for row, buttons in rows.items():
        page.roles.add(("row", row))
        page.roles.add(("link", row))
        inside = [("link", row)]
        for name in buttons:
            page.roles.add(("button", name))
            inside.append(("button", name))
        page.children[("row", row)] = inside
    for name in popup:
        page.has_popup.add(("button", name))
    return page


def open_menu_on(page, button):
    def reveal(kind, name):
        if kind == "button" and name == button:
            page.roles.add(("menuitem", "Download"))

    page.on_click = reveal


class RowActionsTests(unittest.TestCase):
    def walk(self, page, row):
        clock = Clock()
        item = step("file", "Exercise 1: Files", "Task 2: Download", "1", STEP_TEXT.format(row=row))
        outcome = walk_guide(
            page, inventory_of(item), checks=None,
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )
        clicks = [event for event in page.events if event[0] == "click"]
        return outcome.statuses["file"], outcome.notes["file"], clicks

    def assert_found(self, page, row, button):
        open_menu_on(page, button)
        status, note, clicks = self.walk(page, row)
        self.assertEqual(clicks, [("click", "button", button), ("click", "menuitem", "Download")])
        self.assertEqual(status, "NEEDS_REVIEW")

    def assert_not_found(self, page, row):
        status, note, clicks = self.walk(page, row)
        self.assertEqual(clicks, [])
        self.assertEqual(status, "UI_MISMATCH")
        self.assertIn(f"The dropdown in the {row} row was not found", note)
        return note

    def test_show_actions_button_without_haspopup_is_found(self):
        page = files_page({"Report Alpha": ["Show Actions"]})
        self.assert_found(page, "Report Alpha", "Show Actions")

    def test_show_n_more_actions_button_is_found(self):
        page = files_page({"Report Alpha": ["Show 3 more actions"]})
        self.assert_found(page, "Report Alpha", "Show 3 more actions")

    def test_label_with_line_break_is_normalized(self):
        page = files_page({"Report Alpha": ["Show\n  Actions"]})
        self.assert_found(page, "Report Alpha", "Show\n  Actions")

    def test_title_attribute_names_the_button(self):
        page = files_page({"Report Alpha": ["chevron"]})
        attrs = {("button", "chevron", "title"): "Show Actions"}
        original = Loc.get_attribute

        def get_attribute(self, name, timeout=None):
            if (self.kind, self.name, name) in attrs:
                return attrs[(self.kind, self.name, name)]
            return original(self, name, timeout)

        with mock.patch.object(Loc, "get_attribute", get_attribute):
            self.assert_found(page, "Report Alpha", "chevron")

    def test_aria_label_names_the_button(self):
        page = files_page({"Report Alpha": ["chevron"]})
        attrs = {("button", "chevron", "aria-label"): "Show actions"}
        original = Loc.get_attribute

        def get_attribute(self, name, timeout=None):
            if (self.kind, self.name, name) in attrs:
                return attrs[(self.kind, self.name, name)]
            return original(self, name, timeout)

        with mock.patch.object(Loc, "get_attribute", get_attribute):
            self.assert_found(page, "Report Alpha", "chevron")

    def test_two_show_actions_buttons_are_not_guessed(self):
        page = files_page({"Report Alpha": ["Show Actions", "Show 2 more actions"]})
        open_menu_on(page, "Show Actions")
        self.assert_not_found(page, "Report Alpha")

    def test_hidden_show_actions_button_is_never_used(self):
        page = files_page({"Report Alpha": ["Show Actions"]})
        page.hidden.add(("button", "Show Actions"))
        open_menu_on(page, "Show Actions")
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("buttons in the row: 0", note)

    def test_show_actions_in_another_row_is_never_used(self):
        page = files_page({"Report Alpha": [], "Report Beta": ["Show Actions"]})
        open_menu_on(page, "Show Actions")
        self.assert_not_found(page, "Report Alpha")

    def test_label_naming_the_file_is_not_an_actions_button(self):
        page = files_page({"Report Alpha": ["Actions for Report Alpha"]})
        open_menu_on(page, "Actions for Report Alpha")
        self.assert_not_found(page, "Report Alpha")

    def test_label_must_fully_match(self):
        page = files_page({"Report Alpha": ["Show Actions for Report Alpha"]})
        open_menu_on(page, "Show Actions for Report Alpha")
        self.assert_not_found(page, "Report Alpha")

    def test_two_haspopup_buttons_are_not_resolved_by_label(self):
        page = files_page(
            {"Report Alpha": ["Menu One", "Menu Two", "Show Actions"]}, popup=("Menu One", "Menu Two"),
        )
        open_menu_on(page, "Show Actions")
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("menu buttons in the row: 2", note)

    def test_not_found_note_has_counts_and_safe_labels(self):
        long_label = "A" * 31
        page = files_page({"Report Alpha": ["https://example.test/file/123", long_label, "Edit"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("Rows matched: 1", note)
        self.assertIn("menu buttons in the row: 0", note)
        self.assertIn("buttons in the row: 3", note)
        self.assertIn("button labels: other, other.", note)
        self.assertNotIn("http", note)
        self.assertNotIn("example.test", note)
        self.assertNotIn(long_label, note)
        # Only two labels are listed.
        self.assertNotIn("Edit", note)

    def test_note_keeps_the_target_name_as_a_label(self):
        page = files_page({"Report_Alpha-Final": ["Report_Alpha-Final", "Share"]})
        note = self.assert_not_found(page, "Report_Alpha-Final")
        self.assertIn("button labels: Report_Alpha-Final, Share.", note)

    def test_actions_button_is_used_not_an_earlier_button(self):
        page = files_page({"Report Alpha": ["Edit", "Show Actions"]})
        self.assert_found(page, "Report Alpha", "Show Actions")

    def test_person_name_label_is_other(self):
        page = files_page({"Report Alpha": ["Jane Smith"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: other.", note)
        self.assertNotIn("Jane", note)

    def test_date_label_is_other(self):
        page = files_page({"Report Alpha": ["Oct 28 2026"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: other.", note)
        self.assertNotIn("2026", note)

    def test_control_word_label_is_shown(self):
        page = files_page({"Report Alpha": ["Edit"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: Edit.", note)

    def test_plain_target_name_label_is_shown(self):
        page = files_page({"Report Alpha": ["Report Alpha"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: Report Alpha.", note)

    def test_first_two_labels_are_shown_in_order(self):
        page = files_page({"Report Alpha": ["Delete", "Edit", "Share"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: Delete, Edit.", note)

    def test_first_label_of_a_button_is_shown(self):
        page = files_page({"Report Alpha": ["Jane Smith"]})
        attrs = {("button", "Jane Smith", "aria-label"): "Edit"}
        original = Loc.get_attribute

        def get_attribute(self, name, timeout=None):
            if (self.kind, self.name, name) in attrs:
                return attrs[(self.kind, self.name, name)]
            return original(self, name, timeout)

        with mock.patch.object(Loc, "get_attribute", get_attribute):
            note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: Edit.", note)

    def test_show_n_more_actions_label_is_shown(self):
        page = files_page({"Report Alpha": ["Show Actions", "Show 2 more actions"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: Show Actions, Show 2 more actions.", note)

    def test_every_known_control_word_is_shown(self):
        words = (
            "Show Actions", "Actions", "More", "Edit", "Delete", "Download", "Share",
            "Preview", "Upload", "Open", "View", "Menu", "Options",
        )
        for word in words:
            with self.subTest(word=word):
                note = self.assert_not_found(files_page({"Report Alpha": [word, word]}), "Report Alpha")
                self.assertIn(f"button labels: {word}, {word}.", note)

    def test_row_without_buttons_lists_none(self):
        page = files_page({"Report Alpha": []})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("buttons in the row: 0; button labels: none.", note)

    def test_note_counts_rows_when_no_row_matches(self):
        page = files_page({"Report Beta": ["Show Actions"]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("Rows matched: 0.", note)
        self.assertNotIn("Show Actions", note)

    def test_note_counts_rows_when_two_rows_match(self):
        page = Page(URL)
        for row in ("row-one", "row-two"):
            page.roles.add(("row", row))
            page.children[("row", row)] = [("link", "Report Alpha")]
        page.roles.add(("link", "Report Alpha"))
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("Rows matched: 2.", note)

    def test_modal_block_gets_no_row_note(self):
        page = files_page({"Report Alpha": ["Edit"]})
        page.roles.add(("dialog", "Update Available\nInstall Now"))
        page.roles.add(("h2", "Update Available"))
        page.children[("dialog", "Update Available\nInstall Now")] = [("h2", "Update Available")]
        status, note, clicks = self.walk(page, "Report Alpha")
        self.assertEqual(status, "ENV_BLOCKED")
        self.assertEqual(clicks, [])
        self.assertNotIn("Rows matched", note)

    # Privacy (W-090): a label that only contains the row name or a control word is "other".
    def assert_label_is_other(self, label):
        page = files_page({"Report Alpha": [label]})
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: other.", note)
        self.assertNotIn("Jane", note)

    def test_label_containing_the_row_name_is_other(self):
        self.assert_label_is_other("Report Alpha by Jane Smith")

    def test_label_starting_with_show_actions_is_other(self):
        self.assert_label_is_other("Show Actions for Jane Smith")

    def test_label_starting_with_a_control_word_is_other(self):
        self.assert_label_is_other("Edit Jane Smith")

    # Look-alike: only "Show [N] [more] actions" names the row's actions button.
    def test_show_bulk_actions_is_not_the_actions_button(self):
        page = files_page({"Report Alpha": ["Show Bulk Actions"]})
        open_menu_on(page, "Show Bulk Actions")
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: other.", note)

    def test_show_actions_panel_is_not_the_actions_button(self):
        page = files_page({"Report Alpha": ["Show Actions Panel"]})
        open_menu_on(page, "Show Actions Panel")
        note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("button labels: other.", note)

    # Robustness: a failed read ends in the normal not-found note, never a crash.
    def test_label_read_that_raises_is_skipped(self):
        page = files_page({"Report Alpha": ["Edit"]})
        original = Loc.get_attribute

        def get_attribute(self, name, timeout=None):
            if (self.kind, self.name) == ("button", "Edit") and name == "aria-label":
                raise RuntimeError("synthetic read failure")
            return original(self, name, timeout)

        with mock.patch.object(Loc, "get_attribute", get_attribute):
            note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("buttons in the row: 1; button labels: Edit.", note)

    def test_visibility_read_that_raises_is_skipped(self):
        page = files_page({"Report Alpha": ["Edit", "Share"]})
        original = Loc.is_visible

        def is_visible(self):
            if (self.kind, self.name) == ("button", "Edit"):
                raise RuntimeError("synthetic read failure")
            return original(self)

        with mock.patch.object(Loc, "is_visible", is_visible):
            note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("buttons in the row: 1; button labels: Share.", note)

    def test_menu_button_lookup_that_raises_is_skipped(self):
        page = files_page({"Report Alpha": ["Edit"]})
        original = Loc.locator

        def locator(self, selector):
            if (self.kind, self.name) == ("row", "Report Alpha") and "aria-haspopup" in selector:
                raise RuntimeError("synthetic lookup failure")
            return original(self, selector)

        with mock.patch.object(Loc, "locator", locator):
            note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("menu buttons in the row: 0; buttons in the row: 1; button labels: Edit.", note)

    def test_button_lookup_that_raises_is_skipped(self):
        page = files_page({"Report Alpha": ["Show Actions"]})
        open_menu_on(page, "Show Actions")
        original = Loc.get_by_role

        def get_by_role(self, role, name=None, exact=True):
            if (self.kind, self.name) == ("row", "Report Alpha") and role == "button":
                raise RuntimeError("synthetic lookup failure")
            return original(self, role, name, exact)

        with mock.patch.object(Loc, "get_by_role", get_by_role):
            note = self.assert_not_found(page, "Report Alpha")
        self.assertIn("buttons in the row: 0; button labels: none.", note)


if __name__ == "__main__":
    unittest.main()
