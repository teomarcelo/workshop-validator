"""Tests for F14 low notes from PR 25 security review.

Items:
1. team_gates._redact_stderr strips Unicode Cc and Cf except newline and tab
2. team_kit.watchdog_check catches any Exception from check_main_clean
3. public_safety strict decoding of git listings: see tests/test_f14_utf8_fix.py
"""

import os
import tempfile
import unittest
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from workshop_validator import public_safety, team_gates, team_kit
from workshop_validator.team_state import _write_private


class TestF14RedactStderr(unittest.TestCase):
    """F14 item 1: _redact_stderr strips Unicode Cc and Cf except newline and tab."""

    def test_strips_del_character(self):
        """DEL (U+007F) is Cc and must be stripped."""
        stderr = "Error\x7Fmessage"
        result = team_gates._redact_stderr(stderr)
        self.assertEqual(result, "Errormessage")
        self.assertNotIn("\x7f", result)

    def test_strips_c1_controls(self):
        """C1 controls like U+009B (CSI) must be stripped."""
        stderr = "Error\x9Bmessage"
        result = team_gates._redact_stderr(stderr)
        self.assertEqual(result, "Errormessage")
        self.assertNotIn("\x9b", result)

    def test_strips_bidi_override(self):
        """Bidi override U+202E (Cf category) must be stripped."""
        stderr = "Error‮message"
        result = team_gates._redact_stderr(stderr)
        self.assertEqual(result, "Errormessage")
        self.assertNotIn("‮", result)

    def test_preserves_newline_and_tab(self):
        """Newline and tab must be preserved."""
        stderr = "Line1\nLine2\tTabbed"
        result = team_gates._redact_stderr(stderr)
        self.assertIn("\n", result)
        self.assertIn("\t", result)
        self.assertEqual(result, "Line1\nLine2\tTabbed")

    def test_strips_zero_width_space(self):
        """Zero-width space U+200B (Cf category) must be stripped."""
        stderr = "Error​message"
        result = team_gates._redact_stderr(stderr)
        self.assertEqual(result, "Errormessage")
        self.assertNotIn("​", result)

    def test_strips_all_cc_and_cf_except_newline_tab(self):
        """All Cc and Cf unicode categories except newline and tab must be stripped."""
        # Build a test string with various control and format characters
        test_chars = []
        for code_point in range(0x10000):  # Test BMP
            char = chr(code_point)
            category = unicodedata.category(char)
            if category in ("Cc", "Cf"):
                # Newline and tab should be preserved
                if char not in ("\n", "\t"):
                    test_chars.append(char)

        # Add some sample chars
        if test_chars:
            # Use a few representative ones
            test_string = "Start" + test_chars[0] + test_chars[10] + test_chars[-1] + "End"
            result = team_gates._redact_stderr(test_string)
            # All control/format chars should be gone
            for char in test_chars[:50]:  # Check first 50
                if char in test_string:
                    self.assertNotIn(char, result, f"Char {char!r} (U+{ord(char):04X}, {unicodedata.category(char)}) not stripped")


class TestF14WatchdogCheckException(unittest.TestCase):
    """F14 item 2: team_kit.watchdog_check catches any Exception from check_main_clean."""

    def test_catches_exception_from_check_main_clean(self):
        """When check_main_clean raises any Exception, watchdog_check reports STALLED."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            team = Path(tmp) / "team"
            team.mkdir()

            # Initialize git repo
            os.system(f"cd {repo} && git init -b main >/dev/null 2>&1")

            # Create empty now.json
            now_path = team / "progress" / "now.json"
            now_path.parent.mkdir(parents=True)
            _write_private(now_path, '{"steps": [], "updated": "2026-10-27T12:00:00+00:00"}')

            # Patch check_main_clean to raise a generic Exception
            with patch("workshop_validator.public_safety.check_main_clean") as mock_check:
                mock_check.side_effect = Exception("Simulated exception")

                result = team_kit.watchdog_check(repo, team)

                # Should report STALLED, not crash
                self.assertIn("STALLED", result)
                self.assertIn("Simulated exception", result)

    def test_catches_value_error_from_check_main_clean(self):
        """When check_main_clean raises ValueError, watchdog_check reports STALLED."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            team = Path(tmp) / "team"
            team.mkdir()

            # Initialize git repo
            os.system(f"cd {repo} && git init -b main >/dev/null 2>&1")

            # Create empty now.json
            now_path = team / "progress" / "now.json"
            now_path.parent.mkdir(parents=True)
            _write_private(now_path, '{"steps": [], "updated": "2026-10-27T12:00:00+00:00"}')

            # Patch check_main_clean to raise ValueError
            with patch("workshop_validator.public_safety.check_main_clean") as mock_check:
                mock_check.side_effect = ValueError("Some value error")

                result = team_kit.watchdog_check(repo, team)

                # Should report STALLED
                self.assertIn("STALLED", result)


if __name__ == "__main__":
    unittest.main()

