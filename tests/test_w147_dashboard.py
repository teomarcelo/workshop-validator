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

            # Create synthetic summary.json matching real structure
            summary = {
                "guide_name": "Example Workshop Guide",
                "counts": {
                    "PASS": 2,
                    "NEEDS_REVIEW": 1,
                    "UI_MISMATCH": 1,
                    "NOT_RUN": 1
                },
                "failures": [
                    {"step_id": "P001N003", "status": "UI_MISMATCH", "note": "Control not found"},
                    {"step_id": "P001N004", "status": "NEEDS_REVIEW", "note": "Manual check"}
                ],
                "stop_step": "P001N004"
            }
            (run_dir / "summary.json").write_text(json.dumps(summary))

            # Create synthetic board.json matching real structure
            board = {
                "sections": [
                    {
                        "exercises": [
                            {
                                "tasks": [
                                    {
                                        "steps": [
                                            {"step_id": "P001N001", "status": "PASS"},
                                            {"step_id": "P001N002", "status": "PASS"},
                                            {"step_id": "P001N003", "status": "UI_MISMATCH"},
                                            {"step_id": "P001N004", "status": "NEEDS_REVIEW"},
                                            {"step_id": "P001N005", "status": "NOT_RUN"}
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
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

            summary = {
                "guide_name": "Test Guide",
                "counts": {
                    "FUNCTIONAL_FAIL": 1,
                    "ENV_BLOCKED": 1,
                    "AUTH_BLOCKED": 1,
                    "NEEDS_REVIEW": 1,
                    "NOT_RUN": 1
                },
                "failures": [
                    {"step_id": "P001N001", "status": "FUNCTIONAL_FAIL", "note": "Error 1"},
                    {"step_id": "P001N002", "status": "ENV_BLOCKED", "note": "Error 2"},
                    {"step_id": "P001N003", "status": "AUTH_BLOCKED", "note": "Error 3"},
                    {"step_id": "P001N004", "status": "NEEDS_REVIEW", "note": "Error 4"}
                ],
                "stop_step": "P001N004"
            }
            (run_dir / "summary.json").write_text(json.dumps(summary))

            board = {
                "sections": [{
                    "exercises": [{
                        "tasks": [{
                            "steps": [
                                {"step_id": "P001N001", "status": "FUNCTIONAL_FAIL"},
                                {"step_id": "P001N002", "status": "ENV_BLOCKED"},
                                {"step_id": "P001N003", "status": "AUTH_BLOCKED"},
                                {"step_id": "P001N004", "status": "NEEDS_REVIEW"},
                                {"step_id": "P001N005", "status": "NOT_RUN"}
                            ]
                        }]
                    }]
                }]
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

            long_note = "B" * 100  # 100 character note
            summary = {
                "guide_name": "Test Guide",
                "counts": {"FUNCTIONAL_FAIL": 1, "NOT_RUN": 1},
                "failures": [
                    {"step_id": "P001N001", "status": "FUNCTIONAL_FAIL", "note": long_note}
                ],
                "stop_step": "P001N001"
            }
            (run_dir / "summary.json").write_text(json.dumps(summary))

            board = {
                "sections": [{
                    "exercises": [{
                        "tasks": [{
                            "steps": [
                                {"step_id": "P001N001", "status": "FUNCTIONAL_FAIL"},
                                {"step_id": "P001N002", "status": "NOT_RUN"}
                            ]
                        }]
                    }]
                }]
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

            summary = {
                "guide_name": "All Pass Guide",
                "counts": {"PASS": 3},
                "failures": [],
                "stop_step": "P001N003"
            }
            (run_dir / "summary.json").write_text(json.dumps(summary))

            board = {
                "sections": [{
                    "exercises": [{
                        "tasks": [{
                            "steps": [
                                {"step_id": "P001N001", "status": "PASS"},
                                {"step_id": "P001N002", "status": "PASS"},
                                {"step_id": "P001N003", "status": "PASS"}
                            ]
                        }]
                    }]
                }]
            }
            (run_dir / "board.json").write_text(json.dumps(board))

            result = team_dashboard.status_line(run_dir)

            self.assertIn("All Pass Guide", result)
            self.assertIn("P001N003", result)  # Furthest step
            self.assertIn("PASS 3", result)
            self.assertIn("FAIL 0", result)
            self.assertNotIn("|", result)  # No failures section

    def test_status_line_handles_missing_board(self):
        """status-line works without board.json, using stop_step."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            summary = {
                "guide_name": "Test Guide",
                "counts": {"PASS": 5, "NOT_RUN": 10},
                "failures": [],
                "stop_step": "P002N005"
            }
            (run_dir / "summary.json").write_text(json.dumps(summary))

            result = team_dashboard.status_line(run_dir)

            # Should still produce output with guide name
            self.assertIn("Test Guide", result)
            self.assertIn("P002N005", result)  # Uses stop_step as furthest

    def test_status_line_handles_missing_summary(self):
        """status-line returns error message when summary.json missing."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            result = team_dashboard.status_line(run_dir)

            self.assertIn("No summary.json found", result)

    def test_status_line_walks_real_shaped_board(self):
        """status-line correctly walks nested board structure."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)

            # Build a real-shaped board by walking nested sections
            summary = {
                "guide_name": "Multi-Section Guide",
                "counts": {"PASS": 23, "NEEDS_REVIEW": 3, "NOT_RUN": 926},
                "failures": [
                    {"step_id": "P003N007", "status": "NEEDS_REVIEW", "note": "Verify modal text"},
                    {"step_id": "P005N012", "status": "NEEDS_REVIEW", "note": "Check field value"},
                    {"step_id": "P008N003", "status": "NEEDS_REVIEW", "note": "Confirm button state"}
                ],
                "stop_step": "P008N003"
            }
            (run_dir / "summary.json").write_text(json.dumps(summary))

            # Real board has multiple sections, exercises, tasks
            board = {
                "sections": [
                    {
                        "exercises": [
                            {
                                "tasks": [
                                    {
                                        "steps": [
                                            {"step_id": "P001N001", "status": "PASS"},
                                            {"step_id": "P001N002", "status": "PASS"}
                                        ]
                                    },
                                    {
                                        "steps": [
                                            {"step_id": "P001N003", "status": "PASS"}
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "exercises": [
                            {
                                "tasks": [
                                    {
                                        "steps": [
                                            {"step_id": "P002N001", "status": "PASS"},
                                            {"step_id": "P002N002", "status": "PASS"}
                                        ]
                                    }
                                ]
                            },
                            {
                                "tasks": [
                                    {
                                        "steps": [
                                            {"step_id": "P003N001", "status": "PASS"},
                                            {"step_id": "P003N002", "status": "PASS"},
                                            {"step_id": "P003N007", "status": "NEEDS_REVIEW"}
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "exercises": [
                            {
                                "tasks": [
                                    {
                                        "steps": [
                                            {"step_id": "P008N001", "status": "PASS"},
                                            {"step_id": "P008N002", "status": "PASS"},
                                            {"step_id": "P008N003", "status": "NEEDS_REVIEW"},
                                            {"step_id": "P008N004", "status": "NOT_RUN"}
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
            (run_dir / "board.json").write_text(json.dumps(board))

            result = team_dashboard.status_line(run_dir)

            # Verify it reads all the data correctly
            self.assertIn("Multi-Section Guide", result)
            self.assertIn("P008N003", result)  # Furthest non-NOT_RUN step
            self.assertIn("PASS 23", result)
            self.assertIn("NEEDS_REVIEW 3", result)
            self.assertIn("NOT_RUN 926", result)
            # Check first failure is present
            self.assertIn("P003N007", result)


if __name__ == "__main__":
    unittest.main()
