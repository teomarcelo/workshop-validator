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
        # Should have the failure guard directly after mktemp -d command substitution
        guard_pattern = r'mktemp -d[^)]*\)\s*\|\|'
        self.assertIsNotNone(re.search(guard_pattern, text),
                            "run-operator.md missing mktemp failure guard directly after mktemp -d")

    def test_run_operator_has_chmod_700(self):
        """run-operator.md must set mode 700 on temp folder."""
        run_operator_file = AGENTS_DIR / "run-operator.md"
        text = run_operator_file.read_text(encoding="utf-8")
        # Should have chmod 700 on the temp folder variable
        chmod_pattern = r'chmod\s+700\s+"\$D"'
        self.assertIsNotNone(re.search(chmod_pattern, text),
                            "run-operator.md missing chmod 700 on temp folder")

    def test_run_operator_has_exit_trap(self):
        """run-operator.md must have EXIT trap to remove temp folder."""
        run_operator_file = AGENTS_DIR / "run-operator.md"
        text = run_operator_file.read_text(encoding="utf-8")
        # Should have EXIT trap that removes the temp folder
        exit_trap_pattern = r'trap\s+\'rm -rf "\$D"\'\s+EXIT'
        self.assertIsNotNone(re.search(exit_trap_pattern, text),
                            "run-operator.md missing EXIT trap to remove temp folder")

    def test_run_operator_has_signal_traps(self):
        """run-operator.md must have TERM/INT/HUP traps."""
        run_operator_file = AGENTS_DIR / "run-operator.md"
        text = run_operator_file.read_text(encoding="utf-8")
        # Should have trap for TERM INT HUP signals
        signal_trap_pattern = r'trap\s+\'exit 143\'\s+TERM\s+INT\s+HUP'
        self.assertIsNotNone(re.search(signal_trap_pattern, text),
                            "run-operator.md missing TERM/INT/HUP trap")


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
        test_input = "Error for jane.doe+x@mail.example.org at foo\nuser admin@corp-test.example.com failed"
        expected_output = "Error for [email] at foo\nuser [email] failed"

        # Use /usr/bin/sed if available, otherwise fall back to shutil.which
        sed_path = "/usr/bin/sed" if Path("/usr/bin/sed").exists() else shutil.which("sed")
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


def extract_run_command(agent_text):
    """Extract the single backticked shell command that runs automate from run-operator.md."""
    # Find all backtick code blocks
    code_blocks = re.findall(r'`([^`]+)`', agent_text, re.DOTALL)
    # Filter for commands that contain workshop_validator.automate
    automate_commands = [block for block in code_blocks if 'workshop_validator.automate' in block]

    if len(automate_commands) != 1:
        raise ValueError(f"Expected exactly one backticked automate command, found {len(automate_commands)}")

    return automate_commands[0]


class RunOperatorRunLinePinsTests(unittest.TestCase):
    """F25: Pin critical guards and order on the run line."""

    def source(self):
        return (AGENTS_DIR / "run-operator.md").read_text(encoding="utf-8")

    def run_command(self):
        return extract_run_command(self.source())

    def test_exactly_one_automate_command(self):
        """run-operator.md must have exactly one backticked shell command running automate."""
        text = self.source()
        code_blocks = re.findall(r'`([^`]+)`', text, re.DOTALL)
        automate_commands = [block for block in code_blocks if 'workshop_validator.automate' in block]
        self.assertEqual(len(automate_commands), 1,
                        f"Expected exactly one backticked automate command, found {len(automate_commands)}")

    def test_mktemp_guard_on_run_line(self):
        """The mktemp -d assignment must have the guard on the run line."""
        cmd = self.run_command()
        # Pin the guard pattern: D=$(mktemp -d) || { ...; exit 2; }
        guard_pattern = r'D=\$\(mktemp -d\)\s*\|\|\s*\{[^}]*exit 2[^}]*\}'
        self.assertIsNotNone(re.search(guard_pattern, cmd),
                            "run line missing mktemp guard: D=$(mktemp -d) || { ...; exit 2; }")

    def test_order_guard_before_traps(self):
        """The mktemp guard must appear before the traps on the run line."""
        cmd = self.run_command()
        guard_match = re.search(r'D=\$\(mktemp -d\)\s*\|\|\s*\{[^}]*exit 2[^}]*\}', cmd)
        trap_match = re.search(r'trap\s+', cmd)
        self.assertIsNotNone(guard_match, "mktemp guard not found")
        self.assertIsNotNone(trap_match, "trap not found")
        self.assertLess(guard_match.start(), trap_match.start(),
                       "mktemp guard must appear before traps")

    def test_order_traps_before_chmod(self):
        """Traps must appear before chmod 700 on the run line."""
        cmd = self.run_command()
        # Find the last trap
        trap_matches = list(re.finditer(r'trap\s+', cmd))
        chmod_match = re.search(r'chmod\s+700', cmd)
        self.assertTrue(trap_matches, "no traps found")
        self.assertIsNotNone(chmod_match, "chmod 700 not found")
        self.assertLess(trap_matches[-1].start(), chmod_match.start(),
                       "traps must appear before chmod 700")

    def test_order_chmod_before_automate(self):
        """chmod 700 must appear before automate call on the run line."""
        cmd = self.run_command()
        chmod_match = re.search(r'chmod\s+700', cmd)
        automate_match = re.search(r'workshop_validator\.automate', cmd)
        self.assertIsNotNone(chmod_match, "chmod 700 not found")
        self.assertIsNotNone(automate_match, "automate call not found")
        self.assertLess(chmod_match.start(), automate_match.start(),
                       "chmod 700 must appear before automate call")

    def test_log_tail_redaction(self):
        """The tail -5 log output must be piped through the redaction sed on the run line."""
        cmd = self.run_command()
        # Look for: tail -5 "$D/run.log" | sed -E 's/.../[email]/g'
        redaction_pattern = r'tail -5 "\$D/run\.log"\s*\|\s*sed -E'
        self.assertIsNotNone(re.search(redaction_pattern, cmd),
                            "run line missing log tail redaction: tail -5 piped to sed")

    def test_no_unguarded_mktemp(self):
        """There must be no second unguarded mktemp -d on the run line."""
        cmd = self.run_command()
        # Find all mktemp -d occurrences
        mktemp_matches = list(re.finditer(r'mktemp -d', cmd))
        # Each must be followed by ) || { within a few chars
        for match in mktemp_matches:
            after = cmd[match.end():match.end()+20]
            self.assertIn(') ||', after,
                         f"Found unguarded mktemp -d at position {match.start()}")


