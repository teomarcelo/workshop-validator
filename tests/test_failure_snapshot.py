"""Failure snapshots: one private snapshot per non-PASS step, redacted. Synthetic pages only."""

import json
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
    def __init__(self, name="", text="", visible=True, headings=(), tag="div", role_attr=""):
        self.name, self.text, self.visible = name, text, visible
        self.heads = [Item(text=h, tag="h2") for h in headings]
        self.tag = tag
        self.role_attr = role_attr

    def is_visible(self):
        return self.visible

    def get_attribute(self, attr, timeout=None):
        if attr == "aria-label":
            return self.name
        elif attr == "role":
            return self.role_attr
        elif attr == "aria-level":
            return "2" if self.tag.startswith("h") else ""
        return None

    def text_content(self, timeout=None):
        return self.text

    def inner_text(self, timeout=None):
        return self.text

    def evaluate(self, script, timeout=None):
        return self.tag

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


class ParseAriaLineTests(unittest.TestCase):
    def test_quoted_line_with_colon_in_name_parses_role_correctly(self):
        from workshop_validator.failure_snapshot import _parse_aria_line
        # Playwright quotes lines when name contains ": "
        line = "  - 'row \"Field Label: Name\"'"
        result = _parse_aria_line(line)
        self.assertIsNotNone(result)
        indent, role, name = result
        self.assertEqual(role, "row")
        self.assertEqual(name, "Field Label: Name")
        self.assertEqual(indent, "  ")

    def test_double_quoted_line_with_escaped_quotes_parses_correctly(self):
        from workshop_validator.failure_snapshot import _parse_aria_line
        # Double-quoted outer wrapper with escaped inner quotes
        line = "  - \"button \\\"Save: Draft\\\"\""
        result = _parse_aria_line(line)
        self.assertIsNotNone(result)
        indent, role, name = result
        self.assertEqual(role, "button")
        self.assertEqual(name, "Save: Draft")

    def test_unquoted_line_parses_correctly(self):
        from workshop_validator.failure_snapshot import _parse_aria_line
        line = '- button "Save"'
        result = _parse_aria_line(line)
        self.assertIsNotNone(result)
        indent, role, name = result
        self.assertEqual(role, "button")
        self.assertEqual(name, "Save")

    def test_role_without_name_parses_correctly(self):
        from workshop_validator.failure_snapshot import _parse_aria_line
        line = "- heading"
        result = _parse_aria_line(line)
        self.assertIsNotNone(result)
        indent, role, name = result
        self.assertEqual(role, "heading")
        self.assertEqual(name, "")

    def test_text_node_returns_none(self):
        from workshop_validator.failure_snapshot import _parse_aria_line
        self.assertIsNone(_parse_aria_line("  - text: some content"))

    def test_property_line_returns_none(self):
        from workshop_validator.failure_snapshot import _parse_aria_line
        self.assertIsNone(_parse_aria_line("  - /link/"))


class RegionPage(Page):
    """Page with regions for testing."""
    def __init__(self):
        super().__init__()
        self.roles.update({
            "region": [
                Item(name="Navigation"),
                Item(name="Content", headings=("Page Title", "Section 1")),
            ],
            "tabpanel": [Item(name="Details Panel")],
            "grid": [Item(name="Data Grid", headings=("Grid Header",))],
        })


