"""Tests for W-147: team_dashboard status-line command."""

import json
import tempfile
import unittest
from pathlib import Path

from workshop_validator import team_dashboard


class StatusLineTests(unittest.TestCase):
    """Tests for status-line command."""

    def test_status_line_with_synthetic_data(self):
        """status-line generates expected output from synthetic summary and board."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            # Create synthetic summary.json
            summary = {
                "guide": "Example Workshop Guide",
                "total_steps": 10,
            }
            (run_dir / "summary.json").write_text(json.dumps(summary))

            # Create synthetic board.json
            board = {
                "steps": [
                    {"id": "P001N001", "status": "PASS", "note": ""},
                    {"id": "P001N002", "status": "PASS", "note": ""},
                    {"id": "P001N003", "status": "UI_MISMATCH", "note": "Control not found: Save Button"},
                    {"id": "P001N004", "status": "NEEDS_REVIEW", "note": "Manual verification required"},
                    {"id": "P001N005", "status": "NOT_RUN", "note": ""},
                ]
            }
            (run_dir / "board.json").write_text(json.dumps(board))

            # Generate status line
            result = team_dashboard.status_line(run_dir)

            # Verify structure
            self.assertIn("Example Workshop Guide", result)
            self.assertIn("P001N004", result)  # Furthest step
            self.assertIn("PASS 2", result)
            self.assertIn("NEEDS_REVIEW 1", result)
            self.assertIn("FAIL 1", result)  # UI_MISMATCH counts as FAIL
            self.assertIn("NOT_RUN 1", result)

            # Verify top failures are included
            self.assertIn("P001N003", result)
            self.assertIn("UI_MISMATCH", result)
            self.assertIn("P001N004", result)

    def test_status_line_with_multiple_failures(self):
        """status-line shows top 3 failures."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            summary = {"guide": "Test Guide"}
            (run_dir / "summary.json").write_text(json.dumps(summary))

            board = {
                "steps": [
                    {"id": "P001N001", "status": "FUNCTIONAL_FAIL", "note": "Error 1"},
                    {"id": "P001N002", "status": "ENV_BLOCKED", "note": "Error 2"},
                    {"id": "P001N003", "status": "AUTH_BLOCKED", "note": "Error 3"},
                    {"id": "P001N004", "status": "NEEDS_REVIEW", "note": "Error 4"},
                    {"id": "P001N005", "status": "NOT_RUN", "note": ""},
                ]
            }
            (run_dir / "board.json").write_text(json.dumps(board))

            result = team_dashboard.status_line(run_dir)

            # Should include first 3 failures
            self.assertIn("P001N001", result)
            self.assertIn("P001N002", result)
            self.assertIn("P001N003", result)

    def test_status_line_truncates_long_reasons(self):
        """status-line truncates reasons at 80 characters."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            summary = {"guide": "Test Guide"}
            (run_dir / "summary.json").write_text(json.dumps(summary))

            long_note = "B" * 100  # 100 character note
            board = {
                "steps": [
                    {"id": "P001N001", "status": "FUNCTIONAL_FAIL", "note": long_note},
                    {"id": "P001N002", "status": "NOT_RUN", "note": ""},
                ]
            }
            (run_dir / "board.json").write_text(json.dumps(board))

            result = team_dashboard.status_line(run_dir)

            # Note should be truncated to 80 chars
            # The format is "step_id: status: reason", so we check the reason part
            self.assertIn("P001N001", result)
            # The full note would have 100 B's, truncated should have exactly 80
            b_count = result.count("B")
            self.assertEqual(b_count, 80)

    def test_status_line_handles_all_pass(self):
        """status-line works when all steps pass."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            summary = {"guide": "All Pass Guide"}
            (run_dir / "summary.json").write_text(json.dumps(summary))

            board = {
                "steps": [
                    {"id": "P001N001", "status": "PASS", "note": ""},
                    {"id": "P001N002", "status": "PASS", "note": ""},
                    {"id": "P001N003", "status": "PASS", "note": ""},
                ]
            }
            (run_dir / "board.json").write_text(json.dumps(board))

            result = team_dashboard.status_line(run_dir)

            self.assertIn("All Pass Guide", result)
            self.assertIn("P001N003", result)  # Furthest step
            self.assertIn("PASS 3", result)
            self.assertIn("FAIL 0", result)
            self.assertNotIn("|", result)  # No failures section

    def test_status_line_handles_missing_board(self):
        """status-line works without board.json."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            summary = {"guide": "Test Guide"}
            (run_dir / "summary.json").write_text(json.dumps(summary))

            result = team_dashboard.status_line(run_dir)

            # Should still produce output with guide name
            self.assertIn("Test Guide", result)
            self.assertIn("None", result)  # No furthest step

    def test_status_line_handles_missing_summary(self):
        """status-line returns error message when summary.json missing."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            result = team_dashboard.status_line(run_dir)

            self.assertIn("No summary.json found", result)


if __name__ == "__main__":
    unittest.main()
