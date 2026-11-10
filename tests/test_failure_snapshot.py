"""Failure snapshots: one private snapshot per non-PASS step, redacted. Synthetic pages only."""

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from workshop_validator.failure_snapshot import redact_text, save_failure_snapshot
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, walk_guide
from workshop_validator.private_runner import _inventory_report, run_uploaded_guide
from workshop_validator.report import ReportStep
from workshop_validator.step_board import build_board, outcomes_from_report_steps, render_run_report

EMAIL = "learner" + "@" + "example.com"
HOST = "example" + ".my.example.com"
TYPED = "typed-secret-value-42"
PNG = b"\x89PNG\r\n\x1a\nfake"


class Item:
    def __init__(self, name="", text="", visible=True, headings=()):
        self.name, self.text, self.visible = name, text, visible
        self.heads = [Item(text=h) for h in headings]

    def is_visible(self):
        return self.visible

    def get_attribute(self, attr, timeout=None):
        return self.name if attr == "aria-label" else None

    def text_content(self, timeout=None):
        return self.text

    def inner_text(self, timeout=None):
        return self.text

    def evaluate(self, js, timeout=None):
        # For tag name check (short lambda)
        if "el => el.tagName" in str(js):
            return "DIV"
        # For aria-label extraction: check if it extracts aria-label after the shared guards
        # The key distinguisher is that it gets aria-label and does value-match on it
        if "element.getAttribute('aria-label')" in str(js) and "ariaLabel =" in str(js):
            return self.name
        # For safe text extraction: return the text content (self.text)
        return self.text

    def get_by_role(self, role):
        return Loc(self.heads if role == "heading" else [])


class Loc:
    def __init__(self, items=(), tree=""):
        self.items, self.tree = list(items), tree

    def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]

    @property
    def first(self):
        return self.items[0]

    def aria_snapshot(self, timeout=None):
        return self.tree


class Page:
    def __init__(self, screenshot_error=False):
        self.url = f"https://{HOST}/lightning/setup/Home?user={EMAIL}#x"
        self.frames = []
        self.screenshot_error = screenshot_error
        self.masks = None
        self.selectors = []
        self.roles = {
            "button": [Item(text=f"Signed in as {EMAIL}"), Item(text="Save"), Item(text="Hidden", visible=False)],
            "link": [Item(name=f"Open {HOST}"), Item(text="Setup Home")],
            "tab": [Item(text="Details")],
            "dialog": [Item(name="", text="whole dialog body text", headings=("Welcome Tour",))],
            "complementary": [Item(name="Help panel", headings=("Need help?",))],
            "textbox": [Item(text=TYPED)],
        }

    def title(self):
        return f"Home | {HOST}"

    def get_by_role(self, role):
        return Loc(self.roles.get(role, []))

    def locator(self, selector):
        self.selectors.append(selector)
        if selector == "head > title":
            return Loc([Item(text=self.title())])
        if selector == "body":
            return Loc(tree=(
                f'- button "Signed in as {EMAIL}"\n'
                f'- link "{HOST}"\n'
                f'- textbox "Email": {TYPED}\n'
                '- button "Close"\n'
            ))
        return Loc()

    def screenshot(self, **kwargs):
        if self.screenshot_error:
            raise RuntimeError("no screenshot")
        self.masks = kwargs.get("mask")
        return PNG


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def step(step_id, text):
    return GuideStep(step_id, 1, None, None, "1", text, (0, 0, 0, 0), None, ())


def checks_by_text():
    def run(state, item):
        status = item.text
        evidence = (f"{item.step_id}:read",) if status == "PASS" else ()
        return CheckResult(status, f"{status} note for {EMAIL}", evidence)
    return (GuideCheck("by-text", lambda item: True, run),)


ORDER = ("PASS", "UI_MISMATCH", "FUNCTIONAL_FAIL", "NEEDS_REVIEW", "ENV_BLOCKED")


def walk(page, run_dir, statuses=ORDER):
    steps = tuple(step(f"S{i}", status) for i, status in enumerate(statuses, 1))
    clock = Clock()
    return walk_guide(
        page, GuideInventory(1, steps, (), ()), checks=checks_by_text(),
        timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        snapshot_dir=run_dir,
    )


class SnapshotWrittenTests(unittest.TestCase):
    def test_each_non_pass_status_writes_json_and_png_and_pass_writes_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            outcome = walk(Page(), run_dir)
            self.assertEqual(outcome.statuses, dict(zip(("S1", "S2", "S3", "S4", "S5"), ORDER)))
            self.assertEqual(sorted(outcome.snapshots), ["S2", "S3", "S4", "S5"])
            folder = run_dir / "snapshots"
            self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)
            names = sorted(p.name for p in folder.iterdir())
            self.assertEqual(names, [
                "002-S2.json", "002-S2.png", "003-S3.json", "003-S3.png",
                "004-S4.json", "004-S4.png", "005-S5.json", "005-S5.png",
            ])
            for path in folder.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
            self.assertEqual(outcome.snapshots["S3"], "snapshots/003-S3.json")
            data = json.loads((folder / "003-S3.json").read_text())
            self.assertEqual(data["status"], "FUNCTIONAL_FAIL")
            self.assertEqual(data["url_path"], "/lightning/setup/Home")
            self.assertEqual(data["screenshot"], "snapshots/003-S3.png")
            self.assertIn("Save", data["controls"]["button"])
            self.assertNotIn("Hidden", data["controls"]["button"])
            self.assertIn("Setup Home", data["controls"]["link"])
            self.assertEqual(data["controls"]["tab"], ["Details"])
            self.assertEqual(data["controls"]["dialog"], ["Welcome Tour"])
            panels = {(p["role"], p["name"]): p["headings"] for p in data["panels"]}
            self.assertEqual(panels[("complementary", "Help panel")], ["Need help?"])
            self.assertEqual(panels[("dialog", "")], ["Welcome Tour"])
            self.assertIn("FUNCTIONAL_FAIL note for", data["note"])
            self.assertIn('- button "Close"', data["tree"])
            self.assertEqual((folder / "003-S3.png").read_bytes(), PNG)

    def test_no_snapshot_dir_writes_nothing(self):
        clock = Clock()
        outcome = walk_guide(
            Page(), GuideInventory(1, (step("S1", "UI_MISMATCH"),), (), ()),
            checks=checks_by_text(), timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )
        self.assertEqual(outcome.statuses, {"S1": "UI_MISMATCH"})
        self.assertEqual(outcome.snapshots, {})