class RegionsTests(unittest.TestCase):
    def test_regions_field_includes_visible_regions(self):
        with tempfile.TemporaryDirectory() as tmp:
            walk(RegionPage(), Path(tmp), ("UI_MISMATCH",))
            data = json.loads((Path(tmp) / "snapshots" / "001-S1.json").read_text())
            self.assertIn("regions", data)
            regions = data["regions"]
            self.assertIsInstance(regions, list)
            # Should have collected region, tabpanel, grid, dialog, complementary
            self.assertGreater(len(regions), 0)
            # Check structure of a region entry
            if regions:
                first = regions[0]
                self.assertIn("role", first)
                self.assertIn("name", first)
                self.assertIn("tag", first)
                self.assertIn("role_attr", first)
                self.assertIn("frame", first)
                self.assertIn("parent", first)
                self.assertIn("headings", first)

    def test_regions_capped_at_twelve(self):
        """Guard: region cap 12"""
        page = Page()
        # Add 15 regions
        page.roles["region"] = [Item(name=f"Region {i}") for i in range(15)]
        with tempfile.TemporaryDirectory() as tmp:
            walk(page, Path(tmp), ("UI_MISMATCH",))
            data = json.loads((Path(tmp) / "snapshots" / "001-S1.json").read_text())
            # Should be capped at 12
            self.assertLessEqual(len(data["regions"]), 12)


class NamedTreePage(Page):
    """Page with a richer tree for testing named_tree."""
    def __init__(self):
        super().__init__()

    def locator(self, selector):
        self.selectors.append(selector)
        if selector == "head > title":
            return Loc([Item(text=self.title())])
        if selector == "body":
            return Loc(tree=(
                f'- heading "Page Title"\n'
                f'- region "Main Content"\n'
                f'  - button "Save"\n'
                f'  - link "Help"\n'
                f'  - textbox "Account Name": secret-value-123\n'
                f'- dialog "Confirmation"\n'
                f'  - button "OK"\n'
            ))
        return Loc()


class NamedTreeTests(unittest.TestCase):
    def test_named_tree_keeps_names_for_structural_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            walk(NamedTreePage(), Path(tmp), ("UI_MISMATCH",))
            data = json.loads((Path(tmp) / "snapshots" / "001-S1.json").read_text())
            self.assertIn("named_tree", data)
            named_tree = data["named_tree"]
            self.assertIn('heading "Page Title"', named_tree)
            self.assertIn('region "Main Content"', named_tree)
            self.assertIn('button "Save"', named_tree)
            self.assertIn('link "Help"', named_tree)
            self.assertIn('dialog "Confirmation"', named_tree)
            self.assertIn('button "OK"', named_tree)

    def test_named_tree_keeps_textbox_label_not_value(self):
        """Guard: input value dropped"""
        with tempfile.TemporaryDirectory() as tmp:
            walk(NamedTreePage(), Path(tmp), ("UI_MISMATCH",))
            data = json.loads((Path(tmp) / "snapshots" / "001-S1.json").read_text())
            named_tree = data["named_tree"]
            # Should have the label (name)
            self.assertIn('textbox "Account Name"', named_tree)
            # Should NOT have the value
            self.assertNotIn("secret-value-123", named_tree)
            # Should not have the colon and value part
            self.assertNotIn(": secret", named_tree)

    def test_named_tree_caps_names_at_eighty_chars(self):
        """Guard: name cap 80"""
        # Use a long name with spaces so it's not treated as a token
        long_name = " ".join(["Word"] * 30)  # 120+ characters with spaces

        class LongNamePage(Page):
            def locator(self, selector):
                self.selectors.append(selector)
                if selector == "head > title":
                    return Loc([Item(text=self.title())])
                if selector == "body":
                    return Loc(tree=f'- button "{long_name}"\n')
                return Loc()

        with tempfile.TemporaryDirectory() as tmp:
            walk(LongNamePage(), Path(tmp), ("UI_MISMATCH",))
            data = json.loads((Path(tmp) / "snapshots" / "001-S1.json").read_text())
            named_tree = data["named_tree"]
            # Should be truncated with ...
            self.assertIn("...", named_tree)
            # Check that no single name exceeds the cap in the output
            for line in named_tree.splitlines():
                if '"' in line:
                    # Extract the quoted part
                    start = line.find('"')
                    end = line.rfind('"')
                    if start != end and start >= 0:
                        quoted = line[start+1:end]
                        self.assertLessEqual(len(quoted), 80)


