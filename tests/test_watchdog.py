"""Tests for watchdog module."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
import unittest

from workshop_validator.team_state import start_job, advance, load, _write_private
from workshop_validator.watchdog import (
    RESTART_CAP_PER_HOUR,
    decide,
    install_plist,
    record_restart,
    record_stalled,
    run_once,
)


class TestWatchdog(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.now = datetime(2026, 10, 22, 10, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_decide_no_job(self):
        self.assertEqual(decide(None, now=self.now), "idle")

    def test_decide_job_done(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        from workshop_validator.team_state import finish
        finish(self.root, "success", now=self.now.isoformat())
        state = load(self.root)
        self.assertEqual(decide(state, now=self.now), "idle")

    def test_decide_recent_heartbeat(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        state = load(self.root)
        later = self.now + timedelta(minutes=10)
        self.assertEqual(decide(state, now=later), "alive")

    def test_decide_stale_no_lock(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        state = load(self.root)
        later = self.now + timedelta(minutes=20)
        self.assertEqual(decide(state, now=later), "restart")

    def test_decide_stale_dead_lock(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        from workshop_validator.team_state import acquire_lock
        acquire_lock(self.root, 99999, alive=lambda p: False, now=self.now.isoformat())
        state = load(self.root)
        later = self.now + timedelta(minutes=20)
        self.assertEqual(decide(state, now=later), "restart")

    def test_decide_stale_live_lock(self):
        import os
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        from workshop_validator.team_state import acquire_lock
        live_pid = os.getpid()
        acquire_lock(self.root, live_pid, alive=lambda p: True, now=self.now.isoformat())
        state = load(self.root)
        state["heartbeat"] = (self.now - timedelta(minutes=20)).isoformat()
        _write_private(self.root / "team" / "state.json", json.dumps(state, indent=2) + "\n")
        state = load(self.root)
        later = self.now
        self.assertEqual(decide(state, now=later), "alive")

    def test_decide_too_many_restarts(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        for i in range(RESTART_CAP_PER_HOUR):
            record_restart(self.root, now=self.now + timedelta(minutes=i))
        state = load(self.root)
        later = self.now + timedelta(minutes=20)
        self.assertEqual(decide(state, now=later), "stalled")

    def test_decide_old_restarts_ignored(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        old = self.now - timedelta(hours=2)
        for i in range(RESTART_CAP_PER_HOUR):
            record_restart(self.root, now=old + timedelta(minutes=i))
        state = load(self.root)
        later = self.now + timedelta(minutes=20)
        self.assertEqual(decide(state, now=later), "restart")

    def test_record_restart_appends(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        record_restart(self.root, now=self.now)
        state = load(self.root)
        self.assertEqual(len(state["restarts"]), 1)
        record_restart(self.root, now=self.now + timedelta(minutes=1))
        state = load(self.root)
        self.assertEqual(len(state["restarts"]), 2)

    def test_record_stalled_writes_once(self):
        record_stalled(self.root, "Too many restarts", now=self.now)
        path = self.root / "team" / "stalled.json"
        self.assertTrue(path.is_file())
        data = json.loads(path.read_text())
        self.assertEqual(data["reason"], "Too many restarts")
        record_stalled(self.root, "Another reason", now=self.now)
        data = json.loads(path.read_text())
        self.assertEqual(data["reason"], "Too many restarts")

    def test_run_once_idle(self):
        launched = []
        result = run_once(
            self.root, self.root,
            launcher=lambda r, l, v: launched.append((r, l, v)),
            now=self.now,
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(launched), 0)

    def test_run_once_alive(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        launched = []
        result = run_once(
            self.root, self.root,
            launcher=lambda r, l, v: launched.append((r, l, v)),
            now=self.now + timedelta(minutes=5),
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(launched), 0)

    def test_run_once_restart(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        launched = []
        result = run_once(
            self.root, self.root,
            launcher=lambda r, l, v: launched.append((r, l, v)),
            now=self.now + timedelta(minutes=20),
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(launched), 1)
        repo_root, log_path, venv = launched[0]
        self.assertEqual(repo_root, self.root)
        self.assertEqual(log_path, self.root / "team" / "watchdog.log")

    def test_run_once_stalled(self):
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        for i in range(RESTART_CAP_PER_HOUR):
            record_restart(self.root, now=self.now + timedelta(minutes=i))
        launched = []
        result = run_once(
            self.root, self.root,
            launcher=lambda r, l, v: launched.append((r, l, v)),
            now=self.now + timedelta(minutes=20),
        )
        self.assertEqual(result, 0)
        self.assertEqual(len(launched), 0)
        stalled_path = self.root / "team" / "stalled.json"
        self.assertTrue(stalled_path.is_file())

    def test_restart_and_stall_become_team_lessons(self):
        from workshop_validator.lessons import TEAM, read_lessons
        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        run_once(self.root, self.root, launcher=lambda r, l, v: None, now=self.now + timedelta(minutes=20))
        team = read_lessons(self.root, kind=TEAM)
        self.assertEqual(len(team), 1)
        self.assertIn("restarted", team[0]["detail"])
        self.assertTrue(team[0]["never"])

    def test_failed_launch_is_reported_and_recorded(self):
        from workshop_validator.lessons import TEAM, read_lessons

        def missing(r, l, v):
            raise FileNotFoundError("claude CLI not found")

        start_job(self.root, "guide.pdf", "quick", now=self.now.isoformat())
        result = run_once(self.root, self.root, launcher=missing, now=self.now + timedelta(minutes=20))
        self.assertEqual(result, 1)
        details = [item["detail"] for item in read_lessons(self.root, kind=TEAM)]
        self.assertTrue(any("could not launch" in d for d in details))

    def test_launch_uses_the_claude_cli_not_a_python_module(self):
        import os
        from unittest import mock
        from workshop_validator import watchdog
        fake = self.root / "claude"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        fake.chmod(0o700)
        with mock.patch.dict(os.environ, {"WORKSHOP_VALIDATOR_CLAUDE": str(fake)}), \
                mock.patch.object(watchdog.subprocess, "Popen") as popen:
            watchdog.launch_resume(self.root, self.root / "team" / "watchdog.log", Path("/venv/python"))
        cmd = popen.call_args[0][0]
        self.assertEqual(cmd[0], str(fake))
        self.assertNotIn("-m", cmd)
        self.assertIn("/validate-guide --resume", cmd)

    def test_install_plist_substitution(self):
        template = self.root / "template.plist"
        template.write_text("{{PYTHON}} {{REPO}}", encoding="utf-8")
        home = self.root / "home"
        venv = Path("/path/to/venv/bin/python")
        repo = Path("/repo")
        cmd = install_plist(template, home, venv, repo, Path(home) / "private")
        plist = home / "Library" / "LaunchAgents" / "com.workshop-validator.watchdog.plist"
        self.assertTrue(plist.is_file())
        content = plist.read_text()
        self.assertIn("/path/to/venv/bin/python", content)
        self.assertIn("/repo", content)
        self.assertIn("launchctl load", cmd)
        self.assertIn(str(plist), cmd)


if __name__ == "__main__":
    unittest.main()


class PlistGenerationTests(unittest.TestCase):
    """Tests for launchd plist generation."""
    
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
    
    def test_install_plist_includes_private_root_placeholder(self):
        from workshop_validator.watchdog import install_plist
        
        template_dir = self.tmp_path / "scripts"
        template_dir.mkdir()
        template = template_dir / "com.workshop-validator.watchdog.plist"
        
        plist_content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>ProgramArguments</key>
    <array>
        <string>{{PYTHON}}</string>
        <string>-m</string>
        <string>workshop_validator.watchdog</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{{REPO}}</string>
    <key>StandardOutPath</key>
    <string>{{PRIVATE_ROOT}}/team/watchdog-launchd.log</string>
    <key>StandardErrorPath</key>
    <string>{{PRIVATE_ROOT}}/team/watchdog-launchd.log</string>
</dict>
</plist>"""
        
        template.write_text(plist_content, encoding="utf-8")
        
        home = self.tmp_path / "home"
        home.mkdir()
        venv_python = self.tmp_path / "venv" / "bin" / "python"
        venv_python.parent.mkdir(parents=True)
        venv_python.touch()
        repo_root = self.tmp_path / "repo"
        repo_root.mkdir()
        private_root = self.tmp_path / "private"
        private_root.mkdir()
        
        cmd = install_plist(template, home, venv_python, repo_root, private_root)
        
        plist_path = home / "Library" / "LaunchAgents" / "com.workshop-validator.watchdog.plist"
        self.assertTrue(plist_path.exists())
        
        content = plist_path.read_text(encoding="utf-8")
        
        # Verify placeholders were replaced
        self.assertIn(str(venv_python), content)
        self.assertIn(str(repo_root), content)
        self.assertIn(str(private_root), content)
        
        # Verify old placeholder not present
        self.assertNotIn("{{PYTHON}}", content)
        self.assertNotIn("{{REPO}}", content)
        self.assertNotIn("{{PRIVATE_ROOT}}", content)
        
        # Verify log path includes private_root
        self.assertIn(f"{private_root}/team/watchdog-launchd.log", content)
        
        # Verify command is correct
        self.assertIn("launchctl load", cmd)
        self.assertIn(str(plist_path), cmd)
