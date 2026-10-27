import json
import tempfile
import unittest
from pathlib import Path

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.report import ReportStep
from workshop_validator.step_board import (
    StepOutcome,
    build_board,
    outcomes_from_report_steps,
    render_activity,
    render_board,
    write_results_pdf,
)


def step(step_id, exercise, task, number, text, page=1):
    return GuideStep(
        step_id, page, exercise, task, number, text,
        (90.0, 10.0, 200.0, 20.0), None, (),
    )


def html_for(steps, outcomes=None, notice="Checked."):
    inventory = GuideInventory(1, tuple(steps), (), ())
    board = build_board(
        inventory,
        outcomes=outcomes,
        notice=notice,
        run_id="board-test",
    )
    return render_board(board), board


class StepBoardTests(unittest.TestCase):
    def test_setup_comes_first_and_exercises_keep_printed_order(self):
        page, _board = html_for((
            step("g", None, None, "1", "Loose instruction"),
            step("e2", "Exercise 2: Later work", "Task 1: Second", "1", "Do the later step"),
            step("s", "Set Up Your Instance", "Task 1: Prepare the org", "1", "Open the setup page"),
        ))
        self.assertLess(page.index("<h2>Setup</h2>"), page.index("<h2>Guide</h2>"))
        self.assertLess(page.index("<h2>Guide</h2>"), page.index("<h2>Exercise 2</h2>"))
        self.assertIn("Set Up Your Instance", page)
        self.assertIn("Later work", page)
        self.assertIn("Setup, Task 1: Prepare the org:", page)
        self.assertIn(">NOT RUN</span>", page)
        self.assertNotIn("class='badge pass'", page)
        self.assertNotIn("class='badge fail'", page)

    def test_failure_stays_on_its_step_and_later_steps_remain(self):
        steps = (
            step("a", "Exercise 1-2: Check the agent", "Task 3: Test your agent",
                 "1", "Select Preview"),
            step("b", "Exercise 1-2: Check the agent", "Task 3: Test your agent",
                 "2", "Welcome message is shown"),
            step("c", "Exercise 1-2: Check the agent", "Task 3: Test your agent",
                 "3", "Reply with Hello there!"),
        )
        outcomes = outcomes_from_report_steps((
            ReportStep("c", 1, "Reply", "PASS", ("evidence-3",)),
            ReportStep("a", 1, "Preview", "PASS", ("evidence-1",)),
            ReportStep(
                "b", 1, "Welcome", "FUNCTIONAL_FAIL", (),
                "Live Test shows Let's chat, not the saved welcome.",
            ),
        ))
        page, board = html_for(steps, outcomes)
        self.assertIn("Exercise 1-2, Task 3: Test your agent:", page)
        self.assertIn("<article class='task fail' data-status='FAIL'>", page)
        self.assertIn("<h2>Exercise 1-2</h2>", page)
        self.assertIn("1 task failed", page)
        welcome = page.index("Welcome message is shown")
        reason = page.index("Live Test shows Let&#x27;s chat, not the saved welcome.")
        reply = page.index("Reply with Hello there!")
        self.assertLess(page.index("Select Preview"), welcome)
        self.assertLess(welcome, reason)
        self.assertLess(reason, reply)
        self.assertIn("<li class='pass' data-status='PASS'>", page)
        self.assertIn("<li class='fail' data-status='FAIL'>", page)
        self.assertEqual(board.failed, 1)
        self.assertEqual(board.passed, 2)
        self.assertEqual(board.tasks_failed, 1)
        self.assertEqual(board.tasks_passed, 0)

    def test_not_run_can_show_why_it_was_not_failed(self):
        page, board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Reply with Hello there!"),),
            {"a": StepOutcome(
                "NOT_RUN",
                reason="The page was still loading after about 90 seconds, so this step was not marked failed.",
            )},
        )
        self.assertIn("still loading", page)
        self.assertNotIn("class='badge fail'", page)
        self.assertEqual(board.failed, 0)
        self.assertEqual(board.not_run, 1)

    def test_not_run_step_keeps_the_task_from_passing(self):
        page, board = html_for(
            (
                step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),
                step("b", "Exercise 1", "Task 1: Start", "2", "Save the page"),
            ),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",))},
        )
        self.assertIn("<article class='task not-run' data-status='NOT RUN'>", page)
        self.assertIn("<li class='pass' data-status='PASS'>", page)
        self.assertIn("<li class='not-run' data-status='NOT RUN'>", page)
        self.assertEqual(board.tasks_passed, 0)
        self.assertEqual(board.not_run, 1)

    def test_pass_without_evidence_is_a_failure(self):
        page, board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),),
            {"a": StepOutcome("PASS")},
        )
        self.assertIn("without evidence", page)
        self.assertNotIn("class='badge pass'", page)
        self.assertEqual(board.passed, 0)
        self.assertEqual(board.failed, 1)

    def test_running_step_is_not_a_failure(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),),
            {"a": StepOutcome("RUNNING")},
        )
        self.assertIn("still running", page)
        self.assertIn("data-status='NOT RUN'", page)
        self.assertNotIn("class='badge fail'", page)

    def test_environment_block_is_visible_and_does_not_blame_the_guide(self):
        page, board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),),
            {"a": StepOutcome("ENV_BLOCKED")},
        )
        self.assertIn("not evidence the guide is wrong", page)
        self.assertEqual(board.tasks_failed, 1)
        self.assertEqual(board.passed, 0)

    def test_unmatched_pass_is_not_applied_to_another_step(self):
        page, board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),),
            {"other": StepOutcome("PASS", evidence_ids=("evidence-1",))},
        )
        self.assertEqual(board.unmatched, 1)
        self.assertIn("left off this board", page)
        self.assertNotIn("class='badge pass'", page)
        self.assertEqual(board.passed, 0)

    def test_guide_text_is_escaped(self):
        page, _board = html_for((
            step("a", "Exercise 1", "Task 1: <script>", "1", "<img src=x onerror=alert(1)>"),
        ))
        self.assertNotIn("<img src=", page)
        self.assertNotIn("<script>", page.split("<style>", 1)[0])
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertIn("&lt;script&gt;", page)

    def test_lettered_step_keeps_its_marker(self):
        page, _board = html_for((
            step("a", "Exercise 1", "Task 1: Fields", "a", "Open the tab"),
        ))
        self.assertIn(">a.</span>", page)

    def test_all_passing_steps_make_the_task_pass(self):
        page, board = html_for(
            (
                step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),
                step("b", "Exercise 1", "Task 1: Start", "2", "Save the page"),
            ),
            {
                "a": StepOutcome("PASS", evidence_ids=("evidence-1",)),
                "b": StepOutcome("PASS", evidence_ids=("evidence-2",)),
            },
        )
        self.assertIn("<article class='task pass' data-status='PASS'>", page)
        self.assertEqual(board.tasks_passed, 1)
        self.assertEqual(board.failed, 0)
        self.assertEqual(board.notice_tone, "ok")

    def test_more_than_eighty_progress_lines_are_kept_and_lead_with_the_current_step(self):
        steps = tuple(
            f"Step {number} of 120: Action {number}" for number in range(1, 91)
        )
        checks = (
            "Checked at 30 seconds. The page is still loading.",
            "Checked at 60 seconds. Still looking for Action 90.",
            "Checking the page for Action 90. 30s",
        )
        freeze = "The run froze after 5 minutes. Action 90 was not on the page."
        lines = steps + checks + (freeze,)
        running = render_activity(steps + checks, live=True)
        self.assertNotIn("class='freeze'", running)
        self.assertEqual(running.count(" of 120: "), 90)
        self._assert_progress_lead(running, freeze=False)

        inventory = GuideInventory(
            1,
            (step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),),
            (),
            (),
        )
        board = build_board(inventory, notice="Checked.", run_id="board-test")
        page = render_board(board, activity=lines)
        self.assertEqual(page.count(" of 120: "), 90)
        self.assertEqual(page.count("The run froze after 5 minutes."), 1)
        self.assertIn("background: #e7f5ef", page)
        self.assertLess(page.index("class='freeze'"), page.index(">Passed<"))
        self._assert_progress_lead(page, freeze=True)

        frozen_step = build_board(
            inventory,
            outcomes={"a": StepOutcome(
                "UI_MISMATCH",
                reason="Could not find Open the page. The page showed: Home.",
            )},
            notice="Checked.",
            run_id="board-test",
        )
        frozen_page = render_board(frozen_step, activity=steps + checks)
        self.assertEqual(frozen_page.count(" of 120: "), 90)
        self.assertLess(
            frozen_page.index("Could not find Open the page. The page showed: Home."),
            frozen_page.index("class='current-step'"),
        )
        self.assertLess(frozen_page.index("class='freeze'"), frozen_page.index(">Passed<"))
        self._assert_progress_lead(frozen_page, freeze=False)

        quiet = render_activity(steps, live=False)
        self.assertEqual(quiet.count(" of 120: "), 90)
        self.assertLess(
            quiet.index("Step 1 of 120: Action 1"),
            quiet.index("Step 90 of 120: Action 90"),
        )
        self.assertNotIn("class='current-step'", quiet)
        self.assertIn("What this run did", quiet)

    def _assert_progress_lead(self, page: str, *, freeze: bool) -> None:
        self.assertEqual(page.count("Step 1 of 120: Action 1"), 1)
        self.assertEqual(page.count("Step 13 of 120: Action 13"), 1)
        self.assertEqual(page.count("Step 90 of 120: Action 90"), 1)
        self.assertLess(page.index("class='current-step'"), page.index(">Checks<"))
        self.assertLess(page.index("Step 90 of 120: Action 90"), page.index(">Checks<"))
        self.assertLess(page.index(">Checks<"), page.index("class='check'"))
        self.assertLess(page.index("class='check'"), page.index("Checked at 30 seconds"))
        self.assertLess(
            page.index("Checked at 30 seconds"),
            page.index("Checked at 60 seconds"),
        )
        self.assertLess(
            page.index("Checked at 60 seconds"),
            page.index("Checking the page for Action 90. 30s"),
        )
        self.assertLess(
            page.index("Checking the page for Action 90. 30s"),
            page.index(">From the start<"),
        )
        self.assertLess(page.index(">From the start<"), page.index("Step 1 of 120: Action 1"))
        self.assertLess(
            page.index("Step 1 of 120: Action 1"),
            page.index("Step 13 of 120: Action 13"),
        )
        self.assertLess(
            page.index("Step 90 of 120: Action 90"),
            page.index(">From the start<"),
        )
        if freeze:
            self.assertLess(page.index("class='freeze'"), page.index("The run froze"))
            self.assertLess(page.index("The run froze"), page.index("class='current-step'"))


    def test_results_pdf_lists_every_step_status(self):
        import fitz

        _page, board = html_for(
            (
                step("s", "Set Up Your Instance", "Task 1: Prepare the org", "1", "Open the setup page"),
                step("a", "Exercise 1-2: Check the agent", "Task 3: Test your agent", "2", "Welcome message is shown"),
            ),
            outcomes={
                "s": StepOutcome("PASS", evidence_ids=("s:opened",)),
                "a": StepOutcome("UI_MISMATCH", reason="Live Test shows Let's chat."),
            },
            notice="Checked the guide.",
        )
        with tempfile.TemporaryDirectory() as folder:
            dest = Path(folder) / "results.pdf"
            write_results_pdf(board, dest)
            self.assertTrue(dest.read_bytes().startswith(b"%PDF-"))
            text = "\n".join(page.get_text() for page in fitz.open(dest))
        self.assertIn("PASS 1. FAIL 1. NOT RUN 0.", text)
        self.assertIn("Open the setup page: PASS", text)
        self.assertIn("Welcome message is shown: FAIL", text)
        self.assertIn("Live Test shows Let's chat.", text)

    def test_presence_only_chip_renders(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("presence-only",))},
        )
        self.assertIn("Weak evidence: element only", page)
        self.assertIn("class='chip suspect'", page)
        self.assertIn("Evidence based only on element presence", page)

    def test_no_screen_change_chip_renders(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Open the page"),),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("no-screen-change",))},
        )
        self.assertIn("Screen did not change", page)
        self.assertIn("class='chip suspect'", page)
        self.assertIn("URL, page title, and headings did not change", page)

    def test_suspect_pass_counter_shows_for_flagged_passes(self):
        page, _board = html_for(
            (
                step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),
                step("b", "Exercise 1", "Task 1: Start", "2", "Open the page"),
            ),
            {
                "a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("presence-only",)),
                "b": StepOutcome("PASS", evidence_ids=("evidence-2",), suspect=("no-screen-change",)),
            },
        )
        self.assertIn("2", page)
        self.assertIn("passes need a check", page)
        self.assertIn("class='metric pending'", page)

    def test_suspect_pass_counter_singular_form(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("presence-only",))},
        )
        self.assertIn("1", page)
        self.assertIn("pass needs a check", page)
        self.assertNotIn("passes need", page)

    def test_suspect_pass_counter_hidden_when_no_flags(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",))},
        )
        self.assertNotIn("pass needs a check", page)
        self.assertNotIn("passes need a check", page)

    def test_suspect_chip_uses_theme_tokens(self):
        """Verify that suspect chip CSS reads colors from theme.py, not hardcoded values."""
        from unittest.mock import patch
        from workshop_validator import theme

        # Replace existing values only. patch.dict would silently add a
        # missing key, so first require the keys to exist in theme.py.
        sentinel_bg = "#ff00ff"  # Magenta
        sentinel_fg = "#00ff00"  # Green
        for key in ("chip_suspect_bg", "chip_suspect"):
            self.assertIn(key, theme.COLORS_LIGHT, f"theme.COLORS_LIGHT must define {key}")

        with patch.dict(theme.COLORS_LIGHT, {"chip_suspect_bg": sentinel_bg, "chip_suspect": sentinel_fg}):
            page, _board = html_for(
                (step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),),
                {"a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("presence-only",))},
            )

        # Verify sentinel colors appear in the CSS
        self.assertIn(sentinel_bg, page, "Suspect chip background color should come from theme.COLORS_LIGHT")
        self.assertIn(sentinel_fg, page, "Suspect chip foreground color should come from theme.COLORS_LIGHT")

        # Verify the old hardcoded colors are not present
        self.assertNotIn("#fff4e0", page, "Should not have hardcoded background color")
        self.assertNotIn("#9a6700", page, "Should not have hardcoded foreground color")

    def test_weak_evidence_chip_renders(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("weak-evidence",))},
        )
        self.assertIn("Weak evidence", page)
        self.assertIn("class='chip suspect'", page)
        self.assertIn("Evidence is based only on a click action", page)

    def test_url_only_evidence_chip_renders(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Navigate to page"),),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("url-only-evidence",))},
        )
        self.assertIn("Evidence is the URL only", page)
        self.assertIn("class='chip suspect'", page)

    def test_unexpected_change_chip_renders(self):
        page, _board = html_for(
            (step("a", "Exercise 1", "Task 1: Start", "1", "Save the form"),),
            {"a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("unexpected-change",))},
        )
        self.assertIn("Unexpected page change", page)
        self.assertIn("class='chip suspect'", page)
        self.assertIn("The page changed in an unexpected way", page)

    def test_multi_flag_pass_counted_once(self):
        page, board = html_for(
            (
                step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),
                step("b", "Exercise 1", "Task 1: Start", "2", "Open the page"),
            ),
            {
                "a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("presence-only", "weak-evidence")),
                "b": StepOutcome("PASS", evidence_ids=("evidence-2",)),
            },
        )
        self.assertEqual(board.suspect_passes, 1)
        self.assertIn("1", page)
        self.assertIn("pass needs a check", page)

    def test_all_five_suspect_flags_counted(self):
        page, board = html_for(
            (
                step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),
                step("b", "Exercise 1", "Task 1: Start", "2", "Open the page"),
                step("c", "Exercise 1", "Task 1: Start", "3", "Navigate"),
                step("d", "Exercise 1", "Task 1: Start", "4", "Submit"),
                step("e", "Exercise 1", "Task 1: Start", "5", "Verify"),
            ),
            {
                "a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("presence-only",)),
                "b": StepOutcome("PASS", evidence_ids=("evidence-2",), suspect=("no-screen-change",)),
                "c": StepOutcome("PASS", evidence_ids=("evidence-3",), suspect=("weak-evidence",)),
                "d": StepOutcome("PASS", evidence_ids=("evidence-4",), suspect=("url-only-evidence",)),
                "e": StepOutcome("PASS", evidence_ids=("evidence-5",), suspect=("unexpected-change",)),
            },
        )
        self.assertEqual(board.suspect_passes, 5)
        self.assertIn("5", page)
        self.assertIn("passes need a check", page)

    def test_all_suspect_flags_render_in_run_report(self):
        from workshop_validator.step_board import render_run_report

        inventory = GuideInventory(
            1,
            (
                step("a", "Exercise 1", "Task 1: Start", "1", "Click Save"),
                step("b", "Exercise 1", "Task 1: Start", "2", "Open the page"),
                step("c", "Exercise 1", "Task 1: Start", "3", "Navigate"),
                step("d", "Exercise 1", "Task 1: Start", "4", "Submit"),
                step("e", "Exercise 1", "Task 1: Start", "5", "Verify"),
            ),
            (),
            (),
        )

        board = build_board(
            inventory,
            outcomes={
                "a": StepOutcome("PASS", evidence_ids=("evidence-1",), suspect=("presence-only",)),
                "b": StepOutcome("PASS", evidence_ids=("evidence-2",), suspect=("no-screen-change",)),
                "c": StepOutcome("PASS", evidence_ids=("evidence-3",), suspect=("weak-evidence",)),
                "d": StepOutcome("PASS", evidence_ids=("evidence-4",), suspect=("url-only-evidence",)),
                "e": StepOutcome("PASS", evidence_ids=("evidence-5",), suspect=("unexpected-change",)),
            },
            notice="Test notice",
            run_id="test-run",
        )

        summary = {
            "outcome": "success",
            "reason": "",
            "org": 1,
            "orgs": 1,
        }

        html = render_run_report(board, summary=summary, guide_name="Test Guide")

        # Verify all five suspect flags appear
        self.assertIn("Weak evidence: element only", html)
        self.assertIn("Screen did not change", html)
        self.assertIn("Weak evidence", html)
        self.assertIn("Evidence is the URL only", html)
        self.assertIn("Unexpected page change", html)

        # Verify suspect counter
        self.assertIn("5", html)
        self.assertIn("passes need a check", html)