class TruncatedRolesTests(unittest.TestCase):
    def test_truncated_roles_lists_capped_control_roles(self):
        """Test that truncated_roles lists roles that hit their cap."""
        page = Page()
        # Add 70 buttons (cap is 60)
        page.roles["button"] = [Item(text=f"Button {i}") for i in range(70)]
        with tempfile.TemporaryDirectory() as tmp:
            walk(page, Path(tmp), ("UI_MISMATCH",))
            data = json.loads((Path(tmp) / "snapshots" / "001-S1.json").read_text())
            self.assertIn("truncated_roles", data)
            # button should be in the list
            self.assertIn("button", data["truncated_roles"])

    def test_truncated_roles_empty_when_under_cap(self):
        """Test that truncated_roles is empty when no roles hit cap."""
        with tempfile.TemporaryDirectory() as tmp:
            walk(Page(), Path(tmp), ("UI_MISMATCH",))
            data = json.loads((Path(tmp) / "snapshots" / "001-S1.json").read_text())
            self.assertEqual(data["truncated_roles"], [])


class NewFieldsLeakGuardTests(unittest.TestCase):
    def test_leak_guard_applied_to_regions(self):
        """Guard: leak guard applied to new fields"""
        page = RegionPage()
        page.roles["region"] = [Item(name="Setup Home")]
        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            walk_guide(
                page, GuideInventory(1, (step("S1", "UI_MISMATCH"),), (), ()),
                checks=checks_by_text(), timeout_s=0, interval_s=1, clock=clock,
                sleeper=clock.sleep, snapshot_dir=Path(tmp),
                snapshot_leaks=lambda value: "Setup Home" in value,
            )
            text = (Path(tmp) / "snapshots" / "001-S1.json").read_text()
            self.assertNotIn("Setup Home", text)
            self.assertIn("<omitted>", text)

    def test_leak_guard_applied_to_named_tree(self):
        """Guard: leak guard applied to new fields"""
        class LeakPage(Page):
            def locator(self, selector):
                self.selectors.append(selector)
                if selector == "head > title":
                    return Loc([Item(text=self.title())])
                if selector == "body":
                    return Loc(tree='- button "Secret Button"\n')
                return Loc()

        with tempfile.TemporaryDirectory() as tmp:
            clock = Clock()
            walk_guide(
                LeakPage(), GuideInventory(1, (step("S1", "UI_MISMATCH"),), (), ()),
                checks=checks_by_text(), timeout_s=0, interval_s=1, clock=clock,
                sleeper=clock.sleep, snapshot_dir=Path(tmp),
                snapshot_leaks=lambda value: "Secret Button" in value,
            )
            text = (Path(tmp) / "snapshots" / "001-S1.json").read_text()
            self.assertNotIn("Secret Button", text)
            self.assertIn("<omitted>", text)


class BudgetOverrunTests(unittest.TestCase):
    def test_budget_overrun_drops_new_fields_not_old(self):
        """Guard: budget overrun drops only new fields"""
        clock = Clock()
        page = SlowPage(clock)
        with tempfile.TemporaryDirectory() as tmp:
            from workshop_validator.guide_walk import _redact_aria_snapshot
            href = save_failure_snapshot(
                page, Path(tmp), 1, "S1", "UI_MISMATCH", "missed",
                roots=[page], redact_tree=_redact_aria_snapshot, clock=clock,
            )
            data = json.loads((Path(tmp) / href).read_text())
        # Old fields should be present
        self.assertIn("controls", data)
        self.assertIn("button", data["controls"])
        self.assertIn("panels", data)
        # New fields should be empty due to budget overrun
        self.assertEqual(data["regions"], [])
        self.assertEqual(data["named_tree"], "")
        # Truncated flag should be set
        self.assertEqual(data["truncated"], "snapshot truncated (time)")


if __name__ == "__main__":
    unittest.main()
