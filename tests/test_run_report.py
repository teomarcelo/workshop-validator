"""Tests for HTML run report rendering and serialization."""

import json
import tempfile
import unittest
from pathlib import Path

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.step_board import (
    Board,
    StepOutcome,
    build_board,
    load_board,
    render_run_report,
    write_history_index,
    write_run_report,
)


def make_step(step_id, exercise, task, number, text, page=1):
    """Helper to create a GuideStep with minimal boilerplate."""
    return GuideStep(
        step_id, page, exercise, task, number, text,
        (90.0, 10.0, 200.0, 20.0), None, (),
    )


class RunReportTests(unittest.TestCase):
    def test_html_escaping_in_step_text(self):
        """Guide text with HTML tags must be escaped."""
        steps = (
            make_step("s1", "Exercise 1", "Task 1", "1", "<script>alert('xss')</script>"),
            make_step("s2", "Exercise 1", "Task 1", "2", "<img src=x onerror=alert(1)>"),
        )
        inventory = GuideInventory(1, steps, (), ())
        board = build_board(inventory, notice="Test run", run_id="test-001")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "inconclusive",
            "reason": "Test",
            "signed_in": True,
            "counts": {"NOT_RUN": 2},
        }

        html = render_run_report(board, summary=summary, guide_name="Test Guide")

        # Script and img tags should be escaped
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img src=", html)

    def test_pass_without_evidence_shown_as_fail(self):
        """A PASS without evidence_ids must display as FAIL with explanation."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Open the page"),)
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {"s1": StepOutcome("PASS")}
        board = build_board(inventory, outcomes=outcomes, notice="Test", run_id="test-002")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "failed",
            "reason": "Step failed",
            "counts": {"FAIL": 1},
        }

        html = render_run_report(board, summary=summary)

        # Should show as FAIL with reason
        self.assertIn("without evidence", html)
        self.assertIn("pill fail", html)
        self.assertNotIn("pill pass", html)

    def test_every_step_id_appears_as_anchor(self):
        """Each step's step_id should appear as an id anchor."""
        steps = (
            make_step("P001N001", "Exercise 1", "Task 1", "1", "First step"),
            make_step("P001N002", "Exercise 1", "Task 1", "2", "Second step"),
            make_step("P002N001", "Exercise 2", "Task 1", "1", "Third step", page=2),
        )
        inventory = GuideInventory(2, steps, (), ())
        board = build_board(inventory, notice="Test", run_id="test-003")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "inconclusive",
            "reason": "",
            "counts": {"NOT_RUN": 3},
        }

        html = render_run_report(board, summary=summary)

        self.assertIn("id='P001N001'", html)
        self.assertIn("id='P001N002'", html)
        self.assertIn("id='P002N001'", html)

    def test_failure_reason_shown_on_failing_step(self):
        """Failure reason should be displayed inline on the failing step."""
        steps = (
            make_step("s1", "Exercise 1", "Task 1", "1", "Open page"),
            make_step("s2", "Exercise 1", "Task 1", "2", "Save changes"),
        )
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {
            "s1": StepOutcome("PASS", evidence_ids=("ev1",)),
            "s2": StepOutcome("UI_MISMATCH", reason="Button not found on page."),
        }
        board = build_board(inventory, outcomes=outcomes, notice="Test", run_id="test-004")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "failed",
            "reason": "Step s2 failed",
            "counts": {"PASS": 1, "FAIL": 1},
            "first_failure": {"step_id": "s2", "page": 1, "status": "UI_MISMATCH"},
        }

        html = render_run_report(board, summary=summary)

        self.assertIn("Button not found on page.", html)
        self.assertIn("step-reason", html)

    def test_steps_after_failure_listed_as_not_run(self):
        """Steps that were not executed should remain visible as NOT RUN."""
        steps = (
            make_step("s1", "Exercise 1", "Task 1", "1", "First"),
            make_step("s2", "Exercise 1", "Task 1", "2", "Second"),
            make_step("s3", "Exercise 1", "Task 1", "3", "Third"),
        )
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {
            "s1": StepOutcome("PASS", evidence_ids=("ev1",)),
            "s2": StepOutcome("FUNCTIONAL_FAIL", reason="Check failed"),
        }
        board = build_board(inventory, outcomes=outcomes, notice="Test", run_id="test-005")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "failed",
            "reason": "",
            "counts": {"PASS": 1, "FAIL": 1, "NOT_RUN": 1},
        }

        html = render_run_report(board, summary=summary)

        # All three steps should be in the output
        self.assertIn("First", html)
        self.assertIn("Second", html)
        self.assertIn("Third", html)

        # s3 should be NOT RUN
        self.assertIn("pill not-run", html)

    def test_already_set_chip(self):
        """Steps with 'already-set' in evidence should show the chip."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Enable feature"),)
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {
            "s1": StepOutcome("PASS", evidence_ids=("already-set:feature-enabled",)),
        }
        board = build_board(inventory, outcomes=outcomes, notice="Test", run_id="test-006")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "success",
            "reason": "",
            "counts": {"PASS": 1},
        }

        html = render_run_report(board, summary=summary)

        self.assertIn("chip already-set", html)
        self.assertIn(
            "<span class='stat-value'>1</span><span class='stat-label'>Already set by org</span>", html
        )

    def test_renamed_chip(self):
        """Steps with 'reviewed alias' in note should show renamed chip."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Click the button"),)
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {
            "s1": StepOutcome(
                "PASS",
                evidence_ids=("ev1",),
                reason="This was a reviewed alias in the UI.",
            ),
        }
        board = build_board(inventory, outcomes=outcomes, notice="Test", run_id="test-007")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "success",
            "reason": "",
            "counts": {"PASS": 1, "renamed": 1},
        }

        html = render_run_report(board, summary=summary)

        self.assertIn("chip renamed", html)
        self.assertIn("Renamed in UI", html)

    def test_dark_mode_media_query_present(self):
        """CSS should include @media (prefers-color-scheme: dark)."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Test"),)
        inventory = GuideInventory(1, steps, (), ())
        board = build_board(inventory, notice="Test", run_id="test-008")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "inconclusive",
            "reason": "",
            "counts": {"NOT_RUN": 1},
        }

        html = render_run_report(board, summary=summary)

        self.assertIn("@media (prefers-color-scheme: dark)", html)

    def test_no_external_resources(self):
        """Report must not reference external HTTP/HTTPS resources."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Test step"),)
        inventory = GuideInventory(1, steps, (), ())
        board = build_board(inventory, notice="Test", run_id="test-009")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "inconclusive",
            "reason": "",
            "counts": {"NOT_RUN": 1},
        }

        html = render_run_report(board, summary=summary)

        # Should not contain external resource references
        self.assertNotIn('href="http://', html)
        self.assertNotIn('href="https://', html)
        self.assertNotIn('src="http://', html)
        self.assertNotIn('src="https://', html)

        # data: URIs are OK for inline content like favicons
        # file:// URIs are OK for local links

    def test_write_run_report_creates_0600_files(self):
        """write_run_report must create files with mode 0600."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Test"),)
        inventory = GuideInventory(1, steps, (), ())
        board = build_board(inventory, notice="Test", run_id="test-010")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "success",
            "reason": "",
            "counts": {"PASS": 1},
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "test-run"
            report_path = write_run_report(board, run_dir, summary=summary)

            # Check file permissions
            self.assertTrue(report_path.exists())
            self.assertEqual(report_path.stat().st_mode & 0o777, 0o600)

            board_json = run_dir / "board.json"
            self.assertTrue(board_json.exists())
            self.assertEqual(board_json.stat().st_mode & 0o777, 0o600)

            summary_json = run_dir / "summary.json"
            self.assertTrue(summary_json.exists())
            self.assertEqual(summary_json.stat().st_mode & 0o777, 0o600)

    def test_load_board_round_trips(self):
        """load_board should reconstruct a Board from board.json."""
        steps = (
            make_step("s1", "Exercise 1", "Task 1", "1", "First step"),
            make_step("s2", "Exercise 1", "Task 1", "2", "Second step"),
        )
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {
            "s1": StepOutcome("PASS", evidence_ids=("ev1",)),
            "s2": StepOutcome("FAIL", reason="Test failure"),
        }
        original_board = build_board(
            inventory,
            outcomes=outcomes,
            notice="Original test",
            run_id="test-011",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run"
            run_dir.mkdir()
            board_path = run_dir / "board.json"
            board_path.write_text(
                json.dumps(
                    {
                        "run_id": original_board.run_id,
                        "notice": original_board.notice,
                        "notice_tone": original_board.notice_tone,
                        "passed": original_board.passed,
                        "failed": original_board.failed,
                        "not_run": original_board.not_run,
                        "tasks_passed": original_board.tasks_passed,
                        "tasks_failed": original_board.tasks_failed,
                        "tasks_not_run": original_board.tasks_not_run,
                        "unmatched": original_board.unmatched,
                        "sections": [
                            {
                                "title": sec.title,
                                "subtitle": sec.subtitle,
                                "failed_tasks": sec.failed_tasks,
                                "tasks": [
                                    {
                                        "lead": task.lead,
                                        "label": task.label,
                                        "css": task.css,
                                        "steps": [
                                            {
                                                "number": step.number,
                                                "title": step.title,
                                                "label": step.label,
                                                "css": step.css,
                                                "reason": step.reason,
                                                "step_id": step.step_id,
                                                "status": step.status,
                                                "chips": list(step.chips),
                                            }
                                            for step in task.steps
                                        ],
                                    }
                                    for task in sec.tasks
                                ],
                            }
                            for sec in original_board.sections
                        ],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            loaded_board = load_board(board_path)

            # Verify key attributes
            self.assertEqual(loaded_board.run_id, original_board.run_id)
            self.assertEqual(loaded_board.notice, original_board.notice)
            self.assertEqual(loaded_board.passed, original_board.passed)
            self.assertEqual(loaded_board.failed, original_board.failed)
            self.assertEqual(len(loaded_board.sections), len(original_board.sections))

            # Verify a step detail
            first_step = loaded_board.sections[0].tasks[0].steps[0]
            self.assertEqual(first_step.step_id, "s1")
            self.assertEqual(first_step.label, "PASS")

    def test_history_index_lists_runs_newest_first(self):
        """write_history_index should sort runs by run_id descending."""
        with tempfile.TemporaryDirectory() as tmpdir:
            private_root = Path(tmpdir)
            reports_dir = private_root / "reports"
            reports_dir.mkdir(parents=True)

            history = [
                {
                    "run_id": "2025-01-10T10:00:00",
                    "guide": "Guide A",
                    "org": 1,
                    "orgs": 3,
                    "outcome": "success",
                    "reason": "",
                    "counts": {"PASS": 10},
                    "run_dir": str(reports_dir / "run1"),
                },
                {
                    "run_id": "2025-01-15T15:30:00",
                    "guide": "Guide B",
                    "org": 2,
                    "orgs": 3,
                    "outcome": "failed",
                    "reason": "Step failed",
                    "counts": {"PASS": 5, "FAIL": 2},
                    "run_dir": str(reports_dir / "run2"),
                },
                {
                    "run_id": "2025-01-12T12:00:00",
                    "guide": "Guide A",
                    "org": 2,
                    "orgs": 3,
                    "outcome": "inconclusive",
                    "reason": "Timed out",
                    "counts": {"PASS": 3, "NOT_RUN": 5},
                    "run_dir": str(reports_dir / "run3"),
                },
            ]

            history_path = reports_dir / "history.json"
            history_path.write_text(json.dumps(history), encoding="utf-8")

            # Create dummy report files so links work
            for entry in history:
                run_dir = Path(entry["run_dir"])
                run_dir.mkdir(parents=True, exist_ok=True)
                (run_dir / "report.html").write_text("test", encoding="utf-8")

            index_path = write_history_index(private_root)

            self.assertTrue(index_path.exists())
            html = index_path.read_text(encoding="utf-8")

            # Check sorting (newest first)
            pos_2025_01_15 = html.index("2025-01-15T15:30:00")
            pos_2025_01_12 = html.index("2025-01-12T12:00:00")
            pos_2025_01_10 = html.index("2025-01-10T10:00:00")

            self.assertLess(pos_2025_01_15, pos_2025_01_12)
            self.assertLess(pos_2025_01_12, pos_2025_01_10)

            # Check pills
            self.assertIn("pill pass", html)
            self.assertIn("VERIFIED", html)
            self.assertIn("pill fail", html)
            self.assertIn("FAILED", html)
            self.assertIn("pill not-run", html)
            self.assertIn("INCOMPLETE", html)

    def test_needs_review_shown_with_distinct_pill(self):
        """NEEDS_REVIEW status should show 'Needs review' pill, not FAIL."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Review this"),)
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {"s1": StepOutcome("NEEDS_REVIEW", reason="Manual review needed")}
        board = build_board(inventory, outcomes=outcomes, notice="Test", run_id="test-012")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "failed",
            "reason": "",
            "counts": {"NEEDS_REVIEW": 1},
        }

        html = render_run_report(board, summary=summary)

        # In the run report, NEEDS_REVIEW should show as "Needs review" pill
        self.assertIn("pill needs-review", html)
        self.assertIn("Needs review", html)

    def test_sections_and_tasks_collapsible(self):
        """Report should use <details> for collapsible sections/tasks."""
        steps = (
            make_step("s1", "Exercise 1", "Task 1", "1", "Step 1"),
            make_step("s2", "Exercise 1", "Task 1", "2", "Step 2"),
        )
        inventory = GuideInventory(1, steps, (), ())
        board = build_board(inventory, notice="Test", run_id="test-013")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "inconclusive",
            "reason": "",
            "counts": {"NOT_RUN": 2},
        }

        html = render_run_report(board, summary=summary)

        # Should use details/summary for collapsing
        self.assertIn("<details", html)
        self.assertIn("<summary>", html)
        self.assertIn("</details>", html)

    def test_first_failure_card_links_to_step(self):
        """First failure card should link to the failing step's anchor."""
        steps = (
            make_step("s1", "Exercise 1", "Task 1", "1", "Pass step"),
            make_step("s2", "Exercise 1", "Task 1", "2", "Fail step"),
        )
        inventory = GuideInventory(1, steps, (), ())
        outcomes = {
            "s1": StepOutcome("PASS", evidence_ids=("ev1",)),
            "s2": StepOutcome("UI_MISMATCH", reason="Failed"),
        }
        board = build_board(inventory, outcomes=outcomes, notice="Test", run_id="test-014")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "failed",
            "reason": "Step failed",
            "counts": {"PASS": 1, "FAIL": 1},
            "first_failure": {"step_id": "s2", "page": 1, "status": "UI_MISMATCH"},
        }

        html = render_run_report(board, summary=summary)

        self.assertIn("First Failure", html)
        self.assertIn("href='#s2'", html)

    def test_team_activity_timeline_rendered(self):
        """Team log entries should appear in a timeline."""
        steps = (make_step("s1", "Exercise 1", "Task 1", "1", "Test"),)
        inventory = GuideInventory(1, steps, (), ())
        board = build_board(inventory, notice="Test", run_id="test-015")

        summary = {
            "org": 1,
            "orgs": 1,
            "outcome": "success",
            "reason": "",
            "counts": {"PASS": 1},
        }

        team_log = [
            {
                "time": "10:00:00",
                "agent": "AgentA",
                "action": "Started run",
                "detail": "Opened browser",
            },
            {
                "time": "10:05:00",
                "agent": "AgentB",
                "action": "Completed step",
                "detail": "Step s1 passed",
                "link": "https://example.com/log",
            },
        ]

        html = render_run_report(board, summary=summary, team_log=team_log)

        self.assertIn("Team Activity", html)
        self.assertIn("timeline", html)
        self.assertIn("AgentA", html)
        self.assertIn("Started run", html)
        self.assertIn("AgentB", html)
        self.assertIn("Completed step", html)



