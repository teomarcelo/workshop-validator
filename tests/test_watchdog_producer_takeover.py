"""Tests for W-153: watchdog launches a fresh producer session on takeover.

Escape 2026-11-04: the desktop watchdog task typed into a stale session that refused.
The launchd watchdog (run_once) already launches fresh resume sessions, but only for open
jobs with stale heartbeats. With no open job, it returned idle and did nothing.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
import json
import subprocess
import sys
import tempfile
import unittest

from workshop_validator import team_kit, team_state, watchdog
from workshop_validator.lessons import TEAM, read_lessons

NOW = datetime(2026, 11, 7, 10, 0, 0, tzinfo=timezone.utc)
STALE = NOW - timedelta(hours=14)


def _age(path: Path, moment: datetime) -> None:
    """Set file mtime to a specific time."""
    import os
    os.utime(path, (moment.timestamp(), moment.timestamp()))


class WatchdogProducerTakeoverTests(unittest.TestCase):
    """Test that run_once launches a fresh producer when the chain is idle/stalled with no live producer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root, self.repo = base / "private", base / "repo"
        self.team = self.root / "team"
        self.transcripts = base / "transcripts"
        for d in (self.team, self.repo, self.transcripts):
            d.mkdir(parents=True)

        # Patch common dependencies
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
        """The takeover launch command is: claude -p "<wake prompt>" --permission-mode acceptEdits."""
        # Chain is IDLE (ready step, no open job)
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []

        def fake_producer_launcher(repo, log_path):
            produced.append((repo, log_path))

        result = watchdog.run_once(self.repo, self.root, producer_launcher=fake_producer_launcher, now=NOW)
        self.assertEqual(result, 0)
        self.assertEqual(len(produced), 1)
        repo_arg, log_arg = produced[0]
        self.assertEqual(repo_arg, self.repo)
        self.assertEqual(log_arg, self.root / "team" / "watchdog.log")

    def test_launch_producer_wake_command_details(self):
        """launch_producer_wake builds the correct claude command."""
        fake_claude = self.repo / "fake-claude"
        fake_claude.write_text("#!/bin/sh\necho test\n", encoding="utf-8")
        fake_claude.chmod(0o700)

        with mock.patch.dict("os.environ", {"WORKSHOP_VALIDATOR_CLAUDE": str(fake_claude)}), \
                mock.patch.object(subprocess, "Popen") as popen_mock:
            log_path = self.root / "team" / "watchdog.log"
            watchdog.launch_producer_wake(self.repo, log_path)

        cmd = popen_mock.call_args[0][0]
        self.assertEqual(cmd[0], str(fake_claude))
        self.assertEqual(cmd[1], "-p")
        self.assertEqual(cmd[2], watchdog.PRODUCER_WAKE_PROMPT)
        self.assertEqual(cmd[3], "--permission-mode")
        self.assertEqual(cmd[4], "acceptEdits")
        self.assertEqual(len(cmd), 5)

        kwargs = popen_mock.call_args[1]
        self.assertEqual(kwargs["cwd"], str(self.repo))
        self.assertTrue(kwargs["start_new_session"])

    def test_no_launch_when_fresh_producer_holds_seat(self):
        """No takeover launch when a fresh producer is already active."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)
        team_kit.claim_producer(self.team, session_id="active-producer", now=NOW - timedelta(minutes=1))
        transcript = self.transcripts / "active-producer.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        _age(transcript, NOW - timedelta(minutes=1))

        produced = []

        def fake_producer_launcher(repo, log_path):
            produced.append((repo, log_path))

        result = watchdog.run_once(self.repo, self.root, producer_launcher=fake_producer_launcher, now=NOW)
        self.assertEqual(result, 0)
        self.assertEqual(len(produced), 0)

    def test_no_launch_when_open_job_alive(self):
        """No takeover launch when an open job with recent heartbeat exists."""
        team_state.start_job(self.root, "guide.pdf", "quick", now=(NOW - timedelta(minutes=5)).isoformat())
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "running"}]}, NOW)

        produced = []

        def fake_producer_launcher(repo, log_path):
            produced.append((repo, log_path))

        result = watchdog.run_once(self.repo, self.root, producer_launcher=fake_producer_launcher, now=NOW)
        self.assertEqual(result, 0)
        self.assertEqual(len(produced), 0)

    def test_no_second_launch_inside_cooldown(self):
        """No second takeover launch within TAKEOVER_COOLDOWN_MINUTES (60 minutes)."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []

        def fake_producer_launcher(repo, log_path):
            produced.append((repo, log_path))

        # First takeover
        result1 = watchdog.run_once(self.repo, self.root, producer_launcher=fake_producer_launcher, now=NOW)
        self.assertEqual(result1, 0)
        self.assertEqual(len(produced), 1)

        # Second attempt 30 minutes later (within cooldown)
        result2 = watchdog.run_once(self.repo, self.root, producer_launcher=fake_producer_launcher,
                                    now=NOW + timedelta(minutes=30))
        self.assertEqual(result2, 0)
        self.assertEqual(len(produced), 1)  # No new launch

        # Third attempt 61 minutes after first (outside cooldown)
        result3 = watchdog.run_once(self.repo, self.root, producer_launcher=fake_producer_launcher,
                                    now=NOW + timedelta(minutes=61))
        self.assertEqual(result3, 0)
        self.assertEqual(len(produced), 2)  # New launch

    def test_no_launch_past_max_takeovers(self):
        """After MAX_TAKEOVERS_PER_IDLE (3), prints WAITING owner and does not launch."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []

        def fake_producer_launcher(repo, log_path):
            produced.append((repo, log_path))

        # Three takeovers (allowed)
        for i in range(team_kit.MAX_TAKEOVERS_PER_IDLE):
            result = watchdog.run_once(
                self.repo, self.root, producer_launcher=fake_producer_launcher,
                now=NOW + timedelta(minutes=61 * i)
            )
            self.assertEqual(result, 0)
        self.assertEqual(len(produced), team_kit.MAX_TAKEOVERS_PER_IDLE)

        # Fourth attempt should print WAITING owner and not launch
        with mock.patch.object(sys, "stderr") as stderr_mock:
            result = watchdog.run_once(
                self.repo, self.root, producer_launcher=fake_producer_launcher,
                now=NOW + timedelta(minutes=61 * team_kit.MAX_TAKEOVERS_PER_IDLE)
            )
        self.assertEqual(result, 0)
        self.assertEqual(len(produced), team_kit.MAX_TAKEOVERS_PER_IDLE)  # No new launch

        stderr_calls = [str(c) for c in stderr_mock.write.call_args_list]
        self.assertTrue(any("WAITING owner" in str(c) and "3 takeovers" in str(c) for c in stderr_calls))

    def test_launch_failure_prints_waiting_owner_and_records_lesson(self):
        """If claude_path() is None or launch raises OSError, print WAITING owner and record a team lesson."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        def failing_launcher(repo, log_path):
            raise OSError("claude CLI not found")

        with mock.patch.object(sys, "stderr") as stderr_mock:
            result = watchdog.run_once(
                self.repo, self.root, producer_launcher=failing_launcher, now=NOW
            )

        self.assertEqual(result, 1)
        stderr_calls = [str(c) for c in stderr_mock.write.call_args_list]
        self.assertTrue(any("WAITING owner" in str(c) and "takeover launch failed" in str(c) for c in stderr_calls))

        lessons = read_lessons(self.root, kind=TEAM)
        self.assertTrue(any("could not start" in lesson["detail"] for lesson in lessons))

    def test_takeover_records_team_state_log(self):
        """After a takeover launch, team_state log contains action=takeover-launch."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        def fake_producer_launcher(repo, log_path):
            pass

        watchdog.run_once(self.repo, self.root, producer_launcher=fake_producer_launcher, now=NOW)

        log_path = self.root / "team" / "team-log.json"
        self.assertTrue(log_path.is_file())
        events = json.loads(log_path.read_text(encoding="utf-8"))
        takeover_events = [e for e in events if e.get("action") == "takeover-launch"]
        self.assertEqual(len(takeover_events), 1)
        self.assertEqual(takeover_events[0]["agent"], "watchdog")


class GuardRemovalTests(unittest.TestCase):
    """Each guard must have a test that fails when only that guard is removed."""

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

    def test_guard_fresh_producer_removed(self):
        """With fresh producer guard removed, a takeover would launch despite live producer."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)
        team_kit.claim_producer(self.team, session_id="active", now=NOW - timedelta(minutes=1))
        transcript = self.transcripts / "active.jsonl"
        transcript.write_text("{}\n", encoding="utf-8")
        _age(transcript, NOW - timedelta(minutes=1))

        produced = []

        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # With guard in place, no launch
        watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher, now=NOW)
        self.assertEqual(len(produced), 0)

    def test_guard_open_job_alive_removed(self):
        """With open job guard removed, a takeover would launch despite alive job."""
        team_state.start_job(self.root, "guide.pdf", "quick", now=(NOW - timedelta(minutes=5)).isoformat())
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "running"}]}, NOW)

        produced = []

        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # With guard in place, no launch (job is alive)
        watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher, now=NOW)
        self.assertEqual(len(produced), 0)

    def test_guard_cooldown_removed(self):
        """With cooldown guard removed, a second takeover would launch too soon."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []

        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # First takeover
        watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher, now=NOW)
        self.assertEqual(len(produced), 1)

        # Second attempt within cooldown (with guard, should not launch)
        watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher,
                         now=NOW + timedelta(minutes=30))
        self.assertEqual(len(produced), 1)

    def test_guard_max_takeovers_removed(self):
        """With MAX_TAKEOVERS guard removed, a fourth takeover would launch."""
        team_kit.save_plan(self.team, {"steps": [{"id": "S1", "state": "next"}]}, NOW)

        produced = []

        def fake_launcher(repo, log_path):
            produced.append((repo, log_path))

        # Three takeovers
        for i in range(team_kit.MAX_TAKEOVERS_PER_IDLE):
            watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher,
                             now=NOW + timedelta(minutes=61 * i))

        self.assertEqual(len(produced), team_kit.MAX_TAKEOVERS_PER_IDLE)

        # Fourth attempt (with guard, should print WAITING owner and not launch)
        with mock.patch.object(sys, "stderr"):
            watchdog.run_once(self.repo, self.root, producer_launcher=fake_launcher,
                             now=NOW + timedelta(minutes=61 * team_kit.MAX_TAKEOVERS_PER_IDLE))
        self.assertEqual(len(produced), team_kit.MAX_TAKEOVERS_PER_IDLE)


if __name__ == "__main__":
    unittest.main()