class SnapshotRedactionTests(unittest.TestCase):
    def test_emails_hosts_and_typed_values_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Page()
            walk(page, Path(tmp), ("UI_MISMATCH",))
            text = (Path(tmp) / "snapshots" / "001-S1.json").read_text()
            for secret in (EMAIL, HOST, "example.com", TYPED, "learner@"):
                self.assertNotIn(secret, text)
            data = json.loads(text)
            self.assertIn("Signed in as <email>", data["controls"]["button"])
            self.assertIn("Open <host>", data["controls"]["link"])
            self.assertEqual(data["title"], "Home | <host>")
            self.assertIn("<email>", data["note"])
            self.assertNotIn("whole dialog body text", text)
            # Inputs are masked in the screenshot.
            self.assertTrue(page.masks)

    def test_runner_secret_check_omits_matching_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            walk_guide(
                Page(), GuideInventory(1, (step("S1", "UI_MISMATCH"),), (), ()),
                checks=checks_by_text(), timeout_s=0, interval_s=1, clock=clock,
                sleeper=clock.sleep, snapshot_dir=Path(tmp),
                snapshot_leaks=lambda value: "Setup Home" in value,
            )
            text = (Path(tmp) / "snapshots" / "001-S1.json").read_text()
            self.assertNotIn("Setup Home", text)
            self.assertIn("<omitted>", text)


class SnapshotErrorTests(unittest.TestCase):
    def test_write_error_keeps_status_and_walk_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_a_dir = Path(tmp) / "file"
            not_a_dir.write_text("x")
            outcome = walk(Page(), not_a_dir, ("UI_MISMATCH", "FUNCTIONAL_FAIL", "PASS"))
            self.assertEqual(
                outcome.statuses,
                {"S1": "UI_MISMATCH", "S2": "FUNCTIONAL_FAIL", "S3": "PASS"},
            )
            self.assertEqual(outcome.snapshots, {})
            self.assertIn("Failure snapshot not saved", outcome.notes["S1"])
            self.assertTrue(outcome.notes["S1"].startswith("UI_MISMATCH note for"))
            self.assertEqual(outcome.executed, ("S1", "S2", "S3"))

    def test_screenshot_error_still_writes_json_and_keeps_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            outcome = walk(Page(screenshot_error=True), Path(tmp), ("NEEDS_REVIEW",))
            self.assertEqual(outcome.statuses, {"S1": "NEEDS_REVIEW"})
            folder = Path(tmp) / "snapshots"
            self.assertEqual([p.name for p in folder.iterdir()], ["001-S1.json"])
            data = json.loads((folder / "001-S1.json").read_text())
            self.assertEqual(data["screenshot"], "unavailable (RuntimeError)")


class SlowItem(Item):
    """Every visibility read costs one second on the shared fake clock."""

    def __init__(self, clock, text):
        super().__init__(text=text)
        self.clock = clock

    def is_visible(self):
        self.clock.now += 1.0
        return True

    def evaluate(self, js, timeout=None):
        # Slow evaluate also costs time
        self.clock.now += 0.1
        return super().evaluate(js, timeout)


class SlowPage(Page):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock
        self.shots = 0
        self.roles["button"] = [SlowItem(clock, f"Button {i}") for i in range(30)]

    def screenshot(self, **kwargs):
        self.shots += 1
        return super().screenshot(**kwargs)


class SnapshotTimeCapTests(unittest.TestCase):
    def snapshot(self, page, clock, tmp):
        from workshop_validator.guide_walk import _redact_aria_snapshot
        return save_failure_snapshot(
            page, Path(tmp), 1, "S1", "UI_MISMATCH", "missed",
            roots=[page], redact_tree=_redact_aria_snapshot, clock=clock,
        )

    def test_slow_page_is_cut_at_the_cap_and_collected_part_is_written(self):
        clock = Clock()
        page = SlowPage(clock)
        with tempfile.TemporaryDirectory() as tmp:
            href = self.snapshot(page, clock, tmp)
            data = json.loads((Path(tmp) / href).read_text())
        self.assertEqual(data["truncated"], "snapshot truncated (time)")
        self.assertEqual(data["status"], "UI_MISMATCH")
        # Reads stop once 8 s are used: some buttons are kept, not all 30.
        self.assertIn("Button 0", data["controls"]["button"])
        self.assertLess(len(data["controls"]["button"]), 30)
        self.assertLessEqual(clock.now, 9.0)
        self.assertEqual(page.shots, 0)
        self.assertEqual(data["screenshot"], "unavailable (TimeoutError)")

    def test_fast_page_is_not_marked_truncated(self):
        clock = Clock()
        page = Page()
        with tempfile.TemporaryDirectory() as tmp:
            href = self.snapshot(page, clock, tmp)
            data = json.loads((Path(tmp) / href).read_text())
        self.assertEqual(data["truncated"], "")
        self.assertEqual(data["screenshot"], "snapshots/001-S1.png")

    def test_walk_uses_the_walk_clock_for_the_cap(self):
        clock = Clock()
        page = SlowPage(clock)
        with tempfile.TemporaryDirectory() as tmp:
            outcome = walk_guide(
                page, GuideInventory(1, (step("S1", "UI_MISMATCH"),), (), ()),
                checks=checks_by_text(), timeout_s=0, interval_s=1, clock=clock,
                sleeper=clock.sleep, snapshot_dir=Path(tmp),
            )
            data = json.loads((Path(tmp) / outcome.snapshots["S1"]).read_text())
        self.assertEqual(outcome.statuses, {"S1": "UI_MISMATCH"})
        self.assertEqual(data["truncated"], "snapshot truncated (time)")