class SuspectChipReportTests(unittest.TestCase):
    """Run report, live report and history page carry the suspect chip and its theme CSS."""

    SENTINELS = {
        "light": {"chip_suspect_bg": "#a1b2c3", "chip_suspect": "#c3b2a1"},
        "dark": {"chip_suspect_bg": "#d4e5f6", "chip_suspect": "#f6e5d4"},
    }
    CHIP_LABELS = {
        "presence-only": "Weak evidence: element only",
        "no-screen-change": "Screen did not change",
    }

    def _patched(self):
        """Replace existing theme values only. Fails when theme.py lacks the keys."""
        from contextlib import ExitStack
        from unittest.mock import patch
        from workshop_validator import theme

        stack = ExitStack()
        for mode, colors in (("light", theme.COLORS_LIGHT), ("dark", theme.COLORS_DARK)):
            for key in self.SENTINELS[mode]:
                self.assertIn(key, colors, f"theme COLORS_{mode.upper()} must define {key}")
            stack.enter_context(patch.dict(colors, self.SENTINELS[mode]))
        return stack

    def _assert_suspect_css(self, html):
        import re

        light = self.SENTINELS["light"]
        dark = self.SENTINELS["dark"]
        rules = re.findall(r"\.chip\.suspect\s*\{([^}]*)\}", html)
        light_rule = (
            f"background: {light['chip_suspect_bg']};",
            f"color: {light['chip_suspect']};",
        )
        dark_rule = (
            f"background: {dark['chip_suspect_bg']};",
            f"color: {dark['chip_suspect']};",
        )
        self.assertTrue(
            any(all(part in rule for part in light_rule) for rule in rules),
            "light .chip.suspect rule must use COLORS_LIGHT tokens",
        )
        self.assertTrue(
            any(all(part in rule for part in dark_rule) for rule in rules),
            "dark .chip.suspect rule must use COLORS_DARK tokens",
        )
        dark_block = html[html.index("prefers-color-scheme: dark"):]
        self.assertRegex(
            dark_block,
            r"\.chip\.suspect\s*\{[^}]*" + re.escape(dark["chip_suspect_bg"]),
            "dark rule must sit inside the dark color-scheme block",
        )

    def _board(self):
        steps = tuple(
            make_step(f"s{i}", "Exercise 1", "Task 1", str(i), f"Synthetic step {i}")
            for i in range(1, len(self.CHIP_LABELS) + 1)
        )
        outcomes = {
            f"s{i}": StepOutcome("PASS", evidence_ids=(f"ev-{i}",), suspect=(flag,))
            for i, flag in enumerate(self.CHIP_LABELS, start=1)
        }
        inventory = GuideInventory(1, steps, (), ())
        return build_board(inventory, outcomes=outcomes, notice="Synthetic", run_id="suspect-001")

    def test_run_report_shows_each_suspect_chip_with_theme_css(self):
        summary = {
            "org": 1, "orgs": 1, "outcome": "success", "reason": "Synthetic",
            "signed_in": True, "counts": {"PASS": len(self.CHIP_LABELS)},
        }
        with self._patched():
            html = render_run_report(self._board(), summary=summary, guide_name="Synthetic Guide")
        for flag, label in self.CHIP_LABELS.items():
            with self.subTest(flag=flag):
                self.assertIn(f">{label}</span>", html)
        self.assertEqual(html.count("<span class='chip suspect'"), len(self.CHIP_LABELS))
        self.assertIn("passes need a check", html)
        self._assert_suspect_css(html)

    def test_live_report_carries_suspect_chip_css(self):
        from workshop_validator.step_board import render_live_report

        with self._patched():
            html = render_live_report(
                guide_name="Synthetic Guide", run_id="r", org=1, orgs=1, lines=["Step 1"],
            )
        self._assert_suspect_css(html)

    def test_history_index_carries_suspect_chip_css(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            private_root = Path(tmpdir)
            (private_root / "reports").mkdir(parents=True)
            (private_root / "reports" / "history.json").write_text("[]", encoding="utf-8")
            with self._patched():
                index_path = write_history_index(private_root)
            html = index_path.read_text(encoding="utf-8")
        self._assert_suspect_css(html)


if __name__ == "__main__":
    unittest.main()
