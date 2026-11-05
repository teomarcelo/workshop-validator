"""W-105 guide note: a PASS reached differently than the guide says carries a visible note.

Synthetic text only. The browser cases run with RUN_BROWSER_SMOKE=1.
"""

import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

import fitz

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide
from workshop_validator.private_runner import _inventory_report, run_uploaded_guide
from workshop_validator.step_board import (
    StepOutcome,
    build_board,
    load_board,
    outcomes_from_report_steps,
    render_board,
    render_run_report,
    write_results_pdf,
)

NOTE = "inline saved message instead of a popup with OK."
SHOWN = "Guide differs: " + NOTE


def step(step_id, text, exercise="Exercise 1: Sample", task="Task 1: Sample", number="1"):
    return GuideStep(step_id, 1, exercise, task, number, text, (0, 0, 0, 0), None, ())


class FakePage:
    url = "https://example.invalid/lightning/setup/Home"
    frames = []

    def title(self):
        return "Home"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def walk_with(results, popup=None):
    """Walk steps whose check returns the given CheckResult per step id."""
    def run(state, item):
        if popup:
            state.popup_notes.append(popup)
        return results[item.step_id]
    steps = tuple(step(sid, f"Synthetic step {sid}.") for sid in results)
    clock = Clock()
    return walk_guide(
        FakePage(), GuideInventory(1, steps, (), ()),
        checks=(GuideCheck("fixed", lambda item: True, run),),
        timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
    )


class WalkPassThroughTests(unittest.TestCase):
    def test_pass_guide_note_reaches_the_walk_outcome(self):
        outcome = walk_with({"S1": CheckResult("PASS", "read", ("S1:read",), guide_note=NOTE)})
        self.assertEqual(outcome.statuses["S1"], "PASS")
        self.assertEqual(outcome.guide_notes, {"S1": NOTE})

    def test_guide_note_survives_a_closed_popup_note(self):
        outcome = walk_with(
            {"S1": CheckResult("PASS", "read", ("S1:read",), guide_note=NOTE)},
            popup="Closed an unrelated pop-up.",
        )
        self.assertIn("Closed an unrelated pop-up.", outcome.notes["S1"])
        self.assertEqual(outcome.guide_notes, {"S1": NOTE})

    def test_non_pass_and_evidence_free_pass_carry_no_guide_note(self):
        outcome = walk_with({
            "S1": CheckResult("UI_MISMATCH", "missed", (), guide_note=NOTE),
            "S2": CheckResult("PASS", "no evidence", (), guide_note=NOTE),
        })
        self.assertEqual(outcome.statuses["S2"], "NEEDS_REVIEW")
        self.assertEqual(outcome.guide_notes, {})


class ReportPassThroughTests(unittest.TestCase):
    def inventory(self):
        return GuideInventory(1, (step("S1", "Click Save."), step("S2", "Click Next.", number="2")), (), ())

    def test_inventory_report_keeps_guide_note_on_pass_only(self):
        report = _inventory_report(
            self.inventory(), {"S1": "PASS", "S2": "UI_MISMATCH"}, {"S2": "missed"},
            {"S1": ("S1:read",)}, None, None, None, {"S1": NOTE, "S2": NOTE},
        )
        self.assertEqual([r.guide_note for r in report], [NOTE, ""])

    def test_runner_scrubs_a_guide_note_that_holds_a_secret(self):
        user = "fake-user-" + "name-789"
        secret = "fake-pass-" + "word-789"

        def run(state, item):
            note = NOTE if item.step_id == "S1" else f"differs for {user}"
            return CheckResult("PASS", "read", (f"{item.step_id}:read",), guide_note=note)
        with tempfile.TemporaryDirectory() as tmp:
            result = run_uploaded_guide(
                user, secret, self.inventory(), page=FakePage(),
                output_dir=Path(tmp) / "run", repo_root=None,
                checks=(GuideCheck("fixed", lambda item: True, run),), walk_timeout=0,
            )
        notes = {r.step_id: r.guide_note for r in result.report_steps}
        self.assertEqual(notes["S1"], NOTE)
        self.assertNotIn(user, notes["S2"])
        self.assertTrue(notes["S2"])