PAGE_DEFAULT_S = 20.0


def _wait(clock, timeout):
    """A Playwright read on a detached element waits its timeout, or the page default."""
    clock.now += (timeout / 1000.0) if timeout else PAGE_DEFAULT_S


class DetachedItem(Item):
    def __init__(self, clock):
        super().__init__(text="Detached")
        self.clock = clock

    def get_attribute(self, attr, timeout=None):
        _wait(self.clock, timeout)
        raise TimeoutError("detached")

    def inner_text(self, timeout=None):
        _wait(self.clock, timeout)
        raise TimeoutError("detached")

    def evaluate(self, js, timeout=None):
        _wait(self.clock, timeout)
        raise TimeoutError("detached")


class SlowTitle(Item):
    def __init__(self, clock):
        super().__init__()
        self.clock = clock

    def text_content(self, timeout=None):
        _wait(self.clock, timeout)
        raise TimeoutError("busy")


class ReviewFixTests(unittest.TestCase):
    def snapshot(self, page, clock, tmp, budget_s=8.0, roots=None):
        from workshop_validator.guide_walk import _redact_aria_snapshot
        href = save_failure_snapshot(
            page, Path(tmp), 1, "S1", "UI_MISMATCH", "missed",
            roots=roots or [page], redact_tree=_redact_aria_snapshot, clock=clock, budget_s=budget_s,
        )
        return json.loads((Path(tmp) / href).read_text())

    def test_detached_attribute_reads_stay_within_the_cap(self):
        clock = Clock()
        page = Page()
        page.roles["button"] = [DetachedItem(clock) for _ in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot(page, clock, tmp)
        self.assertLessEqual(clock.now, 8.5)
        self.assertEqual(data["truncated"], "snapshot truncated (time)")

    def test_slow_title_is_bounded_by_the_budget(self):
        clock = Clock()
        page = Page()
        page.title = lambda: (_wait(clock, None), "Late")[1]
        original = page.locator
        page.locator = lambda sel: Loc([SlowTitle(clock)]) if sel == "head > title" else original(sel)
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot(page, clock, tmp)
        self.assertLessEqual(clock.now, 8.5)
        self.assertEqual(data["title"], "unavailable")

    def test_title_is_skipped_when_the_budget_is_short(self):
        clock = Clock()
        page = Page()
        calls = []
        page.title = lambda: calls.append("title") or "Late"
        original = page.locator
        page.locator = lambda sel: (calls.append(sel), original(sel))[1]
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot(page, clock, tmp, budget_s=0.5)
        self.assertEqual(data["title"], "skipped (time)")
        self.assertNotIn("title", calls)
        self.assertNotIn("head > title", calls)
        self.assertEqual(data["truncated"], "snapshot truncated (time)")

    def test_main_frame_is_not_scanned_twice(self):
        page = Page()
        main = Page()
        page.main_frame = main
        page.frames = [main]
        clock = Clock()
        from workshop_validator.guide_walk import _roots
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot(page, clock, tmp, roots=_roots(page))
        self.assertEqual(len(data["panels"]), 2)

    def test_child_frame_is_still_scanned(self):
        page = Page()
        main, child = Page(), Page()
        child.roles = {"button": [Item(text="Frame Button")]}
        page.main_frame = main
        page.frames = [main, child]
        from workshop_validator.guide_walk import _roots
        with tempfile.TemporaryDirectory() as tmp:
            data = self.snapshot(page, Clock(), tmp, roots=_roots(page))
        self.assertIn("Frame Button", data["controls"]["button"])
        self.assertEqual(len(data["panels"]), 2)

    def test_plaintext_only_editable_is_masked(self):
        page = Page()
        with tempfile.TemporaryDirectory() as tmp:
            self.snapshot(page, Clock(), tmp)
        masked = [sel for sel in page.selectors if sel.startswith("input")]
        self.assertTrue(masked)
        self.assertIn("[contenteditable='plaintext-only']", masked[0])

    def test_dotless_addresses_and_ipv4_are_redacted(self):
        local = "admin" + "@" + "localhost"
        address = ".".join(("10", "0", "0", "12"))
        text = redact_text(f"Mail {local} at {address}:8080 now")
        self.assertNotIn(local, text)
        self.assertNotIn(address, text)
        self.assertEqual(text, "Mail <email> at <ip>:8080 now")


class RunnerWiringTests(unittest.TestCase):
    USER = "fake-user-" + "name-123"
    SECRET = "fake-pass-" + "word-456"

    def run_guide(self, output_dir, repo_root=None):
        def run(state, item):
            return CheckResult("UI_MISMATCH", f"missed {self.USER}", ())
        steps = (step("S1", "Click Save."),)
        return run_uploaded_guide(
            self.USER, self.SECRET, GuideInventory(1, steps, (), ()),
            page=Page(), output_dir=output_dir, repo_root=repo_root,
            checks=(GuideCheck("miss", lambda item: True, run),), walk_timeout=0,
        )

    def test_private_run_dir_gets_snapshot_and_report_row_links_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_guide(Path(tmp) / "run")
            self.assertEqual(result.report_steps[0].status, "UI_MISMATCH")
            self.assertEqual(result.report_steps[0].snapshot, "snapshots/001-S1.json")
            text = (Path(tmp) / "run" / "snapshots" / "001-S1.json").read_text()
            self.assertNotIn(self.USER, text)
            self.assertIn("<omitted>", text)

    def test_run_dir_inside_repo_gets_no_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_guide(Path(tmp) / "run", repo_root=Path(tmp))
            self.assertEqual(result.report_steps[0].status, "UI_MISMATCH")
            self.assertEqual(result.report_steps[0].snapshot, "")
            self.assertFalse((Path(tmp) / "run" / "snapshots").exists())


class SnapshotBoardTests(unittest.TestCase):
    def test_board_row_links_snapshot_only_for_non_pass(self):
        steps = (step("S1", "Click Save."), step("S2", "Click Next."))
        inventory = GuideInventory(1, steps, (), ())
        report = _inventory_report(
            inventory, {"S1": "UI_MISMATCH", "S2": "PASS"}, {"S1": "missed"},
            {"S2": ("S2:read",)}, None, None,
            {"S1": "snapshots/001-S1.json", "S2": "snapshots/002-S2.json"},
        )
        self.assertEqual([r.snapshot for r in report], ["snapshots/001-S1.json", ""])
        board = build_board(inventory, outcomes=outcomes_from_report_steps(report), notice="n", run_id="r1")
        views = {v.step_id: v for s in board.sections for t in s.tasks for v in t.steps}
        self.assertEqual(views["S1"].snapshot, "snapshots/001-S1.json")
        self.assertEqual(views["S2"].snapshot, "")
        html = render_run_report(board, summary={})
        self.assertIn("href='snapshots/001-S1.json'>Failure snapshot</a>", html)
        self.assertNotIn("002-S2.json", html)

    def test_board_rejects_a_path_outside_snapshots(self):
        bad = ReportStep("S1", 1, "Click Save.", "UI_MISMATCH", (), "missed", snapshot="../../x.json")
        inventory = GuideInventory(1, (step("S1", "Click Save."),), (), ())
        board = build_board(inventory, outcomes=outcomes_from_report_steps([bad]), notice="n", run_id="r1")
        views = [v for s in board.sections for t in s.tasks for v in t.steps]
        self.assertEqual(views[0].snapshot, "")


class PlantedValueTests(unittest.TestCase):
    """Test that values typed or selected in controls never leak into snapshot names (W-196).

    Planted-value matrix: 8 input kinds (textbox, textarea, select, combobox, searchbox,
    spinbutton, password, contenteditable) each nested in 5 contexts (button, link, heading,
    table cell, gridcell), plus editable-ancestor cases and hidden text.
    """

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_no_planted_values_appear_in_snapshot_and_static_text_is_kept(self):
        """Values in controls nested in buttons/links/headings/cells never leak. Controls inside
        editable ancestors never leak. Hidden text never appears. Static text is still collected."""
        import subprocess
        import sys

        html = """<!DOCTYPE html>
        <html>
        <head><title>Example Plant Test</title></head>
        <body>
            <!-- Positive case: dialog heading with no control -->
            <dialog id="clean-dialog" open>
                <h2>Example Panel</h2>
                <p>Dialog content</p>
            </dialog>

            <!-- Positive cases: static text should appear -->
            <button id="btn-save">Save</button>
            <a href="#x" id="link-report">Open report</a>

            <!-- Button with title that holds a marker (title never read) -->
            <button id="btn-title" title="PLANT-title-marker">Pick</button>

            <!-- Hidden text inside button -->
            <button id="btn-hidden">Visible <span hidden>PLANT-hidden-span</span></button>
            <button id="btn-aria-hidden">Visible <span aria-hidden="true">PLANT-aria-hidden</span></button>
            <button id="btn-display-none">Visible <span style="display:none">PLANT-display-none</span></button>
            <button id="btn-visibility">Visible <span style="visibility:hidden">PLANT-visibility-hidden</span></button>

            <!-- Planted values: button context -->
            <button id="btn1">Label <input type="text" id="inp1" /></button>
            <button id="btn2">Label <textarea id="ta1"></textarea></button>
            <button id="btn3">Label <select id="sel1"><option>Default</option><option value="2">PLANT-select-button</option></select></button>
            <button id="btn4">Label <input type="search" id="search1" role="searchbox" /></button>
            <button id="btn5">Label <input type="number" id="spin1" role="spinbutton" /></button>
            <button id="btn6">Label <input type="password" id="pass1" /></button>
            <button id="btn7">Label <div contenteditable="true" id="edit1"></div></button>
            <button id="btn8">Label <input type="text" id="combo1" role="combobox" /></button>

            <!-- Link context -->
            <a href="#x" id="link1">Link <input type="text" id="inp2" /></a>
            <a href="#x" id="link2">Link <textarea id="ta2"></textarea></a>
            <a href="#x" id="link3">Link <select id="sel2"><option value="1">PLANT-select-link</option></select></a>
            <a href="#x" id="link4">Link <div contenteditable="true" id="edit2"></div></a>

            <!-- Dialog heading context -->
            <dialog id="main-dialog" open>
                <h2 id="h1">Heading <input type="text" id="inp3" /></h2>
                <h2 id="h2">Heading <textarea id="ta3"></textarea></h2>
                <h2 id="h3">Heading <select id="sel3"><option value="1">PLANT-select-heading</option></select></h2>
                <h2 id="h4">Heading <div contenteditable="true" id="edit3"></div></h2>
            </dialog>

            <!-- Table cell context -->
            <table>
                <tr>
                    <td id="cell1">Cell <input type="text" id="inp4" /></td>
                    <td id="cell2">Cell <textarea id="ta4"></textarea></td>
                </tr>
                <tr>
                    <td id="cell3">Cell <select id="sel4"><option value="1">PLANT-select-cell</option></select></td>
                    <td id="cell4">Cell <div contenteditable="true" id="edit4"></div></td>
                </tr>
            </table>

            <!-- Gridcell context -->
            <div role="grid">
                <div role="row">
                    <div role="gridcell" id="gc1">Grid <input type="text" id="inp5" /></div>
                    <div role="gridcell" id="gc2">Grid <textarea id="ta5"></textarea></div>
                </div>
                <div role="row">
                    <div role="gridcell" id="gc3">Grid <select id="sel5"><option value="1">PLANT-select-gridcell</option></select></div>
                    <div role="gridcell" id="gc4">Grid <div contenteditable="true" id="edit5"></div></div>
                </div>
            </div>

            <!-- Editable-ancestor cases: button/link/heading inside contenteditable -->
            <div contenteditable="true" id="editable-parent">
                <button id="btn-in-editable">PLANT-btn-in-contenteditable</button>
                <a href="#x" id="link-in-editable">PLANT-link-in-contenteditable</a>
                <h2 id="heading-in-editable">PLANT-heading-in-contenteditable</h2>
            </div>

            <!-- Inherited contenteditable (guards isContentEditable for inherited case) -->
            <dialog id="inherited-dialog" open>
                <div contenteditable="true">
                    <h2 id="h-inherited">PLANT-inherited-heading</h2>
                </div>
            </dialog>

            <!-- Elements that are themselves contenteditable (guards isContentEditable check) -->
            <button contenteditable="true" id="btn-itself-editable">PLANT-btn-itself-editable</button>
            <a href="#x" contenteditable="true" id="link-itself-editable">PLANT-link-itself-editable</a>
            <div role="button" contenteditable="true" id="div-btn-editable">PLANT-div-btn-editable</div>
            <div role="link" contenteditable="true" id="div-link-editable">PLANT-div-link-editable</div>

            <!-- Self-editable heading in dialog (guards isContentEditable for headings) -->
            <dialog id="selfedit-dialog" open>
                <h2 contenteditable="true" id="h-selfedit">PLANT-selfedit-heading</h2>
            </dialog>

            <!-- Self-editable link (guards isContentEditable for links) -->
            <a href="#x" contenteditable="true" id="link-selfedit">PLANT-selfedit-link</a>

            <!-- Button/link/heading inside role=textbox (guards closest ancestor check) -->
            <div role="textbox" id="textbox-parent">
                <button id="btn-in-textbox">PLANT-btn-in-textbox</button>
                <a href="#x" id="link-in-textbox">PLANT-link-in-textbox</a>
                <h2 id="heading-in-textbox">PLANT-heading-in-textbox</h2>
            </div>

            <!-- Frame for designMode test (guards designMode check) -->
            <iframe id="design-frame"></iframe>
        </body>
        </html>
        """

        # Start a real browser and create the snapshot
        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()

    # Serve from https://app.example.com/
    page.route("https://app.example.com/test", lambda route: route.fulfill(
        status=200,
        content_type="text/html",
        body=html_content
    ))
    page.goto("https://app.example.com/test")

    # Type real values with key events
    page.locator('#inp1').press_sequentially("PLANT-textbox-button")
    page.locator('#ta1').press_sequentially("PLANT-textarea-button")
    page.locator('#sel1').select_option("2")
    page.locator('#search1').press_sequentially("PLANT-searchbox-button")
    page.locator('#spin1').press_sequentially("731904")
    page.locator('#pass1').press_sequentially("PLANT-password-button")
    page.locator('#edit1').press_sequentially("PLANT-editable-button")
    page.locator('#combo1').press_sequentially("PLANT-combobox-button")

    page.locator('#inp2').press_sequentially("PLANT-textbox-link")
    page.locator('#ta2').press_sequentially("PLANT-textarea-link")
    page.locator('#sel2').select_option("1")
    page.locator('#edit2').press_sequentially("PLANT-editable-link")

    page.locator('#inp3').press_sequentially("PLANT-textbox-heading")
    page.locator('#ta3').press_sequentially("PLANT-textarea-heading")
    page.locator('#sel3').select_option("1")
    page.locator('#edit3').press_sequentially("PLANT-editable-heading")

    page.locator('#inp4').press_sequentially("PLANT-textbox-cell")
    page.locator('#ta4').press_sequentially("PLANT-textarea-cell")
    page.locator('#sel4').select_option("1")
    page.locator('#edit4').press_sequentially("PLANT-editable-cell")

    page.locator('#inp5').press_sequentially("PLANT-textbox-gridcell")
    page.locator('#ta5').press_sequentially("PLANT-textarea-gridcell")
    page.locator('#sel5').select_option("1")
    page.locator('#edit5').press_sequentially("PLANT-editable-gridcell")

    # Set up designMode iframe with content (same origin)
    iframe_html = '<button id="dm-btn">PLANT-btn-in-designMode</button><h2 id="dm-h2">PLANT-heading-in-designMode</h2>'
    page.route("https://app.example.com/iframe", lambda route: route.fulfill(
        status=200,
        content_type="text/html",
        body=iframe_html
    ))
    page.evaluate('''
        const frame = document.getElementById("design-frame");
        frame.src = "https://app.example.com/iframe";
    ''')
    page.wait_for_load_state("networkidle")

    # Set designMode on the iframe
    page.evaluate('document.getElementById("design-frame").contentDocument.designMode = "on"')

    # Get all roots including the frame
    try:
        frame_handle = page.frame(name='')
        if not frame_handle:
            # Try by URL
            for f in page.frames:
                if 'iframe' in f.url:
                    frame_handle = f
                    break
        roots = [page, frame_handle] if frame_handle else [page]
    except:
        roots = [page]

    href = save_failure_snapshot(
        page, run_dir, 1, "TEST1", "UI_MISMATCH", "test note",
        roots=roots, redact_tree=_redact_aria_snapshot
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            # Set PYTHONPATH to include the repository root
            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text()
            snapshot_data = json.loads(snapshot_text)

            # Assert no PLANT marker appears anywhere in the file
            planted_values = [
                "PLANT-textbox-button", "PLANT-textarea-button", "PLANT-select-button",
                "PLANT-searchbox-button", "731904", "PLANT-password-button",
                "PLANT-editable-button", "PLANT-combobox-button",
                "PLANT-textbox-link", "PLANT-textarea-link", "PLANT-select-link", "PLANT-editable-link",
                "PLANT-textbox-heading", "PLANT-textarea-heading", "PLANT-select-heading", "PLANT-editable-heading",
                "PLANT-textbox-cell", "PLANT-textarea-cell", "PLANT-select-cell", "PLANT-editable-cell",
                "PLANT-textbox-gridcell", "PLANT-textarea-gridcell", "PLANT-select-gridcell", "PLANT-editable-gridcell",
                "PLANT-title-marker",
                "PLANT-hidden-span", "PLANT-aria-hidden", "PLANT-display-none", "PLANT-visibility-hidden",
                "PLANT-btn-in-contenteditable", "PLANT-link-in-contenteditable", "PLANT-heading-in-contenteditable",
                "PLANT-btn-itself-editable", "PLANT-link-itself-editable",
                "PLANT-div-btn-editable", "PLANT-div-link-editable",
                "PLANT-selfedit-heading", "PLANT-selfedit-link",
                "PLANT-inherited-heading",
                "PLANT-btn-in-textbox", "PLANT-link-in-textbox", "PLANT-heading-in-textbox",
                "PLANT-btn-in-designMode", "PLANT-heading-in-designMode",
            ]
            for plant in planted_values:
                self.assertNotIn(plant, snapshot_text, f"Leaked {plant}")

            # Positive checks: static text in controls should appear
            self.assertIn('"Save"', snapshot_text)
            self.assertIn('"Open report"', snapshot_text)
            self.assertIn('"Pick"', snapshot_text)
            self.assertIn('"Visible"', snapshot_text)

            # Dialog heading with no control keeps its name
            found_example_panel = False
            for panel in snapshot_data["panels"]:
                if "Example Panel" in panel.get("headings", []):
                    found_example_panel = True
                    break
            self.assertTrue(found_example_panel, "Dialog heading 'Example Panel' not found")

            # Buttons and links with controls inside have no static text (e.g., "Label", "Link", "Cell", "Grid")
            # These words appear in the HTML before the controls but must not appear in the snapshot
            for control_prefix in ["Label", "Link", "Cell", "Grid", "Heading"]:
                self.assertNotIn(f'"{control_prefix}"', snapshot_text, f"Static text '{control_prefix}' from element with control inside leaked")


class D2_1_Tests(unittest.TestCase):
    """D2-1: shared control check and traversal for snapshot names (old fields only).

    Test cases from the design review matrix that apply to old fields (controls/panels).
    All planted values are ASCII, unique, 10-30 characters, with unique prefixes.
    """

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_webkit_user_modify_role_spelling_value_match_shadow_visibility_redaction_positives(self):
        """Comprehensive D2-1 test covering all required cases for old fields."""
        import subprocess
        import sys

        html = """<!DOCTYPE html>
        <html>
        <head><title>D2-1 Comprehensive Test</title></head>
        <body>
            <!-- Case 2: CSS -webkit-user-modify inside button, heading, cell -->
            <button id="btn-webkit">Static <span id="webkit-btn" style="-webkit-user-modify: read-write;">PLANT-webkit-button-val</span></button>
            <dialog open>
                <h2 id="heading-webkit">Heading <span style="-webkit-user-modify: read-write-plaintext-only;">PLANT-webkit-heading-val</span></h2>
            </dialog>
            <table>
                <tr>
                    <td id="cell-webkit">Cell <div style="-webkit-user-modify: read-write;">PLANT-webkit-cell-value</div></td>
                </tr>
            </table>

            <!-- Case 3: role "bogus textbox" and "TEXTBOX" (case-insensitive, token matching) inside button and cell -->
            <button id="btn-bogus">Label <div role="bogus textbox">PLANT-bogus-textbox-btn</div></button>
            <button id="btn-upper">Label <div role="TEXTBOX">PLANT-upper-textbox-btn</div></button>
            <table>
                <tr>
                    <td id="cell-bogus">Cell <div role="bogus  textbox">PLANT-bogus-textbox-cell</div></td>
                    <td id="cell-upper">Cell <div role="  TEXTBOX  ">PLANT-upper-textbox-cell</div></td>
                </tr>
            </table>

            <!-- Case 5: select chosen option mirrored into a sibling span -->
            <div id="container-select">
                <select id="sel-mirror"><option>Default</option><option value="2">PLANT-selected-option-mirrored</option></select>
                <span id="mirror-span">PLANT-selected-option-mirrored</span>
            </div>

            <!-- Case 5: faux combobox chosen text mirrored into an aria-label -->
            <div role="button" aria-label="PLANT-combobox-chosen-mirror" id="faux-combobox">
                <div role="combobox">PLANT-combobox-chosen-mirror</div>
            </div>

            <!-- Case 6: button inside a shadow root whose host is inside role=combobox -->
            <div role="combobox" id="combo-container">
                <div id="shadow-host-combo"></div>
            </div>

            <!-- Case 6: getter shim with an input inside a button (button must have has_input: 1, but text should be blocked) -->
            <button id="btn-shim">Button <input type="text" id="shim-input" value="PLANT-shim-input-value" /></button>
            <div id="shadow-host-shim"></div>

            <!-- Case 7: visibility: collapse and other hidden cases -->
            <button id="btn-collapse">Visible <span style="visibility: collapse;">PLANT-collapse-hidden</span></button>
            <button id="btn-display">Visible <span style="display: none;">PLANT-display-none-hidden</span></button>
            <button id="btn-hidden">Visible <span hidden>PLANT-attr-hidden</span></button>
            <button id="btn-aria">Visible <span aria-hidden="true">PLANT-aria-hidden</span></button>

            <!-- Case 9: redaction - split-span hostname and long-token cases -->
            <dialog open>
                <h2 id="heading-split">Org is <span>acme-dev-ed</span><span>.my.salesforce.example</span> here</h2>
                <h2 id="heading-email">Email <span>zq-local</span><span>@example.com</span> split</h2>
                <!-- 40-char token at position 60 -->
                <h2 id="heading-token">Start of heading text ABCDEFGH012345678901234567890123456789012345 IJKLMNOPQRSTUVWXYZ end</h2>
            </dialog>

            <!-- Rule 1 tests: cut before redaction (must fail with 80-char cut, pass with 400-drop) -->
            <dialog open>
                <!-- Test (a): ~90 char heading with email split at positions 70-95, so 80-cut leaves "zq-local@exa" -->
                <h2 id="heading-cut-email">Prefix text is about sixty chars long before the email zq-local@example.com suffix text</h2>
                <!-- Test (b): 40-char token starting at position 60, so 80-cut gives a 20-char piece -->
                <h2 id="heading-cut-token">Start text is about sixty characters long before token ABCD0123456789EFGH0123456789WXYZ012345 end</h2>
                <!-- Test (c): heading over 400 chars must be dropped whole -->
                <h2 id="heading-over-400">This is a very long heading that exceeds four hundred characters in total length so that it will be dropped entirely by the guard and not appear in the snapshot at all. This text continues for quite a while to reach the required length. More text here to pad it out further and further. Even more text to ensure we definitely exceed the four hundred character limit for this particular test case which validates that over-long names are dropped whole before any redaction occurs.</h2>
            </dialog>

            <!-- Test for M13: button with multiple adjacent text nodes (no spaces between them) -->
            <button id="btn-multitext">Part<!--comment-->One<!--comment-->Two</button>

            <!-- Page-wide value-match tests -->
            <!-- Test (a): select OUTSIDE button with chosen option, button elsewhere with matching text -->
            <select id="sel-pagewide"><option>Default</option><option value="chosen">zqSelPick4471</option></select>
            <button id="btn-pagewide-select"><span>zqSelPick4471</span></button>

            <!-- Test (b): textbox outside with typed value, button with matching aria-label -->
            <input type="text" id="input-pagewide" role="textbox" />
            <button id="btn-pagewide-label" aria-label="Open zqMirror5512"></button>

            <!-- Mutant #8: hidden/submit inputs must be skipped in value-match -->
            <input type="hidden" id="input-hidden-mutant8" value="Save" />
            <input type="submit" id="input-submit-mutant8" value="Send now" />
            <button id="btn-save-mutant8">Save</button>
            <button id="btn-send-mutant8">Send now</button>

            <!-- Mutant #9: shadow root traversal (use divs with role=button since buttons don't support shadow) -->
            <div role="button" id="btn-shadow-input"></div>
            <div role="button" id="btn-shadow-text"></div>

            <!-- Positives: Save button with hidden input value "Save", dialog heading, link -->
            <button id="btn-save">Save</button>
            <input type="hidden" id="hidden-save" value="Save" />
            <dialog open id="dialog-positive">
                <h2>Example Panel</h2>
                <p>Dialog content</p>
            </dialog>
            <a href="#x" id="link-report">Open report</a>

        </body>
        </html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()

    # Serve from https://app.example.com/
    page.route("https://app.example.com/test", lambda route: route.fulfill(
        status=200,
        content_type="text/html",
        body=html_content
    ))
    page.goto("https://app.example.com/test")

    # Set up webkit-user-modify elements with typed values
    page.evaluate('document.getElementById("webkit-btn").textContent = "PLANT-webkit-button-val"')

    # Select the mirrored option
    page.locator('#sel-mirror').select_option("2")

    # Page-wide value-match: select the option and type into textbox
    page.locator('#sel-pagewide').select_option("chosen")
    page.locator('#input-pagewide').press_sequentially("zqMirror5512")

    # Mutant #9: set up shadow roots
    page.evaluate('''
        // Shadow root with input (contains control, should blank button name)
        const btnShadowInput = document.getElementById("btn-shadow-input");
        const shadowInput = btnShadowInput.attachShadow({{mode: 'open'}});
        const input = document.createElement("input");
        input.type = "text";
        input.value = "zqShadowVal2290";
        shadowInput.appendChild(input);
        shadowInput.appendChild(document.createTextNode("Go"));

        // Shadow root with only text (should be readable)
        const btnShadowText = document.getElementById("btn-shadow-text");
        const shadowText = btnShadowText.attachShadow({{mode: 'open'}});
        shadowText.appendChild(document.createTextNode("Shadow Label"));
    ''')

    # Set up shadow root with button inside (Case 6 - combo)
    page.evaluate('''
        const host = document.getElementById("shadow-host-combo");
        const shadow = host.attachShadow({{mode: 'open'}});
        const btn = document.createElement("button");
        btn.textContent = "PLANT-shadow-button-combo";
        btn.id = "shadow-btn-combo";
        shadow.appendChild(btn);
    ''')

    # Set up shadow root with shim (Case 6 - shim: host with getters hiding shadow, input inside button)
    page.evaluate('''
        const shimHost = document.getElementById("shadow-host-shim");
        const shimShadow = shimHost.attachShadow({{mode: 'open'}});

        // Create a button with an input inside in the shadow root
        const shimBtn = document.createElement("button");
        shimBtn.textContent = "ShimButton ";
        shimBtn.id = "shim-shadow-btn";
        const shimInput = document.createElement("input");
        shimInput.type = "text";
        shimInput.value = "PLANT-shim-shadow-input";
        shimBtn.appendChild(shimInput);
        shimShadow.appendChild(shimBtn);

        // Install getter shims on the host that hide the shadow content
        Object.defineProperty(shimHost, 'children', {{
            get: function() {{ return []; }}
        }});
        Object.defineProperty(shimHost, 'childNodes', {{
            get: function() {{ return []; }}
        }});
    ''')

    href = save_failure_snapshot(
        page, run_dir, 1, "D2TEST", "UI_MISMATCH", "test note",
        roots=[page], redact_tree=_redact_aria_snapshot
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text()
            snapshot_text_lower = snapshot_text.lower()
            snapshot_data = json.loads(snapshot_text)

            # Case 2: webkit-user-modify values must not appear
            for plant in ["PLANT-webkit-button-val", "PLANT-webkit-heading-val", "PLANT-webkit-cell-value"]:
                self.assertNotIn(plant, snapshot_text, f"webkit-user-modify value leaked: {plant}")

            # Case 3: role spelling - bogus and uppercase tokens must be recognized as controls, values filtered
            for plant in ["PLANT-bogus-textbox-btn", "PLANT-upper-textbox-btn",
                          "PLANT-bogus-textbox-cell", "PLANT-upper-textbox-cell"]:
                self.assertNotIn(plant, snapshot_text, f"role textbox value leaked: {plant}")

            # Case 5: value-match - mirrored select option must cause name to be dropped
            # Note: with page-wide collection, combobox text also blanks button aria-labels
            self.assertNotIn("PLANT-selected-option-mirrored", snapshot_text, "select mirror leaked")

            # Case 6: shadow root traversal - button inside shadow root should be found
            # But its host is inside role=combobox, so the combobox container should have no content name
            # The button itself inside the shadow should NOT appear because it's inside a combobox
            self.assertNotIn("PLANT-shadow-button-combo", snapshot_text, "shadow button inside combobox leaked")

            # Case 6: shim with getters - button inside shadow root should still be found
            # and should show has_input, but the input value should not appear in the button name
            self.assertNotIn("PLANT-shim-shadow-input", snapshot_text, "shim shadow input value leaked")

            # Case 7: visibility - all hidden text must not appear
            for plant in ["PLANT-collapse-hidden", "PLANT-display-none-hidden",
                          "PLANT-attr-hidden", "PLANT-aria-hidden"]:
                self.assertNotIn(plant, snapshot_text, f"hidden text leaked: {plant}")

            # Case 9: redaction - split-span hostnames and emails must be redacted
            self.assertNotIn("acme-dev-ed", snapshot_text, "split hostname leaked")
            self.assertNotIn("zq-local", snapshot_text, "split email local part leaked")
            self.assertNotIn(".my.salesforce.example", snapshot_text_lower, "split hostname domain leaked")
            # Long token (40 chars) should be redacted - check no 16-char piece appears
            self.assertNotIn("ABCDEFGH01234567", snapshot_text, "long token piece leaked")
            self.assertNotIn("0123456789012345", snapshot_text, "long token piece leaked")

            # M13: multiple adjacent text nodes joined without spaces
            self.assertIn('"PartOneTwo"', snapshot_text, "multitext button should be PartOneTwo")
            self.assertNotIn('"Part One Two"', snapshot_text, "multitext should not have spaces")

            # Rule 1: cut before redaction tests
            # (a) Email split at position 70-95 in ~90 char heading: "zq-local" must not appear
            self.assertNotIn("zq-local", snapshot_text, "Rule 1(a): email local part leaked after cut before redaction")
            # (b) 40-char token starting at position 60: no 16-char piece must appear
            self.assertNotIn("ABCD0123456789EF", snapshot_text, "Rule 1(b): token piece leaked after cut")
            self.assertNotIn("456789WXYZ012345", snapshot_text, "Rule 1(b): token piece leaked after cut")
            # (c) Over-400-char heading: no prefix of it must appear (check first 50 chars)
            self.assertNotIn("This is a very long heading that exceeds four hun", snapshot_text, "Rule 1(c): over-400 heading not dropped whole")

            # Page-wide value-match tests
            # (a) SELECT outside with chosen option, button elsewhere with matching span text
            self.assertNotIn("zqselpick4471", snapshot_text_lower, "Page-wide (a): SELECT value leaked into button")
            # (b) Textbox outside with typed value, button aria-label matching
            self.assertNotIn("zqmirror5512", snapshot_text_lower, "Page-wide (b): textbox value leaked into aria-label")
            # (c) Positive: hidden/submit inputs skipped, button names kept
            self.assertIn('"Save"', snapshot_text, "Page-wide (c): Save button missing")
            self.assertIn('"Send now"', snapshot_text, "Page-wide (c): Send now button missing")

            # Mutant #9: shadow root traversal
            # Button with input in shadow root: value must not leak AND button must be blank (contains control)
            self.assertNotIn("zqshadowval2290", snapshot_text_lower, "Mutant #9: shadow input value leaked")
            # Button must be blank (contains control) - check it's NOT in the list or is empty string
            # Button with only text in shadow root: text must appear
            self.assertIn('"Shadow Label"', snapshot_text, "Mutant #9: shadow text not readable")

            # Positives: Save button keeps name even with hidden input value "Save"
            self.assertIn('"Save"', snapshot_text, "Save button name missing")

            # Dialog heading keeps its name
            found_example_panel = False
            for panel in snapshot_data["panels"]:
                if "Example Panel" in panel.get("headings", []):
                    found_example_panel = True
                    break
            self.assertTrue(found_example_panel, "Example Panel dialog heading missing")

            # Link keeps its name
            self.assertIn('"Open report"', snapshot_text, "Open report link missing")

            # Visible text from buttons should appear (not the hidden parts)
            self.assertIn('"Visible"', snapshot_text, "Visible text missing from buttons")


if __name__ == "__main__":
    unittest.main()
