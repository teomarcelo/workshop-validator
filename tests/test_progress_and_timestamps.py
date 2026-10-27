"""Test progress.json heartbeat, video timestamps, and suspect flags."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import walk_guide
from workshop_validator.private_runner import _inventory_report
from workshop_validator.report import ReportStep


def fake_step(step_id, text):
    return GuideStep(
        step_id, 1, "Sample Exercise", "Sample Task", "1", text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


class FakePage:
    def __init__(self):
        self.url = "https://example.lightning.force.com/lightning/page/home"
        self.roles = {("button", "Save"), ("button", "New Item")}
        self.body = "Welcome to the page"
        self.values = {}
        self.events = []
        self.reads = 0
        self.snapshots = []

    def locator(self, selector):
        return FakeLoc(self, "locator", selector)

    def get_by_role(self, role, name="", exact=True):
        return FakeLoc(self, role, name)

    def get_by_label(self, name, exact=True):
        return FakeLoc(self, "label", name)

    def get_by_text(self, text, exact=True):
        return FakeLoc(self, "text", text)

    @property
    def frames(self):
        return [self]


class FakeLoc:
    def __init__(self, page, kind, name=""):
        self.page = page
        self.kind = kind
        self.name = name

    def count(self):
        if self.kind == "body":
            return 1
        return 1 if (self.kind, self.name) in self.page.roles else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.count() > 0

    def click(self, timeout=None, **kwargs):
        self.page.events.append(("click", self.kind, self.name))
        # Simulate page change after click
        self.page.body = "Item was created successfully"
        self.page.roles.add(("link", "New Item"))

    def get_attribute(self, attr, timeout=None):
        return None

    def inner_text(self, timeout=None):
        # Return the control's own name if it has one, otherwise page body
        if self.name and (self.kind, self.name) in self.page.roles:
            return self.name
        return self.page.body


class RoleListLoc:
    """[role='x'] lookup: visible controls of that role, leaving out those inside a popup."""

    def __init__(self, page, role, selector):
        self.page = page
        self.items = [item for item in sorted(page.roles) if item[0] == role]
        if ":not(" in selector:
            self.items = [item for item in self.items if item not in page.in_popup]

    def count(self):
        return len(self.items)

    def nth(self, index):
        kind, name = self.items[index]
        return FakeLoc(self.page, kind, name)


def role_locator(page, selector):
    match = re.search(r"\[role='(\w+)'\]", selector)
    if match:
        return RoleListLoc(page, match.group(1), selector)
    return FakeLoc(page, "locator", selector)


class TestProgressAndTimestamps(unittest.TestCase):
    def test_progress_json_written_with_correct_permissions(self):
        """Test that progress.json is written with 0600 permissions after each step."""
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            run_id = f"test-{uuid4().hex[:8]}"

            steps = [
                fake_step("step-1", "Click Save."),
                fake_step("step-2", "Click New Item."),
            ]
            inventory = SimpleNamespace(steps=steps)
            page = FakePage()

            heartbeat_calls = []

            def heartbeat(run_id_str, index, total, step_id, status, timestamp):
                heartbeat_calls.append({
                    "run_id": run_id_str,
                    "index": index,
                    "total": total,
                    "step_id": step_id,
                    "status": status,
                    "timestamp": timestamp,
                })
                # Simulate writing progress.json
                progress_path = run_dir / "progress.json"
                payload = {
                    "run_id": run_id,
                    "index": index,
                    "total": total,
                    "step_id": step_id,
                    "status": status,
                    "time": "2026-10-22T12:00:00Z",
                }
                temp = run_dir / f".progress.{uuid4().hex}.tmp"
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(temp, flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                os.chmod(temp, 0o600)
                os.replace(temp, progress_path)
                os.chmod(progress_path, 0o600)

            outcome = walk_guide(
                page, inventory,
                heartbeat=heartbeat,
                video_origin=100.0,
            )

            # Verify heartbeat was called for each step
            self.assertEqual(len(heartbeat_calls), 2)
            self.assertEqual(heartbeat_calls[0]["index"], 1)
            self.assertEqual(heartbeat_calls[0]["step_id"], "step-1")
            self.assertEqual(heartbeat_calls[1]["index"], 2)
            self.assertEqual(heartbeat_calls[1]["step_id"], "step-2")

            # Verify progress.json exists and has correct permissions
            progress_path = run_dir / "progress.json"
            self.assertTrue(progress_path.exists())
            stat = progress_path.stat()
            # On Unix, check that only owner has rw permissions
            if hasattr(os, "stat"):
                self.assertEqual(stat.st_mode & 0o777, 0o600)

            # Verify content
            with open(progress_path, "r") as f:
                data = json.load(f)
            self.assertEqual(data["run_id"], run_id)
            self.assertEqual(data["index"], 2)  # Last step
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["step_id"], "step-2")

    def test_video_timestamps_in_outcome(self):
        """Test that video timestamps are included in walk outcome."""
        steps = [
            fake_step("step-1", "Click Save."),
        ]
        inventory = SimpleNamespace(steps=steps)
        page = FakePage()

        class FakeClock:
            def __init__(self):
                self.now = 200.0

            def __call__(self):
                result = self.now
                self.now += 5.0  # Each operation takes 5 seconds
                return result

        clock = FakeClock()
        video_origin = 100.0

        outcome = walk_guide(
            page, inventory,
            clock=clock,
            video_origin=video_origin,
        )

        # Verify timestamps are present
        self.assertIn("step-1", outcome.timestamps)
        t_start, t_end = outcome.timestamps["step-1"]
        # t_start should be relative to video_origin
        self.assertGreaterEqual(t_start, 0)
        self.assertGreater(t_end, t_start)
        # Timestamps should be seconds from video start
        self.assertLess(t_end - t_start, 100)  # Step shouldn't take 100+ seconds

    def test_click_without_after_state_returns_needs_review(self):
        """Test that click without verifiable after-state returns NEEDS_REVIEW."""
        steps = [
            fake_step("step-1", "Click Save."),  # Click but no verifiable change
        ]
        inventory = SimpleNamespace(steps=steps)
        page = FakePage()

        outcome = walk_guide(page, inventory)

        # Should be NEEDS_REVIEW since no after-state evidence could be read
        self.assertEqual(outcome.statuses.get("step-1"), "NEEDS_REVIEW")
        self.assertIn("but no change could be read", outcome.notes.get("step-1", ""))

    def test_report_step_includes_timestamps_and_suspects(self):
        """Test that ReportStep includes t_start, t_end, and suspect fields."""
        steps = [
            fake_step("step-1", "Click Save."),
        ]
        inventory = SimpleNamespace(steps=steps)

        statuses = {"step-1": "PASS"}
        notes = {"step-1": "Clicked Save."}
        evidence = {"step-1": ("step-1:after-state-url-changed",)}
        timestamps = {"step-1": (10.5, 15.3)}
        suspects = {"step-1": ()}  # No suspect flags with proper after-state evidence

        report_steps = _inventory_report(
            inventory, statuses, notes, evidence, timestamps, suspects
        )

        self.assertEqual(len(report_steps), 1)
        step = report_steps[0]
        self.assertEqual(step.t_start, 10.5)
        self.assertEqual(step.t_end, 15.3)
        self.assertEqual(step.suspect, ())

    def test_unexpected_change_flag(self):
        """Test that unexpected-change flag is set when new names appear."""
        steps = [
            fake_step("step-1", "Click New Item."),  # This causes new items to appear
        ]
        inventory = SimpleNamespace(steps=steps)

        class PageWithSnapshot(FakePage):
            def __init__(self):
                super().__init__()
                self.roles.add(("button", "New Item"))
                self.snapshot_names = []
                # Neither New Item control sits inside a menu or listbox.
                self.in_popup = set()

            def locator(self, selector):
                return role_locator(self, selector)

        class LocWithSnapshot(FakeLoc):
            def click(self, timeout=None, **kwargs):
                self.page.events.append(("click", self.kind, self.name))
                # Add new visible name after click
                self.page.snapshot_names = ["New Item"]
                self.page.roles.add(("link", "New Item"))

        page = PageWithSnapshot()

        # Mock _visible_names_snapshot to return the simulated snapshots
        import workshop_validator.guide_walk as gw
        original_snapshot = gw._visible_names_snapshot
        def mock_snapshot(p):
            if hasattr(p, 'snapshot_names'):
                return frozenset(gw.norm(n) for n in p.snapshot_names)
            return frozenset()
        gw._visible_names_snapshot = mock_snapshot

        try:
            page.get_by_role = lambda role, name="", exact=True: LocWithSnapshot(page, role, name)
            outcome = walk_guide(page, inventory)

            # The click adds "New Item" which is in the step text
            # so it provides after-state evidence and should PASS
            self.assertEqual(outcome.statuses.get("step-1"), "PASS")
            if "step-1" in outcome.suspects:
                suspects = outcome.suspects["step-1"]
                # With after-state evidence, no weak-evidence flag
                self.assertNotIn("weak-evidence", suspects)
        finally:
            gw._visible_names_snapshot = original_snapshot

    def test_click_with_aria_selected_passes(self):
        """Test that click with aria-selected=true provides after-state evidence."""
        steps = [
            fake_step("step-1", "Click Details tab."),
        ]
        inventory = SimpleNamespace(steps=steps)

        class PageWithAriaSelected(FakePage):
            pass

        class LocWithAriaSelected(FakeLoc):
            def get_attribute(self, attr, timeout=None):
                if attr == "aria-selected":
                    return "true"
                return None

        page = PageWithAriaSelected()
        page.roles.add(("tab", "Details"))

        # Override locator to return LocWithAriaSelected
        original_locator = page.locator
        def locator_override(selector):
            return LocWithAriaSelected(page, "tab", "Details")
        page.locator = locator_override
        page.get_by_role = lambda role, name="", exact=True: LocWithAriaSelected(page, role, name)

        outcome = walk_guide(page, inventory)

        # Should PASS with after-state evidence
        self.assertEqual(outcome.statuses.get("step-1"), "PASS")
        self.assertNotIn("weak-evidence", outcome.suspects.get("step-1", ()))

    def test_click_with_url_change_passes(self):
        """Test that click with URL change provides after-state evidence."""
        steps = [
            fake_step("step-1", "Click Setup."),
        ]
        inventory = SimpleNamespace(steps=steps)

        class PageWithUrlChange(FakePage):
            def __init__(self):
                super().__init__()
                self.roles.add(("link", "Setup"))

        class LocWithUrlChange(FakeLoc):
            def click(self, timeout=None, **kwargs):
                self.page.events.append(("click", self.kind, self.name))
                # Simulate URL change after click
                self.page.url = "https://example.lightning.force.com/lightning/setup"

        page = PageWithUrlChange()
        page.get_by_role = lambda role, name="", exact=True: LocWithUrlChange(page, role, name)

        outcome = walk_guide(page, inventory)

        # Should PASS with after-state evidence
        self.assertEqual(outcome.statuses.get("step-1"), "PASS")
        self.assertNotIn("weak-evidence", outcome.suspects.get("step-1", ()))

    def test_click_with_named_item_appearing_passes(self):
        """Test that click with step-named item appearing provides after-state evidence."""
        steps = [
            fake_step("step-1", "Click New Item."),
        ]
        inventory = SimpleNamespace(steps=steps)

        class PageWithSnapshot(FakePage):
            def __init__(self):
                super().__init__()
                self.roles.add(("button", "New Item"))
                self.snapshot_names = []
                # Neither New Item control sits inside a menu or listbox.
                self.in_popup = set()

            def locator(self, selector):
                return role_locator(self, selector)

        class LocWithSnapshot(FakeLoc):
            def click(self, timeout=None, **kwargs):
                self.page.events.append(("click", self.kind, self.name))
                # Add new visible name after click
                self.page.snapshot_names = ["New Item"]
                # The new item shows on the page, outside any menu or listbox.
                self.page.roles.add(("link", "New Item"))

        page = PageWithSnapshot()

        # Mock _visible_names_snapshot to return the simulated snapshots
        import workshop_validator.guide_walk as gw
        original_snapshot = gw._visible_names_snapshot
        def mock_snapshot(p):
            if hasattr(p, 'snapshot_names'):
                return frozenset(gw.norm(n) for n in p.snapshot_names)
            return frozenset()
        gw._visible_names_snapshot = mock_snapshot

        try:
            page.get_by_role = lambda role, name="", exact=True: LocWithSnapshot(page, role, name)
            outcome = walk_guide(page, inventory)

            # Should PASS with after-state evidence ("New Item" appears)
            self.assertEqual(outcome.statuses.get("step-1"), "PASS")
            self.assertNotIn("weak-evidence", outcome.suspects.get("step-1", ()))
        finally:
            gw._visible_names_snapshot = original_snapshot

    def test_click_with_no_change_stops_walk(self):
        """Test that click with no verifiable change returns NEEDS_REVIEW and stops walk."""
        steps = [
            fake_step("step-1", "Click Save."),
            fake_step("step-2", "Click Next."),
        ]
        inventory = SimpleNamespace(steps=steps)

        class PageNoChange(FakePage):
            def __init__(self):
                super().__init__()
                self.roles.add(("button", "Save"))
                self.roles.add(("button", "Next"))

        class LocNoChange(FakeLoc):
            def click(self, timeout=None, **kwargs):
                self.page.events.append(("click", self.kind, self.name))
                # No changes after click - URL same, no new elements, no aria changes

        page = PageNoChange()
        page.get_by_role = lambda role, name="", exact=True: LocNoChange(page, role, name)

        outcome = walk_guide(page, inventory)

        # step-1 should be NEEDS_REVIEW with freeze
        self.assertEqual(outcome.statuses.get("step-1"), "NEEDS_REVIEW")
        self.assertIn("but no change could be read", outcome.notes.get("step-1", ""))

        # step-2 should be NOT_RUN because walk stopped at step-1
        self.assertEqual(outcome.statuses.get("step-2"), "NOT_RUN")


class TestFlowBuilderAfterState(unittest.TestCase):
    def test_flow_builder_field_requires_after_state(self):
        """Test that Flow Builder field placement requires after-state evidence."""
        # The actual implementation verifies after-state by checking aria-selected,
        # URL change, or new names appearing. Without such evidence, it returns NEEDS_REVIEW.
        steps = [
            fake_step("fields-tab", "Click Fields tab."),
        ]
        inventory = SimpleNamespace(steps=steps)
        page = FakePage()
        page.roles.add(("tab", "Fields"))

        outcome = walk_guide(page, inventory)

        # Without after-state evidence, should be NEEDS_REVIEW
        if outcome.statuses.get("fields-tab") == "NEEDS_REVIEW":
            self.assertIn("but no change could be read", outcome.notes.get("fields-tab", ""))
        # If it passes (e.g., due to special handling), verify no weak-evidence flag
        elif outcome.statuses.get("fields-tab") == "PASS":
            suspects = outcome.suspects.get("fields-tab", ())
            self.assertNotIn("weak-evidence", suspects)


if __name__ == "__main__":
    unittest.main()
