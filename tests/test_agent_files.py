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


if __name__ == "__main__":
    unittest.main()
