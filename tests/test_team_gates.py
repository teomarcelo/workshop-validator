"""Tests for team_gates.py"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop_validator import team_gates


class FakeRunner:
    """Fake runner that captures commands and env."""

    def __init__(self, return_codes=None):
        self.calls = []
        self.return_codes = return_codes or {}

    def __call__(self, cmd, cwd, env=None):
        self.calls.append({
            "cmd": cmd,
            "cwd": cwd,
            "env": env
        })
        returncode = self.return_codes.get(tuple(cmd), 0)
        return subprocess.CompletedProcess(cmd, returncode, "", "")


class TestMergedTreeGatesEnv(unittest.TestCase):
    """Test that merged_tree_gates passes env= instead of changing os.environ."""

    def test_env_passed_to_runner_not_os_environ(self):
        """The unittest command receives WORKSHOP_VALIDATOR_CLAUDE in env, not os.environ."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            venv = repo / ".venv" / "bin"
            venv.mkdir(parents=True)
            python = venv / "python"
            python.touch()

            # Mock git commands
            fake_runner = FakeRunner()

            # Store original env state
            original_env = os.environ.copy()
            original_claude_value = os.environ.get("WORKSHOP_VALIDATOR_CLAUDE")

            try:
                # Call merged_tree_gates
                result = team_gates.merged_tree_gates(repo, "abc123" * 5, runner=fake_runner)

                # Verify os.environ is unchanged
                self.assertEqual(os.environ.get("WORKSHOP_VALIDATOR_CLAUDE"), original_claude_value,
                                "os.environ['WORKSHOP_VALIDATOR_CLAUDE'] was modified")
                self.assertEqual(os.environ, original_env,
                                "os.environ was modified")

                # Find the unittest command call
                unittest_calls = [call for call in fake_runner.calls
                                 if len(call["cmd"]) > 2 and call["cmd"][2] == "unittest"]

                self.assertTrue(unittest_calls, "No unittest command was run")

                # Verify env was passed and contains the variable
                for call in unittest_calls:
                    self.assertIsNotNone(call["env"],
                                        "env parameter was not passed to runner")
                    self.assertEqual(call["env"].get("WORKSHOP_VALIDATOR_CLAUDE"),
                                    "/nonexistent/claude",
                                    "WORKSHOP_VALIDATOR_CLAUDE not set correctly in env")

            finally:
                # Restore original environment (shouldn't be needed, but safety)
                os.environ.clear()
                os.environ.update(original_env)

    def test_env_unchanged_after_command_failure(self):
        """os.environ is unchanged even when a command fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            venv = repo / ".venv" / "bin"
            venv.mkdir(parents=True)
            python = venv / "python"
            python.touch()

            # Make unittest command fail
            fake_runner = FakeRunner(return_codes={
                tuple([str(python), "-m", "unittest", "discover", "-s", "tests", "-q"]): 1
            })

            # Store original env state
            original_env = os.environ.copy()
            original_claude_value = os.environ.get("WORKSHOP_VALIDATOR_CLAUDE")

            try:
                # Call merged_tree_gates (will fail)
                result = team_gates.merged_tree_gates(repo, "abc123" * 5, runner=fake_runner)

                # Should fail due to unittest failure
                self.assertFalse(result)

                # Verify os.environ is still unchanged after failure
                self.assertEqual(os.environ.get("WORKSHOP_VALIDATOR_CLAUDE"), original_claude_value,
                                "os.environ['WORKSHOP_VALIDATOR_CLAUDE'] was modified after failure")
                self.assertEqual(os.environ, original_env,
                                "os.environ was modified after failure")

            finally:
                # Restore original environment
                os.environ.clear()
                os.environ.update(original_env)

    def test_all_commands_receive_env(self):
        """All commands (unittest, public_safety, compileall) receive the env."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            repo.mkdir()
            venv = repo / ".venv" / "bin"
            venv.mkdir(parents=True)
            python = venv / "python"
            python.touch()

            fake_runner = FakeRunner()

            try:
                result = team_gates.merged_tree_gates(repo, "abc123" * 5, runner=fake_runner)

                # Find all python command calls
                python_calls = [call for call in fake_runner.calls
                               if call["cmd"] and str(python) in str(call["cmd"][0])]

                # Should have 3 python commands: unittest, public_safety, compileall
                self.assertGreaterEqual(len(python_calls), 3,
                                       "Not all expected commands were run")

                # All should have env with the variable
                for call in python_calls:
                    self.assertIsNotNone(call["env"],
                                        f"env not passed to command: {call['cmd']}")
                    self.assertEqual(call["env"].get("WORKSHOP_VALIDATOR_CLAUDE"),
                                    "/nonexistent/claude",
                                    f"Variable not in env for command: {call['cmd']}")
            finally:
                pass


class TestDefaultRunner(unittest.TestCase):
    """Test that _default_runner passes env to subprocess.run."""

    @patch('subprocess.run')
    def test_default_runner_passes_env_to_subprocess(self, mock_run):
        """_default_runner must pass env parameter to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

        test_env = {"TEST_VAR": "test_value"}
        cmd = ["echo", "test"]
        cwd = Path("/tmp")

        # Call with env
        team_gates._default_runner(cmd, cwd, env=test_env)

        # Verify subprocess.run was called with env
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        self.assertIn('env', call_kwargs,
                     "env parameter not passed to subprocess.run")
        self.assertEqual(call_kwargs['env'], test_env,
                        "env parameter not passed correctly to subprocess.run")

    @patch('subprocess.run')
    def test_default_runner_passes_none_env_to_subprocess(self, mock_run):
        """_default_runner must pass env=None when no env is provided."""
        mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

        cmd = ["echo", "test"]
        cwd = Path("/tmp")

        # Call without env (should default to None)
        team_gates._default_runner(cmd, cwd)

        # Verify subprocess.run was called with env=None
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args.kwargs
        self.assertIn('env', call_kwargs,
                     "env parameter not passed to subprocess.run")
        self.assertIsNone(call_kwargs['env'],
                         "env should be None when not provided")


if __name__ == "__main__":
    unittest.main()
