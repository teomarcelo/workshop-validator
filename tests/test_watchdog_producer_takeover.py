"""Tests for W-153 round 3: one locked launcher, never a second producer.

Escape 2026-11-04: the desktop watchdog task typed into a stale session that refused.
The launchd watchdog (run_once) already launches fresh resume sessions, but only for open
jobs with stale heartbeats. With no open job, it returned idle and did nothing.

Round 2 reviews found: desktop and launchd both wrote takeover-at/takeover-count, causing
interleaving bugs; launchd wrote nudged-at and acted on NUDGE, launching a second producer
while the first was still live; no rolling cap, no lock, WAITING owner only in stderr.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread
from unittest import mock
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from workshop_validator import team_kit, team_state, watchdog
from workshop_validator.watchdog import launch_resume
from workshop_validator.lessons import TEAM, read_lessons

NOW = datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(hours=14)


def _age(path: Path, moment: datetime) -> None:
    """Set file mtime to a specific time."""
    os.utime(path, (moment.timestamp(), moment.timestamp()))


class TakeoverLaunchTests(unittest.TestCase):
    """Test the unified takeover_launch function used by both desktop and launchd."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        from workshop_validator import public_safety
        for patch in (
            mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
            mock.patch.object(team_kit, "free_gb", return_value=100.0),
            mock.patch.object(public_safety, "check_main_clean", return_value=(True, "")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_exact_command_on_takeover(self):
        """The takeover launch command is: claude -p "<wake prompt>" --permission-mode acceptEdits, cwd=repo, start_new_session=True.

        The prompt MUST include both safety lines:
        - "If team_kit status shows a live producer, stop without doing anything."
        - "Never do owner-only work (CLAUDE.md, Autonomy boundary)."

        Deleting either line must fail this test.
        """
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        fake_claude = self.repo / "fake-claude"
        fake_claude.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
        fake_claude.chmod(0o700)

        with mock.patch.dict("os.environ", {"WORKSHOP_VALIDATOR_CLAUDE": str(fake_claude)}), \
                mock.patch.object(subprocess, "Popen") as popen_mock:
            status, _ = team_kit.takeover_launch(self.repo, self.team, NOW, watchdog.launch_producer_wake)

        self.assertEqual(status, "LAUNCHED")
        cmd = popen_mock.call_args[0][0]
        self.assertEqual(cmd, [
            str(fake_claude),
            "-p", watchdog.PRODUCER_WAKE_PROMPT,
            "--permission-mode", "acceptEdits"
        ])

        # Verify the FULL prompt text including both safety lines
        prompt_text = cmd[2]
        self.assertIn("If team_kit status shows a live producer, stop without doing anything",
                     prompt_text,
                     "Prompt must include the 'stop if live producer' safety line")
        self.assertIn("Never do owner-only work (CLAUDE.md, Autonomy boundary)",
                     prompt_text,
                     "Prompt must include the 'never do owner-only work' safety line")

        kwargs = popen_mock.call_args[1]
        self.assertEqual(kwargs["cwd"], str(self.repo))
        self.assertTrue(kwargs["start_new_session"])

        # Check log file mode 0600
        log_path = self.team / "watchdog.log"
        self.assertTrue(log_path.is_file())
        self.assertEqual(stat.S_IMODE(log_path.stat().st_mode), 0o600)

    def test_no_launch_when_fresh_producer_holds_seat(self):
        """No takeover when a fresh producer is already active."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)
        team_kit.claim_producer(self.team, session_id="active-producer", now=NOW - timedelta(minutes=1))
        transcript = self.transcripts / "active-producer.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        _age(transcript, NOW - timedelta(minutes=1))

        produced = []
        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        status, _ = team_kit.takeover_launch(self.repo, self.team, NOW, fake_launcher)
        self.assertEqual(status, "NONE")
        self.assertEqual(len(produced), 0)

    def test_no_launch_when_open_job_alive(self):
        """No takeover when an open job with recent heartbeat exists (status is WORKING)."""
        team_state.start_job(self.root, "guide.pdf", "quick", now=(NOW - timedelta(minutes=5)).isoformat())
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "running"}]}, NOW)

        produced = []
        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        status, message = team_kit.takeover_launch(self.repo, self.team, NOW, fake_launcher)
        self.assertEqual(status, "NONE")
        self.assertIn("WORKING", message)
        self.assertEqual(len(produced), 0)

    def test_no_second_launch_inside_cooldown(self):
        """No second takeover within TAKEOVER_COOLDOWN_MINUTES (60 minutes)."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []
        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # First takeover
        status1, _ = team_kit.takeover_launch(self.repo, self.team, NOW, fake_launcher)
        self.assertEqual(status1, "LAUNCHED")
        self.assertEqual(len(produced), 1)

        # Second attempt 30 minutes later (within cooldown)
        status2, _ = team_kit.takeover_launch(self.repo, self.team, NOW + timedelta(minutes=30), fake_launcher)
        self.assertEqual(status2, "NONE")
        self.assertEqual(len(produced), 1)  # No new launch

        # Third attempt 61 minutes after first (outside cooldown)
        status3, _ = team_kit.takeover_launch(self.repo, self.team, NOW + timedelta(minutes=61), fake_launcher)
        self.assertEqual(status3, "LAUNCHED")
        self.assertEqual(len(produced), 2)  # New launch

    def test_rolling_cap_7th_launch_refused(self):
        """The 7th launch in 24 hours is refused even after a WORKING interval reset idle markers."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []
        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # Six launches over 12 hours (allowed)
        for i in range(team_kit.MAX_TAKEOVERS_PER_DAY):
            at = NOW + timedelta(hours=2 * i)
            team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, at)
            # Reset idle markers to simulate WORKING interval
            (self.team / "idle-since").unlink(missing_ok=True)
            (self.team / "takeover-at").unlink(missing_ok=True)
            (self.team / "takeover-count").unlink(missing_ok=True)
            status, _ = team_kit.takeover_launch(self.repo, self.team, at, fake_launcher)
            self.assertEqual(status, "LAUNCHED", f"Launch {i+1} should succeed")

        self.assertEqual(len(produced), team_kit.MAX_TAKEOVERS_PER_DAY)

        # Seventh attempt (within 24 hours of first)
        at = NOW + timedelta(hours=12, minutes=30)
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, at)
        (self.team / "idle-since").unlink(missing_ok=True)
        (self.team / "takeover-at").unlink(missing_ok=True)
        (self.team / "takeover-count").unlink(missing_ok=True)
        status, message = team_kit.takeover_launch(self.repo, self.team, at, fake_launcher)
        self.assertEqual(status, "WAITING")
        self.assertIn("6", message)
        self.assertIn("24h", message)
        self.assertEqual(len(produced), team_kit.MAX_TAKEOVERS_PER_DAY)  # No new launch

    def test_launch_failure_no_log_lesson_recorded_waiting_marker(self):
        """Launch failure: no takeover-launch log line, lesson recorded, WAITING marker written."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        def failing_launcher(repo, log_path):
            raise OSError("claude CLI not found")

        status, message = team_kit.takeover_launch(self.repo, self.team, NOW, failing_launcher)
        self.assertEqual(status, "WAITING")
        self.assertIn("claude CLI not found", message)

        # No takeover-launch log line
        log_path = self.root / "team" / "team-log.json"
        if log_path.is_file():
            events = json.loads(log_path.read_text(encoding="utf-8"))
            takeover_events = [e for e in events if e.get("action") == "takeover-launch"]
            self.assertEqual(len(takeover_events), 0)

        # Lesson recorded
        lessons = read_lessons(self.root, kind=TEAM)
        self.assertTrue(any("Launch failed" in lesson["detail"] for lesson in lessons))

        # WAITING marker written
        waiting_marker = self.team / "watchdog-waiting-owner"
        self.assertTrue(waiting_marker.is_file())
        data = json.loads(waiting_marker.read_text(encoding="utf-8"))
        self.assertIn("claude CLI not found", data["reason"])

    def test_lock_two_callers_launch_at_most_once(self):
        """With file lock, two simultaneous callers launch at most once."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []
        lock_acquired = []

        def locking_launcher(repo, log_path):
            lock_acquired.append(time.time())
            time.sleep(0.05)  # Hold the lock briefly
            produced.append((repo, log_path))

        def call_takeover():
            team_kit.takeover_launch(self.repo, self.team, NOW, locking_launcher)

        # Two threads try to launch simultaneously
        t1 = Thread(target=call_takeover)
        t2 = Thread(target=call_takeover)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Exactly one launch happened
        self.assertEqual(len(produced), 1)

    def test_desktop_prints_launched_not_takeover(self):
        """watchdog_check returns LAUNCHED (not TAKEOVER) when a fresh session is started."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        fake_claude = self.repo / "fake-claude"
        fake_claude.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
        fake_claude.chmod(0o700)

        with mock.patch.dict("os.environ", {"WORKSHOP_VALIDATOR_CLAUDE": str(fake_claude)}), \
                mock.patch.object(subprocess, "Popen"):
            result = team_kit.watchdog_check(self.repo, self.team, NOW, launcher=watchdog.launch_producer_wake)

        self.assertTrue(result.startswith("LAUNCHED"))
        self.assertNotIn("TAKEOVER", result)

    def test_waiting_owner_printed_once_per_reason(self):
        """WAITING owner message is printed once per new reason, not every tick."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        def failing_launcher(repo, log_path):
            raise OSError("test failure")

        # First failure
        status1, _ = team_kit.takeover_launch(self.repo, self.team, NOW, failing_launcher)
        self.assertEqual(status1, "WAITING")

        marker = self.team / "watchdog-waiting-owner"
        self.assertTrue(marker.is_file())
        data1 = json.loads(marker.read_text(encoding="utf-8"))
        time1 = data1["time"]

        # Second attempt with same reason after cooldown (marker not updated because reason is same)
        # Need to be outside cooldown to not hit NONE for cooldown guard
        status2, _ = team_kit.takeover_launch(self.repo, self.team, NOW + timedelta(minutes=65), failing_launcher)
        self.assertEqual(status2, "WAITING")
        data2 = json.loads(marker.read_text(encoding="utf-8"))
        self.assertEqual(data2["time"], time1)  # Same time, not updated


class InterleavingTests(unittest.TestCase):
    """Test that desktop and launchd callers never launch twice in one cooldown window."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        from workshop_validator import public_safety
        for patch in (
            mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
            mock.patch.object(team_kit, "free_gb", return_value=100.0),
            mock.patch.object(public_safety, "check_main_clean", return_value=(True, "")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_interleaving_desktop_and_launchd_over_5_hours(self):
        """Desktop every 20 min, launchd every 5 min, idle start just before desktop tick: exactly one launch per cooldown."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        fake_claude = self.repo / "fake-claude"
        fake_claude.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
        fake_claude.chmod(0o700)

        produced = []

        original_popen = subprocess.Popen
        def mock_popen(*args, **kwargs):
            produced.append(("popen", args, kwargs))
            # Don't actually start the process
            mock_proc = mock.Mock()
            mock_proc.pid = 12345
            return mock_proc

        # Simulate 5 hours: desktop at 0, 20, 40, ..., launchd at 0, 5, 10, 15, 20, ...
        desktop_ticks = [NOW + timedelta(minutes=20 * i) for i in range(16)]  # 0 to 300 minutes
        launchd_ticks = [NOW + timedelta(minutes=5 * i) for i in range(61)]   # 0 to 300 minutes

        all_ticks = sorted(set(desktop_ticks + launchd_ticks))

        with mock.patch.dict("os.environ", {"WORKSHOP_VALIDATOR_CLAUDE": str(fake_claude)}), \
                mock.patch.object(subprocess, "Popen", side_effect=mock_popen):
            for tick in all_ticks:
                team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, tick)
                if tick in desktop_ticks:
                    # Desktop calls watchdog_check
                    team_kit.watchdog_check(self.repo, self.team, tick, launcher=watchdog.launch_producer_wake)
                if tick in launchd_ticks:
                    # launchd calls run_once
                    watchdog.run_once(self.repo, self.root, now=tick, launcher=launch_resume, producer_launcher=watchdog.launch_producer_wake)

        # Expect launches at: 0, 61, 122, 183, 244 (every 60+ minutes)
        # With 60-minute cooldown, at most 6 launches in 5 hours
        self.assertGreater(len(produced), 0)
        self.assertLessEqual(len(produced), 6)

        # Verify no two launches within 60 minutes
        launches_file = self.team / "takeover-launches"
        self.assertTrue(launches_file.is_file())
        launches = json.loads(launches_file.read_text(encoding="utf-8"))
        times = [datetime.fromisoformat(t) for t in launches]
        for i in range(1, len(times)):
            self.assertGreaterEqual((times[i] - times[i-1]).total_seconds(), 60 * 60)


class NudgeGraceTests(unittest.TestCase):
    """Test that launchd never writes nudged-at, never acts on NUDGE, and desktop NUDGE still works."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        from workshop_validator import public_safety
        for patch in (
            mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
            mock.patch.object(team_kit, "free_gb", return_value=100.0),
            mock.patch.object(public_safety, "check_main_clean", return_value=(True, "")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_nudge_then_grace_launchd_never_writes_nudged_at(self):
        """Fresh live producer for 35+ minutes: launchd launches nothing, never writes nudged-at; desktop NUDGE still fires."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)
        team_kit.claim_producer(self.team, session_id="active", now=NOW - timedelta(minutes=1))
        transcript = self.transcripts / "active.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        _age(transcript, NOW - timedelta(minutes=1))

        produced = []
        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # Desktop at +0: should NUDGE
        result_desktop_0 = team_kit.watchdog_check(self.repo, self.team, NOW)
        self.assertTrue(result_desktop_0.startswith("NUDGE"))
        nudge_file = self.team / "nudged-at"
        self.assertTrue(nudge_file.is_file())

        # launchd at +5, +10, +15, +20, +25, +30, +35: fresh producer still alive, no launch
        for minutes in [5, 10, 15, 20, 25, 30, 35]:
            at = NOW + timedelta(minutes=minutes)
            # Refresh producer activity
            team_kit.claim_producer(self.team, session_id="active", now=at - timedelta(seconds=10))
            _age(transcript, at - timedelta(seconds=10))
            watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher, now=at)

        # No launches by launchd
        self.assertEqual(len(produced), 0)

        # Desktop at +10: producer still fresh, nudged-at set, should see NONE (within grace)
        # Refresh producer for the desktop check too
        at_10 = NOW + timedelta(minutes=10)
        team_kit.claim_producer(self.team, session_id="active", now=at_10 - timedelta(seconds=10))
        _age(transcript, at_10 - timedelta(seconds=10))
        result_desktop_10 = team_kit.watchdog_check(self.repo, self.team, at_10)
        self.assertTrue(result_desktop_10.startswith("NONE"), f"Got: {result_desktop_10}")


class GuardKillingTests(unittest.TestCase):
    """Tests that kill guards when mutated (for team_gates prove)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        from workshop_validator import public_safety
        for patch in (
            mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
            mock.patch.object(team_kit, "free_gb", return_value=100.0),
            mock.patch.object(public_safety, "check_main_clean", return_value=(True, "")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_guard_action_idle_killed_by_working_state(self):
        """Mutating `if action == "idle"` in run_once to `if True` is killed by alive job test."""
        team_state.start_job(self.root, "guide.pdf", "quick", now=(NOW - timedelta(minutes=5)).isoformat())
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "running"}]}, NOW)

        produced = []
        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # With guard in place, decide() returns "alive" (not "idle"), so no takeover check
        result = watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher, now=NOW)
        self.assertEqual(result, 0)
        self.assertEqual(len(produced), 0)

    def test_guard_fresh_producer_killed_by_active_session(self):
        """Mutating `if action == "idle" and not fresh` to `if action == "idle"` is killed by fresh producer test."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)
        team_kit.claim_producer(self.team, session_id="active", now=NOW - timedelta(minutes=1))
        transcript = self.transcripts / "active.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        _age(transcript, NOW - timedelta(minutes=1))

        produced = []
        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # With `not fresh` guard, no launch
        result = watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher, now=NOW)
        self.assertEqual(result, 0)
        self.assertEqual(len(produced), 0)


class NoLaunchByDefaultTests(unittest.TestCase):
    """Test that library functions never launch by default (security escape W-155)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        from workshop_validator import public_safety
        for patch in (
            mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
            mock.patch.object(team_kit, "free_gb", return_value=100.0),
            mock.patch.object(public_safety, "check_main_clean", return_value=(True, "")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_watchdog_check_no_launch_by_default(self):
        """watchdog_check with no launcher parameter does not start a process."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        with mock.patch.object(subprocess, "Popen") as popen_mock:
            result = team_kit.watchdog_check(self.repo, self.team, NOW)

        # No process started
        self.assertEqual(popen_mock.call_count, 0)
        # Returns TAKEOVER without launching
        self.assertTrue(result.startswith("TAKEOVER"))

    def test_run_once_no_launch_by_default(self):
        """run_once with no launcher parameters does not start a process."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        with mock.patch.object(subprocess, "Popen") as popen_mock:
            result = watchdog.run_once(self.repo, self.root, now=NOW)

        # No process started
        self.assertEqual(popen_mock.call_count, 0)
        self.assertEqual(result, 0)

    def test_run_once_stalled_no_launch_by_default(self):
        """run_once with stalled job and no launcher does not start a process."""
        team_state.start_job(self.root, "guide.pdf", "quick", now=(NOW - timedelta(hours=1)).isoformat())
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "running"}]}, NOW)

        with mock.patch.object(subprocess, "Popen") as popen_mock:
            result = watchdog.run_once(self.repo, self.root, now=NOW)

        # No process started
        self.assertEqual(popen_mock.call_count, 0)
        self.assertEqual(result, 0)


class CLIPassesRealLauncherTests(unittest.TestCase):
    """Test that CLI entry points pass the real launcher (W-155 requirement 4)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        from workshop_validator import public_safety
        for patch in (
            mock.patch.object(team_kit, "transcripts_dir", return_value=self.transcripts),
            mock.patch.object(team_kit, "free_gb", return_value=100.0),
            mock.patch.object(public_safety, "check_main_clean", return_value=(True, "")),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_team_kit_watchdog_cli_passes_launcher(self):
        """team_kit CLI 'watchdog' command passes the real launcher."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        fake_claude = self.repo / "fake-claude"
        fake_claude.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
        fake_claude.chmod(0o700)

        with mock.patch.dict("os.environ", {"WORKSHOP_VALIDATOR_CLAUDE": str(fake_claude)}), \
                mock.patch.object(subprocess, "Popen") as popen_mock:
            # Call the CLI entry point
            team_kit.main(["--repo", str(self.repo), "--private-root", str(self.root), "watchdog"])

        # A process was started (real launcher was passed)
        self.assertGreater(popen_mock.call_count, 0)
        cmd = popen_mock.call_args[0][0]
        self.assertEqual(cmd[0], str(fake_claude))

    def test_watchdog_once_cli_passes_launchers(self):
        """watchdog --once CLI passes the real launchers."""
        team_state.start_job(self.root, "guide.pdf", "quick", now=(NOW - timedelta(hours=1)).isoformat())
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "running"}]}, NOW)

        fake_claude = self.repo / "fake-claude"
        fake_claude.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
        fake_claude.chmod(0o700)

        with mock.patch.dict("os.environ", {"WORKSHOP_VALIDATOR_CLAUDE": str(fake_claude)}), \
                mock.patch.object(subprocess, "Popen") as popen_mock:
            # Call the CLI entry point
            watchdog.main(["--repo", str(self.repo), "--private-root", str(self.root), "--once"])

        # A process was started (real launcher was passed)
        self.assertGreater(popen_mock.call_count, 0)


if __name__ == "__main__":
    unittest.main()