class BoardRenderTests(unittest.TestCase):
    def board(self):
        inventory = GuideInventory(1, (
            step("S1", "Click Save, then OK."),
            step("S2", "Click Next.", number="2"),
            step("S3", "Click Done.", number="3"),
        ), (), ())
        outcomes = {
            "S1": StepOutcome("PASS", evidence_ids=("S1:read",), guide_note=NOTE),
            "S2": StepOutcome("PASS", evidence_ids=("S2:read",)),
            # A failure never shows the note; its own reason stays.
            "S3": StepOutcome("UI_MISMATCH", reason="Done was missing.", guide_note=NOTE),
        }
        return build_board(inventory, outcomes=outcomes, notice="Checked.", run_id="r1")

    def views(self, board):
        return {v.step_id: v for s in board.sections for t in s.tasks for v in t.steps}

    def test_board_counts_and_views(self):
        board = self.board()
        self.assertEqual(board.passed, 2)
        self.assertEqual(board.guide_noted, 1)
        views = self.views(board)
        self.assertEqual(views["S1"].guide_note, NOTE)
        self.assertEqual(views["S2"].guide_note, "")
        self.assertEqual(views["S3"].guide_note, "")

    def test_evidence_free_pass_shows_no_guide_note(self):
        inventory = GuideInventory(1, (step("S1", "Click Save."),), (), ())
        board = build_board(
            inventory, outcomes={"S1": StepOutcome("PASS", guide_note=NOTE)}, notice="n", run_id="r1",
        )
        self.assertEqual(self.views(board)["S1"].label, "FAIL")
        self.assertEqual(self.views(board)["S1"].guide_note, "")
        self.assertEqual(board.guide_noted, 0)

    def test_board_page_shows_note_in_its_own_style(self):
        page = render_board(self.board())
        self.assertIn(f"<p class='guide-note'>{SHOWN}</p>", page)
        self.assertEqual(page.count(SHOWN), 1)
        self.assertIn("pass with a guide note", page)

    def test_run_report_shows_note_and_separate_count(self):
        html = render_run_report(self.board(), summary={})
        self.assertIn(f"<div class='step-reason guide-note'>{SHOWN}</div>", html)
        self.assertEqual(html.count(SHOWN), 1)
        self.assertIn("<span class='stat-value'>1</span><span class='stat-label'>pass with a guide note</span>", html)
        self.assertIn(".guide-note {", html)

    def test_run_report_without_notes_has_no_guide_note_tile(self):
        inventory = GuideInventory(1, (step("S1", "Click Next."),), (), ())
        board = build_board(
            inventory, outcomes={"S1": StepOutcome("PASS", evidence_ids=("S1:read",))},
            notice="n", run_id="r1",
        )
        self.assertEqual(board.guide_noted, 0)
        html = render_run_report(board, summary={})
        self.assertNotIn("with a guide note", html)
        self.assertNotIn("Guide differs", html)

    def test_results_pdf_shows_note_and_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "results.pdf"
            write_results_pdf(self.board(), dest)
            text = " ".join(" ".join(page.get_text() for page in fitz.open(dest)).split())
        self.assertIn("PASS 2 (1 with a guide note). FAIL 1. NOT RUN 0.", text)
        self.assertIn(SHOWN, text)
        self.assertEqual(text.count("Guide differs"), 1)

    def test_board_json_round_trip_and_old_files_load(self):
        board = self.board()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "board.json"
            path.write_text(json.dumps(asdict(board)), encoding="utf-8")
            self.assertEqual(self.views(load_board(path))["S1"].guide_note, NOTE)
            self.assertEqual(load_board(path).guide_noted, 1)
            old = asdict(board)
            old.pop("guide_noted")
            for section in old["sections"]:
                for task in section["tasks"]:
                    for item in task["steps"]:
                        item.pop("guide_note")
            path.write_text(json.dumps(old), encoding="utf-8")
            loaded = load_board(path)
        self.assertEqual(loaded.guide_noted, 0)
        self.assertEqual(self.views(loaded)["S1"].guide_note, "")


HOST = "example" + ".invalid"
BASE = f"https://{HOST}/lightning/setup/personalInformation"
SAVED = "Your settings have been successfully saved."


def save_html(before: str, after: str, hash_after: str = "") -> str:
    move = f"location.hash='{hash_after}';" if hash_after else ""
    return (
        "<!doctype html><html><head><title>Personal Information</title></head><body>"
        f"<h1>Personal Information</h1><div id='msg'>{before}</div>"
        "<button onclick=\"window.saves=(window.saves||0)+1;"
        f"{move}document.getElementById('msg').textContent='{after}'\">Save</button>"
        "</body></html>"
    )


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class InlineSavedBrowserTests(unittest.TestCase):
    """Save then OK on a real Chromium page: no popup, only an inline message."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(
            headless=True, executable_path=os.environ.get("CHROMIUM_PATH") or None,
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def walk(self, html):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html", body=html,
        ) if route.request.url.startswith(BASE) else route.abort())
        page.goto(BASE)
        item = step("save", "Click Save at the bottom, then read the popup and click OK.")
        outcome = walk_guide(
            page, GuideInventory(1, (item,), (), ()), checks=(),
            timeout_s=4, interval_s=0.2, page_wait_s=4, page_poll_s=0.5,
        )
        return outcome, page.evaluate("window.saves || 0")

    def test_new_inline_saved_message_is_pass_with_guide_note(self):
        outcome, saves = self.walk(save_html("", SAVED))
        self.assertEqual(saves, 1)
        self.assertEqual(outcome.statuses["save"], "PASS", outcome.notes.get("save"))
        self.assertTrue(outcome.evidence["save"])
        self.assertEqual(outcome.guide_notes, {"save": NOTE})
        self.assertIn("successfully saved", outcome.notes["save"])

    def test_saved_text_already_there_before_save_is_not_pass(self):
        outcome, saves = self.walk(save_html(SAVED, SAVED))
        self.assertEqual(saves, 1)
        self.assertNotEqual(outcome.statuses["save"], "PASS")
        self.assertEqual(outcome.guide_notes, {})

    def test_old_saved_text_is_not_pass_even_when_save_changed_the_url(self):
        # Save itself passes on the URL change; the saved text was there before it.
        outcome, saves = self.walk(save_html(SAVED, SAVED, hash_after="save"))
        self.assertEqual(saves, 1)
        self.assertNotEqual(outcome.statuses["save"], "PASS")
        self.assertEqual(outcome.guide_notes, {})

    def test_no_saved_text_and_no_popup_is_not_pass(self):
        outcome, saves = self.walk(save_html("", "Working."))
        self.assertEqual(saves, 1)
        self.assertNotEqual(outcome.statuses["save"], "PASS")
        self.assertEqual(outcome.guide_notes, {})


if __name__ == "__main__":
    unittest.main()