class RunOperatorFileAccessPinsTests(unittest.TestCase):
    """F25: Pin rules for org and browser-state file access."""

    def source(self):
        return (AGENTS_DIR / "run-operator.md").read_text(encoding="utf-8")

    def test_verification_orgs_test_f_only(self):
        """verification-orgs.json must be checked only with test -f."""
        text = self.source()
        # Must mention checking with test -f
        self.assertIn("test -f", text, "missing test -f check")
        self.assertIn("verification-orgs.json", text, "missing verification-orgs.json reference")
        # Pin the exact instruction
        self.assertIn('~/.workshop-validator/verification-orgs.json', text)
        self.assertIn('test -f', text)

    def test_never_cat_verification_orgs(self):
        """run-operator.md must not cat, head, tail, less or open verification-orgs.json."""
        text = self.source()
        # Check for commands that would read the file
        forbidden = ['cat ~/.workshop-validator/verification-orgs.json',
                    'head ~/.workshop-validator/verification-orgs.json',
                    'tail ~/.workshop-validator/verification-orgs.json',
                    'less ~/.workshop-validator/verification-orgs.json',
                    'open(~/.workshop-validator/verification-orgs.json',
                    'jq ~/.workshop-validator/verification-orgs.json']
        for cmd in forbidden:
            self.assertNotIn(cmd, text, f"run-operator.md must not contain: {cmd}")

    def test_never_read_browser_state(self):
        """run-operator.md must never read any browser-state.json file."""
        text = self.source()
        # Must mention not reading browser-state.json
        self.assertIn('browser-state.json', text)
        # Check it says never read
        browser_state_context = text[max(0, text.find('browser-state.json')-200):
                                      text.find('browser-state.json')+200]
        self.assertIn('Never', browser_state_context,
                     "Should have 'Never' near browser-state.json")

    def test_no_jq_on_sensitive_files(self):
        """run-operator.md must not use jq on verification-orgs.json or browser-state.json."""
        text = self.source()
        # No jq commands on these files
        self.assertNotIn('jq', text.split('verification-orgs.json')[0] +
                        (text.split('verification-orgs.json')[1] if 'verification-orgs.json' in text else ''),
                        "jq should not be used on verification-orgs.json")


class RunOperatorFrontMatterPinsTests(unittest.TestCase):
    """F25: Pin front matter and behavioral rules."""

    def source(self):
        return (AGENTS_DIR / "run-operator.md").read_text(encoding="utf-8")

    def test_tools_exactly_bash(self):
        """Front matter tools must be exactly 'Bash'."""
        text = self.source()
        # Extract front matter
        front_matter_match = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
        self.assertIsNotNone(front_matter_match, "no front matter found")
        front_matter = front_matter_match.group(1)
        # Check tools line
        tools_match = re.search(r'^tools:\s*(.+)$', front_matter, re.MULTILINE)
        self.assertIsNotNone(tools_match, "tools line not found in front matter")
        tools_value = tools_match.group(1).strip()
        self.assertEqual(tools_value, "Bash",
                        f"tools must be exactly 'Bash', found: {tools_value}")

    def test_no_self_retry(self):
        """run-operator.md must contain the no self-retry instruction."""
        text = self.source()
        # Pin the exact sentence
        self.assertIn("Do not retry on your own", text,
                     "missing 'Do not retry on your own' instruction")


if __name__ == "__main__":
    unittest.main()
