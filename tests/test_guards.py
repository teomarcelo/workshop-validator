import unittest

from workshop_validator.guards import approve_auto_layout_restore, checker


class GuardTests(unittest.TestCase):
    def test_checker_allows_a_mode_only_when_the_step_names_it(self):
        self.assertEqual(checker("Switch the canvas to Free-Form.", "Free-Form").verdict, "PASS")
        self.assertEqual(checker("Add a Screen Element named Input", "Free-Form").verdict, "REJECT")
        self.assertEqual(checker("Add a Screen Element named Input", "Screen").verdict, "PASS")

    def test_auto_layout_restore_needs_free_form_and_a_screen_step(self):
        self.assertEqual(approve_auto_layout_restore("Add a Screen Element", "Free-Form").verdict, "PASS")
        self.assertEqual(approve_auto_layout_restore("Add a Screen Element", "Auto-Layout").verdict, "REJECT")
        self.assertEqual(approve_auto_layout_restore("Click Save", "Free-Form").verdict, "REJECT")


if __name__ == "__main__":
    unittest.main()
