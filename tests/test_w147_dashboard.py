"""Tests for W-147: the one-line status from a run's summary.json and board.json.

Fixtures use the real shapes (board: sections > tasks > steps; summary: guide_name,
counts, failures, stop_step) with synthetic ids and notes.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from workshop_validator import team_dashboard


def _board(*sections):
    """sections: [[(step_id, status), ...] per task, ...] per section."""
    return {"run_id": "r1", "sections": [
        {"title": f"Section {n}", "subtitle": "", "failed_tasks": 0,
         "tasks": [{"label": f"Task {t}", "lead": "", "css": "", "steps": [
             {"step_id": sid, "status": status, "title": "Synthetic step", "reason": "",
              "number": "1", "chips": [], "css": "", "guide_note": "", "label": "", "snapshot": ""}
             for sid, status in task]} for t, task in enumerate(tasks, 1)]}
        for n, tasks in enumerate(sections, 1)]}


BOARD = _board(
    [[("P001N001", "PASS"), ("P001N002", "UI_MISMATCH")]],
    [[("P002N001", "PASS"), ("P002N002", "NEEDS_REVIEW")], [("P002N003", "NOT_RUN"), ("P002N004", "NOT_RUN")]],
)
SUMMARY = {
    "guide_name": "Synthetic Guide",
    "counts": {"PASS": 2, "NEEDS_REVIEW": 1, "UI_MISMATCH": 1, "FUNCTIONAL_FAIL": 1,
               "ENV_BLOCKED": 2, "AUTH_BLOCKED": 1, "NOT_RUN": 4},
    "failures": [
        {"step_id": "P001N002", "status": "UI_MISMATCH", "note": "Alpha control missing"},
        {"step_id": "P002N002", "status": "NEEDS_REVIEW", "note": "Beta needs a look"},
        {"step_id": "P003N001", "status": "ENV_BLOCKED", "note": "Gamma blocked"},
        {"step_id": "P003N002", "status": "FUNCTIONAL_FAIL", "note": "Delta not shown"},
    ],
    "stop_step": "P001N002",
    "outcome": "inconclusive",
}


class StatusLineTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_dir(self, summary=SUMMARY, board=BOARD, org="org-a", name="20000101T000000Z-x"):
        path = self.root / "runs" / org / name
        path.mkdir(parents=True)
        if summary is not None:
            (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        if board is not None:
            (path / "board.json").write_text(json.dumps(board), encoding="utf-8")
        return path

    def test_full_line_uses_board_order_for_furthest_step(self):
        line = team_dashboard.status_line(self.run_dir())
        self.assertEqual(line, "Synthetic Guide; P002N002; "
                               "PASS 2 / NEEDS_REVIEW 1 / FAIL 2 / BLOCKED 3 / NOT_RUN 4; "
                               "P001N002: UI_MISMATCH: Alpha control missing | "
                               "P002N002: NEEDS_REVIEW: Beta needs a look | "
                               "P003N001: ENV_BLOCKED: Gamma blocked")

    def test_blocked_counts_env_and_auth_not_fail(self):
        summary = {**SUMMARY, "counts": {"PASS": 1, "ENV_BLOCKED": 3, "AUTH_BLOCKED": 2}, "failures": []}
        line = team_dashboard.status_line(self.run_dir(summary=summary))
        self.assertEqual(line, "Synthetic Guide; P002N002; "
                               "PASS 1 / NEEDS_REVIEW 0 / FAIL 0 / BLOCKED 5 / NOT_RUN 0")

    def test_newline_and_control_characters_are_removed(self):
        summary = {**SUMMARY, "guide_name": "Synthetic\nGuide\x1b",
                   "failures": [{"step_id": "P001N002", "status": "UI_MISMATCH",
                                 "note": "first line\nsecond\r\nthird fourth\x07"}]}
        line = team_dashboard.status_line(self.run_dir(summary=summary))
        self.assertEqual(line, "Synthetic Guide; P002N002; "
                               "PASS 2 / NEEDS_REVIEW 1 / FAIL 2 / BLOCKED 3 / NOT_RUN 4; "
                               "P001N002: UI_MISMATCH: first line second third fourth")
        self.assertEqual(len(line.splitlines()), 1)

    def test_reason_is_cut_to_80_characters(self):
        summary = {**SUMMARY, "failures": [{"step_id": "P001N002", "status": "UI_MISMATCH", "note": "x" * 100}]}
        line = team_dashboard.status_line(self.run_dir(summary=summary))
        self.assertEqual(line, "Synthetic Guide; P002N002; "
                               "PASS 2 / NEEDS_REVIEW 1 / FAIL 2 / BLOCKED 3 / NOT_RUN 4; "
                               "P001N002: UI_MISMATCH: " + "x" * 80)

    def test_falls_back_to_stop_step_without_board(self):
        line = team_dashboard.status_line(self.run_dir(board=None))
        self.assertTrue(line.startswith("Synthetic Guide; P001N002; PASS 2 /"), line)

    def test_falls_back_to_stop_step_when_board_is_all_not_run(self):
        board = _board([[("P001N001", "NOT_RUN"), ("P001N002", "NOT_RUN")]])
        line = team_dashboard.status_line(self.run_dir(board=board))
        self.assertTrue(line.startswith("Synthetic Guide; P001N002; PASS 2 /"), line)

    def test_bad_shapes_never_raise(self):
        summary = {"guide_name": 7, "counts": {"PASS": "two", "NOT_RUN": True},
                   "failures": ["text", {"step_id": None}], "stop_step": None}
        line = team_dashboard.status_line(self.run_dir(summary=summary, board={"sections": "x"}))
        self.assertEqual(line, "7; none; PASS 0 / NEEDS_REVIEW 0 / FAIL 0 / BLOCKED 0 / NOT_RUN 0; : : ")

    def test_newest_run_under_private_root(self):
        old = self.run_dir(summary={**SUMMARY, "guide_name": "Old Guide"}, name="1")
        new = self.run_dir(summary={**SUMMARY, "guide_name": "New Guide"}, org="org-b", name="2")
        os.utime(old / "summary.json", (1000, 1000))
        os.utime(new / "summary.json", (2000, 2000))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = team_dashboard.main(["--private-root", str(self.root), "--status-line"])
        self.assertEqual(code, 0)
        self.assertTrue(out.getvalue().startswith("New Guide; P002N002;"), out.getvalue())

    def test_no_runs_exits_1(self):
        for setup in (lambda: None, lambda: self.run_dir(summary=None)):
            with self.subTest(setup=setup):
                setup()
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = team_dashboard.main(["--private-root", str(self.root), "--status-line"])
                self.assertEqual((code, out.getvalue()), (1, "No runs found\n"))

    def test_run_dir_with_bad_summary_exits_1(self):
        path = self.run_dir(summary=None)
        (path / "summary.json").write_text("{not json", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = team_dashboard.main(["--status-line", "--run-dir", str(path)])
        self.assertEqual((code, out.getvalue()), (1, "No runs found\n"))


if __name__ == "__main__":
    unittest.main()
