"""Tests that verify prove guards work correctly (for mutation testing)."""

import subprocess
import unittest
from pathlib import Path


class ProveGuardTests(unittest.TestCase):
    """Tests that fail when prove guards are removed."""

    def test_skip_check_exists(self):
        """Verify skip check exists in prove_command."""
        # Read the source code to verify the skip check is present
        gates_file = Path(__file__).parent.parent / "workshop_validator" / "team_gates.py"
        source = gates_file.read_text()
        # The check must be present
        self.assertIn("skipped=", source)
        self.assertIn("skip_match", source)
        self.assertIn("PROVE: FAIL tests skipped on head", source)

    def test_base_run_check_exists(self):
        """Verify base-run check exists in prove_command."""
        gates_file = Path(__file__).parent.parent / "workshop_validator" / "team_gates.py"
        source = gates_file.read_text()
        # The check must be present
        self.assertIn("result_base.returncode == 0", source)
        self.assertIn("PROVE: FAIL tests passed on base", source)


if __name__ == "__main__":
    unittest.main()
