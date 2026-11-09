"""Part A: Setup pipe paths are never frozen UI_MISMATCH, always NEEDS_REVIEW.
Part B: Walk Setup | A | B paths in the Setup tree. Synthetic pages only."""
import unittest

from tests.test_click_polling import PollClock, clicks, walk
from tests.test_guide_walk import Page, step
from workshop_validator.guide_inventory import GuideInventory
from workshop_validator.guide_walk import walk_guide
from workshop_validator.automate import outcome_of, gate_outcome_of


class PartACleanNameTests(unittest.TestCase):
    """Part A: A target containing "|" is never a clean control name.
    The step is frozen NEEDS_REVIEW with "could not read one control name" note."""

    def test_setup_pipe_path_5_tokens_needs_review_frozen(self):
        """Setup | X | Y (5 tokens) is NEEDS_REVIEW frozen, never UI_MISMATCH."""
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        pipe_step = step("s1", "Exercise 1: Setup", "Task 1: Config", "1", "Click Setup | X | Y")
        outcome = walk(page, pipe_step, PollClock(), page_wait_s=10)

        self.assertEqual(outcome.statuses["s1"], "NEEDS_REVIEW")
        self.assertIn("could not read one control name", outcome.notes["s1"])
        self.assertEqual(outcome.freezes.get("s1"), True)
        # Nothing was clicked
        self.assertEqual(clicks(page), [])

    def test_setup_pipe_path_6_tokens_needs_review_frozen(self):
        """Setup | X Z | Y (6 tokens) is NEEDS_REVIEW frozen, never UI_MISMATCH."""
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        pipe_step = step("s2", "Exercise 1: Setup", "Task 1: Config", "1", "Click Setup | X Z | Y")
        outcome = walk(page, pipe_step, PollClock(), page_wait_s=10)

        self.assertEqual(outcome.statuses["s2"], "NEEDS_REVIEW")
        self.assertIn("could not read one control name", outcome.notes["s2"])
        self.assertEqual(outcome.freezes.get("s2"), True)
        # Nothing was clicked
        self.assertEqual(clicks(page), [])

    def test_outcome_of_inconclusive_for_pipe_path(self):
        """outcome_of returns INCONCLUSIVE for a NEEDS_REVIEW step."""
        from workshop_validator.step_board import build_board
        from types import SimpleNamespace

        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        page.roles.add(("button", "Login"))

        # Add a passing step to ensure signed_in is True
        passing_step = step("s0", "Exercise 1: Setup", "Task 1: Config", "0", "Click Login")
        pipe_step = step("s1", "Exercise 1: Setup", "Task 1: Config", "1", "Click Setup | X | Y")

        from tests.test_guide_walk import Clock
        clock = Clock()
        outcome = walk_guide(
            page, GuideInventory(1, (passing_step, pipe_step), (), ()),
            checks=(), timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
            page_wait_s=10,
        )

        # Build a result object similar to what automate.py creates
        result = SimpleNamespace(
            signed_in=True,
            message="",
            report_steps=[
                SimpleNamespace(status="PASS", note="", title="Login", freeze=False),
                SimpleNamespace(status="NEEDS_REVIEW", note=outcome.notes["s1"], title="Setup | X | Y", freeze=True)
            ]
        )

        status, reason = outcome_of(result)
        self.assertEqual(status, "inconclusive")

    def test_gate_outcome_of_inconclusive_for_pipe_path(self):
        """gate_outcome_of returns inconclusive for a NEEDS_REVIEW step."""
        from types import SimpleNamespace

        # Build a result with a frozen NEEDS_REVIEW step
        result = SimpleNamespace(
            signed_in=True,
            message="",
            report_steps=[
                SimpleNamespace(status="PASS", note="", title="Login", freeze=False),
                SimpleNamespace(status="NEEDS_REVIEW", note="could not read one control name", title="Setup | X | Y", freeze=True)
            ]
        )

        gate_status = gate_outcome_of(result)
        self.assertEqual(gate_status, "inconclusive")


if __name__ == "__main__":
    unittest.main()
