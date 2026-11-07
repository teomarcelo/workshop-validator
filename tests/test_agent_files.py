"""Text checks for agent prompt files (synthetic, stdlib only)."""

from pathlib import Path
import hashlib
import re
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

# SHA-256 of .claude/agents/run-operator.md from main
RUN_OPERATOR_SHA256 = "049a8b208c7e7faa5fb98a3c10366f145843f0948e837c268271bbfc960fafa9"

# Exact content of run-operator.md above "## Team rules"
RUN_OPERATOR_CONTENT = """---
name: run-operator
description: Runs one guide walk on the current verification org with `automate --json` and returns the sanitized JSON summary. Use for every live run in /validate-guide.
tools: Bash
model: haiku
---
You run exactly one org walk and report its summary. You never read credentials.

1. Check only that `~/.workshop-validator/verification-orgs.json` exists (`test -f`). Never cat, read or print it, and never read any `browser-state.json`.
2. Run, from the repo root:
   `D=$(mktemp -d) || { echo "mktemp failed"; exit 2; }; trap 'rm -rf "$D"' EXIT; trap 'exit 143' TERM INT HUP; chmod 700 "$D"; .venv/bin/python -m workshop_validator.automate --guide "<pdf>" --json > "$D/run.json" 2> "$D/run.log"; EXIT=$?; echo $EXIT; cat "$D/run.json"; if [ $EXIT -eq 2 ]; then tail -5 "$D/run.log" | sed -E 's/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}/[email]/g'; fi; rm -rf "$D"`
   A walk can take longer than one 10-minute Bash call: run it with `run_in_background: true` and wait for the completion notice. Do not retry on your own.
   The command opens the live `report.html` in the user's browser; that is expected. Do not pass `--no-open`.
3. Return only: the exit code (0 success, 1 real failure, 3 inconclusive, 2 refused) and the JSON output. If the exit code is 2, the last 5 lines from stderr are already included with email addresses redacted.

Never return step text from the log beyond those lines. Never change the gate yourself."""


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

        # Extract the sed expression from the run line
        cmd = extract_run_command(text)
        # Look for sed -E 's/...pattern.../[email]/g' after tail -5 "$D/run.log" |
        sed_match = re.search(r"tail -5 \"\$D/run\.log\"\s*\|\s*sed -E 's/([^']+)/\[email\]/g'", cmd)
        self.assertIsNotNone(sed_match, "sed expression not found after tail -5 in run command")
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


class RunOperatorExactContentTests(unittest.TestCase):
    """F25: Pin run-operator.md by exact content."""

    def source(self):
        return (AGENTS_DIR / "run-operator.md").read_text(encoding="utf-8")

    def test_sha256_matches(self):
        """run-operator.md SHA-256 must match the pinned hash."""
        run_operator_file = AGENTS_DIR / "run-operator.md"
        content = run_operator_file.read_bytes()
        actual_hash = hashlib.sha256(content).hexdigest()
        self.assertEqual(
            actual_hash,
            RUN_OPERATOR_SHA256,
            "run-operator.md changed. If the change is intended and reviewed, update RUN_OPERATOR_SHA256 in this test in the same PR, and ask for a security review."
        )

    def test_content_above_team_rules_matches_exactly(self):
        """Content above '## Team rules' must match the pinned copy exactly."""
        text = self.source()
        team_rules_marker = "## Team rules (shared by every role)"
        team_rules_pos = text.find(team_rules_marker)
        self.assertGreater(team_rules_pos, 0, "Team rules marker not found")

        actual_content = text[:team_rules_pos].rstrip()
        expected_content = RUN_OPERATOR_CONTENT.rstrip()

        # Compare line by line for readable diff
        actual_lines = actual_content.split('\n')
        expected_lines = expected_content.split('\n')

        for i, (actual, expected) in enumerate(zip(actual_lines, expected_lines), 1):
            self.assertEqual(
                actual,
                expected,
                f"run-operator.md line {i} changed. If the change is intended and reviewed, update RUN_OPERATOR_CONTENT in this test in the same PR, and ask for a security review.\nExpected: {expected!r}\nActual:   {actual!r}"
            )

        # Check line count
        self.assertEqual(
            len(actual_lines),
            len(expected_lines),
            f"run-operator.md line count changed. If the change is intended and reviewed, update RUN_OPERATOR_CONTENT in this test in the same PR, and ask for a security review.\nExpected {len(expected_lines)} lines, got {len(actual_lines)}"
        )

    def test_exactly_one_automate_command(self):
        """run-operator.md must have exactly one backticked shell command running automate."""
        text = self.source()
        # Call the helper; it will raise if not exactly one
        try:
            extract_run_command(text)
        except ValueError as e:
            self.fail(str(e))


if __name__ == "__main__":
    unittest.main()
