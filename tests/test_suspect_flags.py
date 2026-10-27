"""Tests for suspect flag detection on PASS results."""
import unittest
from workshop_validator.guide_walk import _detect_suspect_flags, GuideStep
from workshop_validator.step_board import StepOutcome, _detect_chips


def make_step(step_id: str, text: str) -> GuideStep:
    """Create a synthetic GuideStep for testing."""
    return GuideStep(
        step_id=step_id,
        page=1,
        exercise="E1",
        task="T1",
        number="1",
        text=text,
        bbox=(0.0, 0.0, 1.0, 1.0),
        parent_id=None,
        warnings=()
    )


class TestSuspectFlags(unittest.TestCase):
    """Test suspect flag detection on different evidence types."""

    def test_presence_only_dialog_opened(self):
        """A PASS with only dialog opened evidence gets presence-only flag."""
        step = make_step("S001", "Click the Configure button")
        before_names = frozenset(["save", "cancel"])
        after_names = frozenset(["save", "cancel", "configure"])

        flags = _detect_suspect_flags(
            step,
            "after-state: dialog opened",
            before_names,
            after_names
        )

        self.assertIn("presence-only", flags)

    def test_presence_only_aria_selected(self):
        """A PASS with aria-selected evidence gets presence-only flag."""
        step = make_step("S002", "Click the Options tab")
        before_names = frozenset(["home", "settings"])
        after_names = frozenset(["home", "settings", "options"])

        flags = _detect_suspect_flags(
            step,
            "after-state: Options tab selected",
            before_names,
            after_names
        )

        self.assertIn("presence-only", flags)

    def test_presence_only_element_appeared(self):
        """A PASS with element appeared evidence gets presence-only flag."""
        step = make_step("S003", "Click the Add Field button")
        before_names = frozenset(["save", "cancel"])
        after_names = frozenset(["save", "cancel", "field name"])

        flags = _detect_suspect_flags(
            step,
            "after-state: field name appeared",
            before_names,
            after_names
        )

        self.assertIn("presence-only", flags)

    def test_no_presence_flag_with_success_message(self):
        """A PASS with success message evidence does not get presence-only flag."""
        step = make_step("S004", "Click the Save button")
        before_names = frozenset(["save", "cancel"])
        after_names = frozenset(["save", "cancel"])

        flags = _detect_suspect_flags(
            step,
            "after-state: success message visible",
            before_names,
            after_names
        )

        self.assertNotIn("presence-only", flags)

    def test_no_presence_flag_with_dialog_text_read(self):
        """A PASS with dialog text read does not get presence-only flag."""
        step = make_step("S005", "Click OK")
        before_names = frozenset(["confirm"])
        after_names = frozenset([])

        flags = _detect_suspect_flags(
            step,
            "after-state: dialog text read",
            before_names,
            after_names
        )

        self.assertNotIn("presence-only", flags)

    def test_no_screen_change_all_unchanged(self):
        """A PASS with unchanged URL, title, and headings gets no-screen-change flag."""
        step = make_step("S006", "Click the Dashboard link")
        before_names = frozenset(["home", "settings"])
        after_names = frozenset(["home", "settings"])
        before_headings = frozenset(["welcome", "quick actions"])
        after_headings = frozenset(["welcome", "quick actions"])

        flags = _detect_suspect_flags(
            step,
            "after-state: dashboard appeared",
            before_names,
            after_names,
            before_url="https://example.salesforce.com/setup/home",
            after_url="https://example.salesforce.com/setup/home",
            before_title="Setup Home",
            after_title="Setup Home",
            before_headings=before_headings,
            after_headings=after_headings
        )

        self.assertIn("no-screen-change", flags)

    def test_no_screen_change_flag_when_url_changes(self):
        """No screen-change flag when URL changes."""
        step = make_step("S007", "Click the Users link")
        before_names = frozenset(["home"])
        after_names = frozenset(["users"])
        before_headings = frozenset(["welcome"])
        after_headings = frozenset(["welcome"])

        flags = _detect_suspect_flags(
            step,
            "after-state: users appeared",
            before_names,
            after_names,
            before_url="https://example.salesforce.com/setup/home",
            after_url="https://example.salesforce.com/setup/users",
            before_title="Home",
            after_title="Home",
            before_headings=before_headings,
            after_headings=after_headings
        )

        self.assertNotIn("no-screen-change", flags)

    def test_no_screen_change_flag_when_title_changes(self):
        """No screen-change flag when title changes."""
        step = make_step("S008", "Click the Settings link")
        before_names = frozenset(["home"])
        after_names = frozenset(["settings"])
        before_headings = frozenset(["welcome"])
        after_headings = frozenset(["welcome"])

        flags = _detect_suspect_flags(
            step,
            "after-state: settings appeared",
            before_names,
            after_names,
            before_url="https://example.salesforce.com/setup",
            after_url="https://example.salesforce.com/setup",
            before_title="Home",
            after_title="Settings",
            before_headings=before_headings,
            after_headings=after_headings
        )

        self.assertNotIn("no-screen-change", flags)

    def test_no_screen_change_flag_when_headings_change(self):
        """No screen-change flag when headings change."""
        step = make_step("S009", "Click the Details button")
        before_names = frozenset(["home"])
        after_names = frozenset(["details"])
        before_headings = frozenset(["overview"])
        after_headings = frozenset(["details", "information"])

        flags = _detect_suspect_flags(
            step,
            "after-state: details appeared",
            before_names,
            after_names,
            before_url="https://example.salesforce.com/setup",
            after_url="https://example.salesforce.com/setup",
            before_title="Home",
            after_title="Home",
            before_headings=before_headings,
            after_headings=after_headings
        )

        self.assertNotIn("no-screen-change", flags)

    def test_no_screen_change_requires_all_parameters(self):
        """No screen-change flag is not set when parameters are missing."""
        step = make_step("S010", "Click the button")
        before_names = frozenset(["home"])
        after_names = frozenset(["home"])

        # Missing before_headings and after_headings
        flags = _detect_suspect_flags(
            step,
            "after-state: button clicked",
            before_names,
            after_names,
            before_url="https://example.salesforce.com/setup",
            after_url="https://example.salesforce.com/setup",
            before_title="Home",
            after_title="Home"
        )

        self.assertNotIn("no-screen-change", flags)


