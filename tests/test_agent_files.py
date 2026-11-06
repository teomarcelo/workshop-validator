"""Text checks for agent prompt files (synthetic, stdlib only)."""

from pathlib import Path
import re
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"


class TestRunnerReplyRulesTests(unittest.TestCase):
    def source(self):
        return (AGENTS_DIR / "test-runner.md").read_text(encoding="utf-8")

    def test_green_requires_all_four_steps_and_both_step4_commands(self):
        text = self.source()
        # Must assert on the exact GREEN rule phrase from 10f3ccd (includes backtick after GREEN)
        self.assertIn("`GREEN` only when all four steps ran, browser smoke shows no skipped tests, and both step-4 commands exited 0", text)

    def test_red_for_gate_fail_or_step_not_run(self):
        text = self.source()
        # Must assert on the exact RED rule phrase from 10f3ccd
        self.assertIn("Any failing step, any gate FAIL line, or any step not run is RED", text)

    def test_early_stop_message_names_browser_smoke_and_gates_not_run(self):
        text = self.source()
        # Must assert on the exact early-stop message phrases from 10f3ccd
        self.assertIn("browser smoke and gates not run: unit RED", text)
        self.assertIn("browser smoke and gates not run: compile RED", text)

    def test_step4_runs_public_safety_and_team_gates_all(self):
        text = self.source()
        # Find step 4
        step4_match = re.search(r'4\.\s+Gates.*?(?=\n\n|\n[A-Z]|\Z)', text, re.DOTALL)
        self.assertIsNotNone(step4_match, "Step 4 not found")
        step4_text = step4_match.group(0)
        # Should contain both commands
        self.assertIn("workshop_validator.public_safety", step4_text)
        self.assertIn("workshop_validator.team_gates all", step4_text)


class AgentFilesSharedRulesTests(unittest.TestCase):
    def test_all_agent_files_end_with_team_rules_block(self):
        expected_heading = "## Team rules (shared by every role)"
        for agent_file in AGENTS_DIR.glob("*.md"):
            with self.subTest(file=agent_file.name):
                text = agent_file.read_text(encoding="utf-8").rstrip()
                # Should have the team rules heading
                self.assertIn(expected_heading, text,
                              f"{agent_file.name} missing team rules heading")
                # Team rules should be at the end (after the last non-empty line before it)
                # Find the position of team rules
                rules_pos = text.rfind(expected_heading)
                self.assertGreater(rules_pos, 0)
                # Everything after the heading should be part of team rules
                after_heading = text[rules_pos + len(expected_heading):].strip()
                # Should have some content after the heading
                self.assertTrue(after_heading,
                                f"{agent_file.name} has empty team rules section")


# ---- F6 item 7: team-rules block must be LAST ## heading ------------------


class TeamRulesBlockPositionTests(unittest.TestCase):
    """F6 item 7: team-rules block must be the LAST ## heading."""

    def test_team_rules_is_last_h2_heading(self):
        """Team rules block should be the last ## heading in each agent file."""
        expected_heading = "## Team rules (shared by every role)"
        for agent_file in AGENTS_DIR.glob("*.md"):
            with self.subTest(file=agent_file.name):
                text = agent_file.read_text(encoding="utf-8")
                rules_pos = text.find(expected_heading)
                self.assertGreater(rules_pos, 0, f"{agent_file.name} missing team rules heading")

                # Check that no ## heading appears after team rules
                after_rules = text[rules_pos + len(expected_heading):]
                h2_pattern = re.compile(r'^## ', re.MULTILINE)
                match = h2_pattern.search(after_rules)
                self.assertIsNone(match,
                                  f"{agent_file.name} has ## heading after team rules at position {rules_pos + len(expected_heading) + match.start() if match else 0}")

    def test_agents_folder_not_empty(self):
        """Agents folder must not be empty."""
        agent_files = list(AGENTS_DIR.glob("*.md"))
        self.assertGreater(len(agent_files), 0, "Agents folder is empty")


class RunOperatorPrivateOutputTests(unittest.TestCase):
    """F23: run-operator writes output only to a private temp folder."""

    def test_run_operator_no_tmp_paths(self):
        """run-operator.md must not write to world-readable /tmp paths."""
        run_operator_file = AGENTS_DIR / "run-operator.md"
        text = run_operator_file.read_text(encoding="utf-8")
        # Should not contain /tmp/ paths that could be world-readable
        self.assertNotIn("/tmp/", text,
                         "run-operator.md contains /tmp/ path; must use mktemp -d with mode 0700")

    def test_run_operator_has_mktemp_guard(self):
        """run-operator.md must guard against mktemp -d failure."""
        run_operator_file = AGENTS_DIR / "run-operator.md"
        text = run_operator_file.read_text(encoding="utf-8")
        # Should have mktemp -d
        self.assertIn("mktemp -d", text, "run-operator.md missing mktemp -d")
        # Should have the failure guard
        self.assertIn("|| ", text, "run-operator.md missing mktemp failure guard")


class RunOperatorEmailRedactionTests(unittest.TestCase):
    """F23: run-operator must redact email addresses in stderr output."""

    def test_email_redaction_works_on_synthetic_addresses(self):
        """Email redaction sed expression must work with both BSD and GNU sed."""
        import subprocess
        import shutil

        run_operator_file = AGENTS_DIR / "run-operator.md"
        text = run_operator_file.read_text(encoding="utf-8")

        # Extract the sed expression from the file
        # Look for sed -E 's/...pattern.../[email]/g'
        sed_match = re.search(r"sed -E 's/([^']+)/\[email\]/g'", text)
        self.assertIsNotNone(sed_match, "sed expression not found in run-operator.md")
        sed_pattern = sed_match.group(1)

        # Test with synthetic email addresses
        test_input = "Error for jane.doe+x@example.co.uk at foo\nuser admin@corp-test.example.com failed"
        expected_output = "Error for [email] at foo\nuser [email] failed"

        # Use system sed if available, otherwise skip
        sed_path = shutil.which("sed")
        if not sed_path:
            self.skipTest("sed not found in PATH")

        # Run sed with the extracted pattern
        result = subprocess.run(
            [sed_path, "-E", f"s/{sed_pattern}/[email]/g"],
            input=test_input,
            capture_output=True,
            text=True,
            timeout=5
        )

        self.assertEqual(result.returncode, 0, f"sed failed: {result.stderr}")
        self.assertEqual(result.stdout.strip(), expected_output,
                         f"Email redaction failed. Got: {result.stdout.strip()}")


if __name__ == "__main__":
    unittest.main()
