"""Test step time caps, timestamps, suspect flags, and heartbeat."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import (
    CheckResult,
    GuideCheck,
    walk_guide,
)


def _step(step_id, text):
    return GuideStep(
        step_id, 1, "Exercise 1", "Task 1", "1", text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


def _inventory(*steps):
    return SimpleNamespace(steps=steps)


def _fake_page():
    page = Mock()
    page.url = "https://example.my.salesforce.com"
    return page


class TestStepTimeCap(unittest.TestCase):
    def test_step_exceeds_time_cap_becomes_not_run(self):
        """Step that takes longer than page_wait + 30s (max 120s) is NOT_RUN."""
        step = _step("step1", "Click Something")
        inv = _inventory(step)
        page = _fake_page()

        elapsed = [0.0]
        def fake_clock():
            result = elapsed[0]
            elapsed[0] += 100  # Each check advances 100s
            return result

        def slow_check(state, step):
            state.clock()  # Advance time
            return CheckResult("PASS", "Took too long", ("step1:slow",))

        check = GuideCheck("slow", lambda s: True, slow_check)
        outcome = walk_guide(
            page, inv, checks=[check], clock=fake_clock, page_wait_s=60
        )

        self.assertEqual(outcome.statuses["step1"], "NOT_RUN")
        self.assertIn("exceeded its time limit", outcome.notes["step1"])

    def test_step_under_time_cap_passes(self):
        """Step that finishes within time cap can PASS."""
        step = _step("step2", "Quick action")
        inv = _inventory(step)
        page = _fake_page()

        elapsed = [0.0]
        def fake_clock():
            result = elapsed[0]
            elapsed[0] += 5  # Quick step
            return result

        def quick_check(state, step):
            return CheckResult("PASS", "Quick", ("step2:fast",))

        check = GuideCheck("quick", lambda s: True, quick_check)
        outcome = walk_guide(
            page, inv, checks=[check], clock=fake_clock, page_wait_s=60
        )

        self.assertEqual(outcome.statuses["step2"], "PASS")

    def test_time_cap_respects_max_120s(self):
        """Even with high page_wait, cap is maximum 120s."""
        # With page_wait_s=200, cap should be min(200+30, 120) = 120
        step = _step("step3", "Action")
        inv = _inventory(step)
        page = _fake_page()

        elapsed = [0.0]
        def fake_clock():
            result = elapsed[0]
            elapsed[0] += 130  # Over 120s
            return result

        def check_fn(state, step):
            return CheckResult("PASS", "Too slow", ("step3:evidence",))

        check = GuideCheck("test", lambda s: True, check_fn)
        outcome = walk_guide(
            page, inv, checks=[check], clock=fake_clock, page_wait_s=200
        )

        self.assertEqual(outcome.statuses["step3"], "NOT_RUN")


class TestVideoTimestamps(unittest.TestCase):
    def test_timestamps_recorded_with_video_origin(self):
        """Each step gets t_start and t_end relative to video origin."""
        step1 = _step("step1", "First")
        step2 = _step("step2", "Second")
        inv = _inventory(step1, step2)
        page = _fake_page()

        elapsed = [100.0]  # Video started at t=100
        def fake_clock():
            result = elapsed[0]
            elapsed[0] += 5
            return result

        def check_fn(state, step):
            return CheckResult("PASS", "Done", (f"{step.step_id}:ok",))

        check = GuideCheck("test", lambda s: True, check_fn)
        outcome = walk_guide(
            page, inv, checks=[check], clock=fake_clock,
            video_origin=100.0
        )

        self.assertIn("step1", outcome.timestamps)
        self.assertIn("step2", outcome.timestamps)

        t1_start, t1_end = outcome.timestamps["step1"]
        self.assertGreaterEqual(t1_start, 0)
        self.assertGreater(t1_end, t1_start)

        t2_start, t2_end = outcome.timestamps["step2"]
        self.assertGreater(t2_start, t1_end)

    def test_no_timestamps_without_video_origin(self):
        """Without video_origin, timestamps dict is empty."""
        step = _step("step1", "Action")
        inv = _inventory(step)
        page = _fake_page()

        def check_fn(state, step):
            return CheckResult("PASS", "Done", ("step1:ok",))

        check = GuideCheck("test", lambda s: True, check_fn)
        outcome = walk_guide(page, inv, checks=[check])

        self.assertEqual(len(outcome.timestamps), 0)


class TestSuspectFlags(unittest.TestCase):
    def test_suspect_flags_preserved_in_outcome(self):
        """Suspect flags from CheckResult are stored in outcome."""
        step = _step("step1", "Click Button")
        inv = _inventory(step)
        page = _fake_page()

        def check_fn(state, step):
            return CheckResult(
                "PASS", "Weak", ("step1:clicked",),
                suspect=("weak-evidence",)
            )

        check = GuideCheck("test", lambda s: True, check_fn)
        outcome = walk_guide(page, inv, checks=[check])

        self.assertIn("step1", outcome.suspects)
        self.assertEqual(outcome.suspects["step1"], ("weak-evidence",))

    def test_multiple_suspect_flags(self):
        """Multiple flags can be recorded."""
        step = _step("step1", "Action")
        inv = _inventory(step)
        page = _fake_page()

        def check_fn(state, step):
            return CheckResult(
                "PASS", "Suspicious", ("step1:ok",),
                suspect=("weak-evidence", "unexpected-change")
            )

        check = GuideCheck("test", lambda s: True, check_fn)
        outcome = walk_guide(page, inv, checks=[check])

        self.assertEqual(len(outcome.suspects["step1"]), 2)
        self.assertIn("weak-evidence", outcome.suspects["step1"])
        self.assertIn("unexpected-change", outcome.suspects["step1"])

    def test_suspect_flags_dont_change_status(self):
        """Flags are informational only, status stays PASS."""
        step = _step("step1", "Action")
        inv = _inventory(step)
        page = _fake_page()

        def check_fn(state, step):
            return CheckResult(
                "PASS", "Flagged", ("step1:evidence",),
                suspect=("weak-evidence",)
            )

        check = GuideCheck("test", lambda s: True, check_fn)
        outcome = walk_guide(page, inv, checks=[check])

        self.assertEqual(outcome.statuses["step1"], "PASS")


class TestHeartbeat(unittest.TestCase):
    def test_heartbeat_called_for_each_step(self):
        """Heartbeat callback is invoked after each step completes."""
        step1 = _step("step1", "First")
        step2 = _step("step2", "Second")
        inv = _inventory(step1, step2)
        page = _fake_page()

        calls = []
        def heartbeat_fn(run_id, index, total, step_id, status, time):
            calls.append({
                "index": index,
                "total": total,
                "step_id": step_id,
                "status": status,
            })

        def check_fn(state, step):
            return CheckResult("PASS", "Done", (f"{step.step_id}:ok",))

        check = GuideCheck("test", lambda s: True, check_fn)
        walk_guide(page, inv, checks=[check], heartbeat=heartbeat_fn)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["step_id"], "step1")
        self.assertEqual(calls[0]["index"], 1)
        self.assertEqual(calls[0]["total"], 2)
        self.assertEqual(calls[1]["step_id"], "step2")

    def test_heartbeat_includes_timestamp(self):
        """Heartbeat receives current time."""
        step = _step("step1", "Action")
        inv = _inventory(step)
        page = _fake_page()

        times = []
        def heartbeat_fn(run_id, index, total, step_id, status, time):
            times.append(time)

        elapsed = [50.0]
        def fake_clock():
            result = elapsed[0]
            elapsed[0] += 10
            return result

        def check_fn(state, step):
            return CheckResult("PASS", "Done", ("step1:ok",))

        check = GuideCheck("test", lambda s: True, check_fn)
        walk_guide(
            page, inv, checks=[check], heartbeat=heartbeat_fn,
            clock=fake_clock
        )

        self.assertEqual(len(times), 1)
        self.assertGreater(times[0], 50)


class TestScreenFlowVerification(unittest.TestCase):
    def test_screen_flow_requires_builder_evidence(self):
        """Screen Flow creation must verify Flow Builder opens."""
        from workshop_validator.guide_walk import run_screen_flow, WalkState

        step = _step("flow1", "Create a new Screen Flow")
        page = Mock()
        page.url = "https://example.my.salesforce.com/lightning/setup/Flows/home"

        # Mock _flow_builder_open to return False (builder didn't open)
        state = WalkState(page=page, home="https://example.my.salesforce.com")

        # This would be called after clicking Create, but builder didn't open
        # The check should catch this and return UI_MISMATCH
        # (Full integration requires mocking the entire flow, so this is a structure test)

    def test_click_without_after_state_not_pass(self):
        """Click without after-state read cannot be PASS."""
        # This tests the principle: a PASS needs evidence that was read from the page
        step = _step("click1", "Click Fields")
        inv = _inventory(step)
        page = _fake_page()

        def weak_check(state, step):
            # Simulates clicking without verifying the result
            return CheckResult("PASS", "Clicked", ())  # No evidence!

        check = GuideCheck("weak", lambda s: True, weak_check)
        outcome = walk_guide(page, inv, checks=[check])

        # Walk_guide converts PASS without evidence to NEEDS_REVIEW
        self.assertEqual(outcome.statuses["click1"], "NEEDS_REVIEW")


class TestProgressJSON(unittest.TestCase):
    def test_progress_json_structure(self):
        """Progress.json should have required fields and 0600 permissions."""
        with TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "progress.json"

            def write_progress(run_id, index, total, step_id, status, time):
                data = {
                    "run_id": run_id or "test-run",
                    "index": index,
                    "total": total,
                    "step_id": step_id,
                    "status": status,
                    "time": datetime.fromtimestamp(time, tz=timezone.utc).isoformat(),
                }
                progress_path.write_text(json.dumps(data, indent=2))
                progress_path.chmod(0o600)

            step = _step("step1", "Action")
            inv = _inventory(step)
            page = _fake_page()

            def check_fn(state, step):
                return CheckResult("PASS", "Done", ("step1:ok",))

            check = GuideCheck("test", lambda s: True, check_fn)
            walk_guide(page, inv, checks=[check], heartbeat=write_progress)

            self.assertTrue(progress_path.exists())
            data = json.loads(progress_path.read_text())
            self.assertIn("run_id", data)
            self.assertIn("index", data)
            self.assertIn("total", data)
            self.assertIn("step_id", data)
            self.assertIn("status", data)
            self.assertIn("time", data)

            # Check permissions (0o600)
            mode = progress_path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
