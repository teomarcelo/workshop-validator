"""Tests for per-page video timeline tracking (I26-1)."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class TestTimelineRecording(unittest.TestCase):
    """Test that timeline data structure exists and can be recorded."""

    def test_null_video_basename_not_fallback(self):
        """When video handle is missing, video_basename is None, not 'walk.webm'."""
        from workshop_validator.guide_walk import walk_guide, GuideCheck, CheckResult
        from workshop_validator.guide_inventory import GuideInventory, GuideStep

        # Create a fake page with no video attribute
        class FakePage:
            def __init__(self):
                self.url = "https://example.com"
                # No video attribute at all

        page = FakePage()

        # Create inventory with one step
        steps = [
            GuideStep(
                "T-NULL-VIDEO", 1, "Ex 1", "Task 1", "1", "Step without video",
                (0, 0, 100, 100), None, ()
            ),
        ]
        inventory = GuideInventory(1, steps, (), ())

        # Check that always passes
        def run_check(state, step):
            return CheckResult(
                status="PASS",
                evidence_ids=("test",),
                note="",
                freeze=False,
                suspect=(),
                snapshot="",
                guide_note=""
            )

        checks = [GuideCheck("check", lambda s: s.step_id == "T-NULL-VIDEO", run_check)]

        # Run walk without page_creation_times
        outcome = walk_guide(page, inventory, checks=checks)

        # Video should be None, not "walk.webm" (mutant m3)
        self.assertIn("T-NULL-VIDEO", outcome.timeline)
        video, t_start, t_end, snapshot = outcome.timeline["T-NULL-VIDEO"]
        self.assertIsNone(video, "Video should be None when handle is missing, not fallback to walk.webm")

    def test_timeline_in_outcome(self):
        """WalkOutcome includes timeline field with snapshot."""
        from workshop_validator.guide_walk import WalkOutcome

        # Create an outcome with timeline data
        outcome = WalkOutcome(
            statuses={"s1": "PASS"},
            notes={},
            evidence={"s1": ("test",)},
            executed=("s1",),
            suspects={},
            timestamps={"s1": (10.0, 20.0)},
            snapshots={},
            guide_notes={},
            freezes={},
            timeline={"s1": ("video1.webm", 5.0, 15.0, "snap1.json")},
        )

        self.assertIn("s1", outcome.timeline)
        video, t_start, t_end, snapshot = outcome.timeline["s1"]
        self.assertEqual(video, "video1.webm")
        self.assertEqual(t_start, 5.0)
        self.assertEqual(t_end, 15.0)
        self.assertEqual(snapshot, "snap1.json")

    def test_timeline_with_null_values(self):
        """Timeline can contain null values for missing data."""
        from workshop_validator.guide_walk import WalkOutcome

        outcome = WalkOutcome(
            statuses={"s1": "PASS"},
            notes={},
            evidence={"s1": ("test",)},
            executed=("s1",),
            suspects={},
            timestamps={"s1": (10.0, 20.0)},
            snapshots={},
            guide_notes={},
            freezes={},
            timeline={"s1": (None, None, None, "")},
        )

        self.assertIn("s1", outcome.timeline)
        video, t_start, t_end, snapshot = outcome.timeline["s1"]
        self.assertIsNone(video)
        self.assertIsNone(t_start)
        self.assertIsNone(t_end)
        self.assertEqual(snapshot, "")


class TestTimelineFile(unittest.TestCase):
    """Test that timeline.json is written correctly."""

    def test_timeline_file_mode_600(self):
        """timeline.json is written with mode 0600."""
        from workshop_validator.private_runner import _write_timeline

        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir)
            timeline = {
                "s1": ("video1.webm", 10.0, 20.0, "snap1.json"),
                "s2": ("video2.webm", 5.0, 15.0, "snap2.json"),
            }

            _write_timeline(work, "test-run", timeline, "password123")

            timeline_path = work / "timeline.json"
            self.assertTrue(timeline_path.exists())

            # Check mode is 0600
            stat = timeline_path.stat()
            mode = stat.st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_timeline_not_overwritten(self):
        """timeline.json is never overwritten if it exists."""
        from workshop_validator.private_runner import _write_timeline

        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir)
            timeline_path = work / "timeline.json"

            # Write initial content
            timeline_path.write_text("original content")
            timeline_path.chmod(0o600)

            # Try to write timeline
            timeline = {
                "s1": ("video1.webm", 10.0, 20.0, "snap1.json"),
            }
            _write_timeline(work, "test-run", timeline, "password123")

            # Content should not change
            self.assertEqual(timeline_path.read_text(), "original content")


    def test_timeline_not_written_with_password_leak(self):
        """timeline.json is not written if password is in the data."""
        from workshop_validator.private_runner import _write_timeline

        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir)
            password = "secretpass"

            # Timeline with password in step_id
            timeline = {
                f"step-{password}": ("video1.webm", 10.0, 20.0, "snap1.json"),
            }

            _write_timeline(work, "test-run", timeline, password)

            timeline_path = work / "timeline.json"
            # File should not be created
            self.assertFalse(timeline_path.exists())

    def test_timeline_json_structure(self):
        """timeline.json has the correct structure including snapshot."""
        from workshop_validator.private_runner import _write_timeline

        with tempfile.TemporaryDirectory() as tmpdir:
            work = Path(tmpdir)
            timeline = {
                "T-ALPHA-1": ("video1.webm", 10.5, 20.3, "snap-alpha.json"),
                "T-BETA-2": (None, None, None, ""),
            }

            _write_timeline(work, "test-run-id", timeline, "password123")

            timeline_path = work / "timeline.json"
            self.assertTrue(timeline_path.exists())

            with open(timeline_path) as f:
                data = json.load(f)

            self.assertEqual(data["run_id"], "test-run-id")
            self.assertEqual(len(data["steps"]), 2)

            # Find steps by id
            steps_by_id = {s["step_id"]: s for s in data["steps"]}

            self.assertIn("T-ALPHA-1", steps_by_id)
            s1 = steps_by_id["T-ALPHA-1"]
            self.assertEqual(s1["video"], "video1.webm")
            self.assertAlmostEqual(s1["t_start"], 10.5)
            self.assertAlmostEqual(s1["t_end"], 20.3)
            self.assertEqual(s1["snapshot"], "snap-alpha.json")

            self.assertIn("T-BETA-2", steps_by_id)
            s2 = steps_by_id["T-BETA-2"]
            self.assertIsNone(s2["video"])
            self.assertIsNone(s2["t_start"])
            self.assertIsNone(s2["t_end"])
            self.assertEqual(s2["snapshot"], "")


class TestSuspectPassSnapshot(unittest.TestCase):
    """Test that PASS rows with suspect chips keep their snapshot."""

    def test_suspect_pass_keeps_snapshot(self):
        """A PASS row with suspect chips keeps its snapshot path."""
        from workshop_validator.private_runner import _inventory_report
        from workshop_validator.guide_inventory import GuideInventory, GuideStep

        steps = [
            GuideStep(
                "T-ALPHA-1", 1, "Ex 1", "Task 1", "1", "Step with suspect",
                (0, 0, 100, 100), None, ()
            ),
        ]
        inventory = GuideInventory(1, steps, (), ())

        statuses = {"T-ALPHA-1": "PASS"}
        evidence = {"T-ALPHA-1": ("test:already-set",)}
        suspects = {"T-ALPHA-1": ("already-set", "popup-closed")}
        snapshots = {"T-ALPHA-1": "snap-alpha.json"}

        result = _inventory_report(
            inventory, statuses, {}, evidence, {}, suspects, snapshots, {}, {}
        )

        self.assertEqual(len(result), 1)
        step = result[0]
        self.assertEqual(step.status, "PASS")
        self.assertEqual(step.snapshot, "snap-alpha.json")

    def test_clean_pass_drops_snapshot(self):
        """A PASS row without suspect chips drops its snapshot path."""
        from workshop_validator.private_runner import _inventory_report
        from workshop_validator.guide_inventory import GuideInventory, GuideStep

        steps = [
            GuideStep(
                "T-BETA-1", 1, "Ex 1", "Task 1", "1", "Clean step",
                (0, 0, 100, 100), None, ()
            ),
        ]
        inventory = GuideInventory(1, steps, (), ())

        statuses = {"T-BETA-1": "PASS"}
        evidence = {"T-BETA-1": ("test",)}
        suspects = {}  # No suspect chips
        snapshots = {"T-BETA-1": "snap-beta.json"}

        result = _inventory_report(
            inventory, statuses, {}, evidence, {}, suspects, snapshots, {}, {}
        )

        self.assertEqual(len(result), 1)
        step = result[0]
        self.assertEqual(step.status, "PASS")
        self.assertEqual(step.snapshot, "")  # Snapshot dropped for clean PASS


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1", "Browser smoke only")
class TestPerPageTimelineBrowserSmoke(unittest.TestCase):
    """Browser smoke test for per-page video timeline with actual Chromium recording."""

    def test_two_pages_different_videos_and_offsets(self):
        """Each page gets its own video and offsets are measured from page creation."""
        from workshop_validator.guide_walk import walk_guide
        from workshop_validator.guide_inventory import GuideInventory, GuideStep

        with tempfile.TemporaryDirectory() as tmpdir:
            video_dir = Path(tmpdir)

            # Create inventory with two steps
            steps = [
                GuideStep(
                    "T-PAGE1-STEP", 1, "Ex 1", "Task 1", "1", "Click link on page 1",
                    (0, 0, 100, 100), None, ()
                ),
                GuideStep(
                    "T-PAGE2-STEP", 1, "Ex 1", "Task 1", "2", "Click button on page 2",
                    (0, 100, 100, 200), None, ()
                ),
            ]
            inventory = GuideInventory(1, steps, (), ())

            pw = sync_playwright().start()
            try:
                executable = os.environ.get('CHROMIUM_PATH') or None
                browser = pw.chromium.launch(headless=True, executable_path=executable)
                try:
                    context = browser.new_context(record_video_dir=str(video_dir))
                    try:
                        page1 = context.new_page()
                        page1_creation = time.monotonic()

                        # Navigate to a proper URL (use data: URL to avoid network)
                        page1.goto("data:text/html,<html><body><h1>Page 1</h1><button id='btn1'>Button 1</button></body></html>")

                        # Track page creation times
                        page_creation_times = {}
                        try:
                            video = page1.video
                            if callable(video):
                                video = video()
                            if video:
                                path_of = getattr(video, "path", None)
                                if callable(path_of):
                                    video_path = path_of()
                                    if video_path:
                                        page_creation_times[video_path] = page1_creation
                        except Exception:
                            pass

                        # Wait a bit so pages have different creation times
                        time.sleep(0.5)

                        # Create page 2 directly
                        page2 = context.new_page()
                        page2_creation = time.monotonic()
                        page2.goto("data:text/html,<html><body><h1>Page 2</h1><button id='btn2'>Button 2</button></body></html>")

                        try:
                            video = page2.video
                            if callable(video):
                                video = video()
                            if video:
                                path_of = getattr(video, "path", None)
                                if callable(path_of):
                                    video_path = path_of()
                                    if video_path:
                                        page_creation_times[video_path] = page2_creation
                        except Exception:
                            pass

                        # Mock checks that always pass
                        from workshop_validator.guide_walk import GuideCheck, CheckResult

                        # Store page2 in a mutable container for closure
                        pages = {'page2': page2}

                        def run_check1(state, step):
                            return CheckResult(
                                status="PASS",
                                evidence_ids=("test1",),
                                note="",
                                freeze=False,
                                suspect=(),
                                snapshot="",
                                guide_note=""
                            )

                        def run_check2(state, step):
                            # Switch to page2 for the second step
                            state.page = pages['page2']
                            return CheckResult(
                                status="PASS",
                                evidence_ids=("test2",),
                                note="",
                                freeze=False,
                                suspect=(),
                                snapshot="",
                                guide_note=""
                            )

                        checks = [
                            GuideCheck("check1", lambda s: s.step_id == "T-PAGE1-STEP", run_check1),
                            GuideCheck("check2", lambda s: s.step_id == "T-PAGE2-STEP", run_check2),
                        ]

                        # Run walk_guide
                        outcome = walk_guide(
                            page1,
                            inventory,
                            checks=checks,
                            page_creation_times=page_creation_times,
                            video_origin=page1_creation,
                        )

                        # Assertions
                        self.assertIn("T-PAGE1-STEP", outcome.timeline)
                        self.assertIn("T-PAGE2-STEP", outcome.timeline)

                        video1, t_start1, t_end1, snap1 = outcome.timeline["T-PAGE1-STEP"]
                        video2, t_start2, t_end2, snap2 = outcome.timeline["T-PAGE2-STEP"]

                        # Each step should name its own video file (different basenames)
                        self.assertIsNotNone(video1)
                        self.assertIsNotNone(video2)

                        # Mutant m2: both steps get "walk.webm" - check they are different
                        self.assertNotEqual(video1, video2, "Steps on different pages must have different video files")

                        # Mutant m3: missing handle falls back to "walk.webm" - check not hardcoded
                        self.assertNotEqual(video1, "walk.webm", "Video1 must not be hardcoded to walk.webm")
                        self.assertNotEqual(video2, "walk.webm", "Video2 must not be hardcoded to walk.webm")

                        # Mutant m1: offsets measured from walk start instead of per-page
                        # With correct code (per-page origins):
                        #   - t_start1 measures from page1 creation, step runs after page2 is created (~0.5s later)
                        #   - t_start2 measures from page2 creation, step runs immediately (~0.02s)
                        #   - difference is large (~0.5s, the sleep time between page creations)
                        # With m1 applied (both use walk start = page1 creation):
                        #   - Both measure from page1 creation, steps run at same time
                        #   - difference is tiny (~0s)
                        offset_diff = abs(t_start2 - t_start1)
                        self.assertGreater(offset_diff, 0.4,
                            f"Offsets should differ by ~sleep time (per-page origins), not be similar. "
                            f"Got diff={offset_diff:.3f}; if < 0.4, suggests same origin (m1 applied)")

                        # Offsets should be non-negative and reasonable
                        self.assertIsNotNone(t_start1)
                        self.assertIsNotNone(t_end1)
                        self.assertIsNotNone(t_start2)
                        self.assertIsNotNone(t_end2)

                        self.assertGreaterEqual(t_start1, 0)
                        self.assertGreaterEqual(t_end1, t_start1)
                        self.assertGreaterEqual(t_start2, 0)
                        self.assertGreaterEqual(t_end2, t_start2)

                        # Page 2's offset should be measured from its own creation time,
                        # not from page 1's creation time. Since page 2 was created later,
                        # if we measured from page 1 creation, the offset would be much larger.
                        # This tests that we're using per-page origins, not the walk start.
                        self.assertLess(t_start2, 10.0, "Page 2 offset should be small (< 10s from its own creation)")

                    finally:
                        context.close()
                finally:
                    browser.close()
            finally:
                pw.stop()


if __name__ == "__main__":
    unittest.main()