class TestSuspectFlagsInBoard(unittest.TestCase):
    """Test that suspect flags appear in board output."""

    def test_suspect_flags_appear_as_chips(self):
        """Suspect flags from StepOutcome appear as chips."""
        outcome = StepOutcome(
            status="PASS",
            reason="Clicked the button.",
            evidence_ids=("S001:after-state-dialog-opened",),
            suspect=("presence-only", "unexpected-change")
        )

        chips = _detect_chips(outcome)

        self.assertIn("presence-only", chips)
        self.assertIn("unexpected-change", chips)

    def test_no_chips_when_no_suspect_flags(self):
        """No suspect chips when outcome has no suspect flags."""
        outcome = StepOutcome(
            status="PASS",
            reason="Clicked Save.",
            evidence_ids=("S002:after-state-success-message-visible",),
            suspect=()
        )

        chips = _detect_chips(outcome)

        self.assertNotIn("presence-only", chips)
        self.assertNotIn("no-screen-change", chips)

    def test_suspect_and_already_set_chips_together(self):
        """Suspect flags and already-set can both appear as chips."""
        outcome = StepOutcome(
            status="PASS",
            reason="Already set.",
            evidence_ids=("S003:already-set:toggle",),
            suspect=("presence-only",)
        )

        chips = _detect_chips(outcome)

        self.assertIn("already-set", chips)
        self.assertIn("presence-only", chips)

    def test_no_screen_change_chip_appears(self):
        """No-screen-change flag appears as a chip."""
        outcome = StepOutcome(
            status="PASS",
            reason="Clicked the link.",
            evidence_ids=("S004:after-state-link-appeared",),
            suspect=("no-screen-change", "presence-only")
        )

        chips = _detect_chips(outcome)

        self.assertIn("no-screen-change", chips)
        self.assertIn("presence-only", chips)


if __name__ == "__main__":
    unittest.main()