if __name__ == "__main__":
    unittest.main()


class VerdictTests(unittest.TestCase):
    """Tests for AI verdict display in reports."""
    
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name)
    
    def test_load_verdicts_from_json(self):
        from workshop_validator.step_board import load_verdicts
        
        verdicts_data = {
            "run_id": "test123",
            "mode": "quick",
            "verdicts": [
                {"step_id": "step1", "verdict": "REAL", "evidence": "Control not found"},
                {"step_id": "step2", "verdict": "FALSE_FAILURE", "evidence": "Control was present"},
                {"step_id": "step3", "verdict": "UNCLEAR", "evidence": "Video frames unclear"},
            ]
        }
        
        verdicts_path = self.run_dir / "verdicts.json"
        verdicts_path.write_text(json.dumps(verdicts_data, indent=2), encoding="utf-8")
        
        result = load_verdicts(self.run_dir)
        self.assertEqual(len(result), 3)
        self.assertEqual(result["step1"]["verdict"], "REAL")
        self.assertEqual(result["step1"]["evidence"], "Control not found")
        self.assertEqual(result["step2"]["verdict"], "FALSE_FAILURE")
        self.assertEqual(result["step3"]["verdict"], "UNCLEAR")
    
    def test_load_verdicts_handles_missing_file(self):
        from workshop_validator.step_board import load_verdicts
        result = load_verdicts(self.run_dir)
        self.assertEqual(result, {})
    
    def test_verdict_badge_html(self):
        from workshop_validator.step_board import verdict_badge_html
        
        self.assertIn("REAL", verdict_badge_html("REAL"))
        self.assertIn("fail", verdict_badge_html("REAL"))
        
        self.assertIn("FALSE FAILURE", verdict_badge_html("FALSE_FAILURE"))
        self.assertIn("pass", verdict_badge_html("FALSE_FAILURE"))
        
        self.assertIn("FALSE PASS", verdict_badge_html("FALSE_PASS"))
        self.assertIn("fail", verdict_badge_html("FALSE_PASS"))
        
        self.assertIn("CONFIRMED PASS", verdict_badge_html("CONFIRMED_PASS"))
        self.assertIn("pass", verdict_badge_html("CONFIRMED_PASS"))
        
        self.assertIn("UNCLEAR", verdict_badge_html("UNCLEAR"))
        self.assertIn("needs-review", verdict_badge_html("UNCLEAR"))
    
    def test_unclear_not_shown_as_pass(self):
        from workshop_validator.step_board import (
            build_board,
            render_run_report,
            StepOutcome,
            GuideInventory,
            GuideStep,
        )
        
        # Create a step that passed
        steps = [
            GuideStep("step1", 1, "Setup", "Configure", "1", "Click Save", (0.0, 0.0, 100.0, 10.0), None, ()),
        ]
        
        inventory = GuideInventory(1, tuple(steps), (), ())
        
        outcomes = {
            "step1": StepOutcome(
                status="PASS",
                reason="",
                evidence_ids=("clicked",),
            ),
        }
        
        board = build_board(
            inventory,
            outcomes=outcomes,
            notice="Test notice",
            run_id="test123",
        )
        
        # Create verdicts.json with UNCLEAR verdict
        verdicts_data = {
            "run_id": "test123",
            "mode": "quick",
            "verdicts": [
                {"step_id": "step1", "verdict": "UNCLEAR", "evidence": "Frames were unclear"},
            ]
        }
        
        verdicts_path = self.run_dir / "verdicts.json"
        verdicts_path.write_text(json.dumps(verdicts_data, indent=2), encoding="utf-8")
        
        summary = {
            "outcome": "inconclusive",
            "reason": "Needs review",
            "org": 1,
            "orgs": 1,
        }
        
        html = render_run_report(
            board,
            summary=summary,
            guide_name="Test Guide",
            run_dir=self.run_dir,
        )
        
        # Verify UNCLEAR verdict is shown
        self.assertIn("UNCLEAR", html)
        # Verify it's not shown as pass - should be needs-review
        self.assertIn("needs-review", html)
    
    def test_false_pass_not_shown_as_pass(self):
        from workshop_validator.step_board import (
            build_board,
            render_run_report,
            StepOutcome,
            GuideInventory,
            GuideStep,
        )
        
        steps = [
            GuideStep("step2", 1, "Setup", "Configure", "2", "Verify field", (0.0, 0.0, 100.0, 10.0), None, ()),
        ]
        
        inventory = GuideInventory(1, tuple(steps), (), ())
        
        outcomes = {
            "step2": StepOutcome(
                status="PASS",
                reason="",
                evidence_ids=("verified",),
            ),
        }
        
        board = build_board(
            inventory,
            outcomes=outcomes,
            notice="Test notice",
            run_id="test456",
        )
        
        verdicts_data = {
            "run_id": "test456",
            "mode": "full",
            "verdicts": [
                {"step_id": "step2", "verdict": "FALSE_PASS", "evidence": "Wrong element was checked"},
            ]
        }
        
        verdicts_path = self.run_dir / "verdicts.json"
        verdicts_path.write_text(json.dumps(verdicts_data, indent=2), encoding="utf-8")
        
        summary = {
            "outcome": "failed",
            "reason": "False pass detected",
            "org": 1,
            "orgs": 1,
        }
        
        html = render_run_report(
            board,
            summary=summary,
            guide_name="Test Guide",
            run_dir=self.run_dir,
        )
        
        self.assertIn("FALSE PASS", html)
        self.assertIn("Wrong element was checked", html)
        # Should show as needs-review, not pass
        self.assertIn("needs-review", html)
    
    def test_render_shows_mode_and_verified_count(self):
        from workshop_validator.step_board import (
            build_board,
            render_run_report,
            GuideInventory,
            GuideStep,
        )
        
        steps = [
            GuideStep("step1", 1, "Setup", "Task", "1", "Step 1", (0.0, 0.0, 100.0, 10.0), None, ()),
            GuideStep("step2", 1, "Setup", "Task", "2", "Step 2", (0.0, 0.0, 100.0, 10.0), None, ()),
        ]
        
        inventory = GuideInventory(1, tuple(steps), (), ())
        
        board = build_board(
            inventory,
            outcomes={},
            notice="Test",
            run_id="test789",
        )
        
        verdicts_data = {
            "run_id": "test789",
            "mode": "full",
            "verdicts": [
                {"step_id": "step1", "verdict": "REAL", "evidence": "Failed"},
                {"step_id": "step2", "verdict": "CONFIRMED_PASS", "evidence": "Passed"},
            ]
        }
        
        verdicts_path = self.run_dir / "verdicts.json"
        verdicts_path.write_text(json.dumps(verdicts_data, indent=2), encoding="utf-8")
        
        summary = {"outcome": "failed", "reason": "", "org": 1, "orgs": 1}
        
        html = render_run_report(
            board,
            summary=summary,
            run_dir=self.run_dir,
        )
        
        self.assertIn("Full AI review", html)
        self.assertIn("2 steps verified by AI", html)
