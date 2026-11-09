"""Tests for W-147: the prove command and proportional reviewers.

Every prove test builds a tiny synthetic git repo (m.py, test_m.py, a base and a
head commit) in a temp folder and runs the real `team_gates.main`.
"""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from workshop_validator import team_gates

BASE_CODE = "def add(a, b):\n    return a - b\n"
HEAD_CODE = "def add(a, b):\n    return a + b\n"
TEST_CODE = ("import unittest\n\nimport m\n\n\nclass AddTests(unittest.TestCase):\n"
             "    def test_add(self):\n        self.assertEqual(m.add(2, 3), 5)\n")
SKIP_CODE = TEST_CODE + ("\n    @unittest.skip('synthetic skip')\n"
                         "    def test_skipped(self):\n        pass\n")
SLEEP_CODE = TEST_CODE + ("\n    def test_slow(self):\n        import time\n"
                          "        time.sleep(30)\n")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=", "-c", "commit.gpgsign=false",
                           *args], cwd=repo, check=True, capture_output=True, text=True).stdout


class ProveCommandTests(unittest.TestCase):
    """prove on synthetic repos: last line and exit code."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()

    def make_repo(self, base=BASE_CODE, head=HEAD_CODE, tests=TEST_CODE, link=None):
        _git(self.repo, "init", "-q", "-b", "trunk")
        (self.repo / "m.py").write_text(base, encoding="utf-8")
        _git(self.repo, "add", "m.py")
        _git(self.repo, "commit", "-q", "-m", "base")
        _git(self.repo, "tag", "base")
        (self.repo / "m.py").write_text(head, encoding="utf-8")
        (self.repo / "test_m.py").write_text(tests, encoding="utf-8")
        _git(self.repo, "add", "m.py", "test_m.py")
        if link is not None:  # a tracked symlink to a file outside the repo
            os.symlink(link, self.repo / "link.py")
            _git(self.repo, "add", "link.py")
        _git(self.repo, "commit", "-q", "-m", "head")

    def mutants(self, *items) -> str:
        path = self.root / "mutants.json"
        path.write_text(json.dumps(list(items)), encoding="utf-8")
        return str(path)

    def prove(self, *extra, base="base"):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = team_gates.main(["--repo", str(self.repo), "prove", f"--base={base}",
                                    "--tests", "test_m", *extra])
        lines = out.getvalue().splitlines()
        self.assertNotIn("Traceback", out.getvalue())
        return code, (lines[-1] if lines else ""), out.getvalue()

    def test_pass_prints_head_sha_and_failing_base_names(self):
        self.make_repo()
        code, last, text = self.prove()
        self.assertEqual((code, last), (0, "PROVE: PASS"))
        head = _git(self.repo, "rev-parse", "HEAD").strip()
        self.assertIn(f"prove: head {head}", text.splitlines())
        self.assertIn("prove: 1 test(s) fail on base: test_m.AddTests.test_add", text.splitlines())

    def test_subtest_error_on_base_counts_its_test_once(self):
        subtest_code = TEST_CODE.replace("        self.assertEqual(m.add(2, 3), 5)\n",
                                         "        with self.subTest(n=1):\n"
                                         "            self.assertEqual(m.add(2, 3), 5)\n")
        self.make_repo(base="def add(a, b):\n    raise ValueError('synthetic')\n", tests=subtest_code)
        code, last, text = self.prove()
        self.assertEqual((code, last), (0, "PROVE: PASS"))
        self.assertIn("prove: 1 test(s) fail on base: test_m.AddTests.test_add", text.splitlines())

    def test_skipped_test_fails(self):
        self.make_repo(tests=SKIP_CODE)
        code, last, _ = self.prove()
        self.assertEqual((code, last), (1, "PROVE: FAIL 1 test(s) skipped on head: test_m.AddTests.test_skipped"))

    def test_failing_test_on_head_fails(self):
        self.make_repo(head=BASE_CODE)
        code, last, _ = self.prove()
        self.assertEqual((code, last), (1, "PROVE: FAIL 1 test(s) failed on head: test_m.AddTests.test_add"))

    def test_test_module_that_does_not_load_fails(self):
        self.make_repo()
        code, last, _ = self.prove("--tests", "test_m", "test_missing")
        self.assertEqual((code, last), (1, "PROVE: FAIL tests did not load on head: test_missing"))

    def test_module_with_no_tests_fails(self):
        self.make_repo(tests="import unittest\n")
        code, last, _ = self.prove()
        self.assertEqual((code, last), (1, "PROVE: FAIL no tests ran on head"))

    def test_tests_that_pass_on_base_fail(self):
        self.make_repo(base=HEAD_CODE)
        code, last, _ = self.prove()
        self.assertEqual((code, last), (1, "PROVE: FAIL tests pass on base"))

    def test_killed_mutant_passes(self):
        self.make_repo()
        mutants = self.mutants({"file": "m.py", "find": "a + b", "replace": "a * b"})
        code, last, text = self.prove("--mutants", mutants)
        self.assertEqual((code, last), (0, "PROVE: PASS"))
        self.assertIn("prove: mutant 1 killed by test_m.AddTests.test_add", text.splitlines())

    def test_surviving_mutant_fails(self):
        self.make_repo()
        mutants = self.mutants({"file": "m.py", "find": "a + b", "replace": "b + a"})
        code, last, _ = self.prove("--mutants", mutants)
        self.assertEqual((code, last), (1, "PROVE: FAIL mutant survived: 1"))

    def test_import_only_mutant_fails(self):
        self.make_repo()
        mutants = self.mutants({"file": "m.py", "find": "def add",
                                "replace": "import no_such_synthetic_module\ndef add"})
        code, last, _ = self.prove("--mutants", mutants)
        self.assertEqual((code, last), (1, "PROVE: FAIL mutant broke import: 1"))

    def test_mutant_that_does_not_apply_fails(self):
        self.make_repo()
        mutants = self.mutants({"file": "m.py", "find": "not in the file", "replace": "x"})
        code, last, _ = self.prove("--mutants", mutants)
        self.assertEqual((code, last), (1, "PROVE: FAIL mutant did not apply: 1"))

    def test_outside_mutant_paths_fail_and_write_nothing(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"a + b\n")
        self.make_repo(link=str(outside))
        head_code = (self.repo / "m.py").read_bytes()
        for name in ("../outside.txt", str(outside), str(self.repo / "m.py"), "sub/../m.py",
                     "untracked.py", "link.py"):
            with self.subTest(name=name):
                mutants = self.mutants({"file": name, "find": "a + b", "replace": "MUTATED"})
                code, last, text = self.prove("--mutants", mutants)
                self.assertEqual((code, last), (1, "PROVE: FAIL bad mutant path"))
                self.assertEqual(outside.read_bytes(), b"a + b\n")
                self.assertEqual((self.repo / "m.py").read_bytes(), head_code)
                if name != "link.py":  # rejected before any phase ran
                    self.assertNotIn("prove: head", text)

    def test_uncommitted_changes_fail(self):
        self.make_repo()
        (self.repo / "m.py").write_text(HEAD_CODE + "# edit\n", encoding="utf-8")
        code, last, _ = self.prove()
        self.assertEqual((code, last), (1, "PROVE: FAIL uncommitted changes"))

    def test_untracked_file_fails(self):
        self.make_repo()
        (self.repo / "new.py").write_text("x = 1\n", encoding="utf-8")
        code, last, _ = self.prove()
        self.assertEqual((code, last), (1, "PROVE: FAIL uncommitted changes"))

    def test_bad_base_fails_without_traceback(self):
        self.make_repo()
        code, last, _ = self.prove(base="no-such-ref")
        self.assertEqual(code, 1)
        self.assertTrue(last.startswith("PROVE: FAIL git rev-parse failed"), last)
        code, last, _ = self.prove(base="--output=x")
        self.assertEqual((code, last), (1, "PROVE: FAIL bad --base"))
        self.assertFalse((self.repo / "x").exists())

    def test_timeout_fails(self):
        self.make_repo(tests=SLEEP_CODE)
        code, last, _ = self.prove("--timeout", "3")
        self.assertEqual((code, last), (1, "PROVE: FAIL timeout"))

    def test_prove_command_never_raises_on_missing_repo(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            ok = team_gates.prove_command(self.root / "missing", "base", ["test_m"])
        self.assertFalse(ok)
        self.assertTrue(out.getvalue().splitlines()[-1].startswith("PROVE: FAIL"))

    def test_merged_tree_gates_keep_the_venv_python(self):
        calls = []

        def runner(cmd, cwd, env=None):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, "", "")

        with contextlib.redirect_stdout(io.StringIO()):
            team_gates.merged_tree_gates(self.repo, "0" * 40, runner=runner)
        venv = str(self.repo / ".venv" / "bin" / "python")
        self.assertIn([venv, "-m", "workshop_validator.public_safety"], calls)


class RequiredReviewersTests(unittest.TestCase):
    """Tests for proportional required_reviewers."""

    def test_code_reviewer_always_required(self):
        """code-reviewer is always required."""
        roles = team_gates.required_reviewers([])
        self.assertIn("code-reviewer", roles)

    def test_safety_scanner_always_required(self):
        """safety-scanner is always required."""
        roles = team_gates.required_reviewers([])
        self.assertIn("safety-scanner", roles)

    def test_rule_auditor_for_rule_paths(self):
        """rule-auditor required for workshop_validator/, .claude/, .cursor/, CLAUDE.md."""
        test_cases = [
            (["workshop_validator/test.py"], True),
            ([".claude/settings.json"], True),
            ([".cursor/rules/test.mdc"], True),
            (["CLAUDE.md"], True),
            (["tests/test_something.py"], False),
        ]
        for changed, should_include in test_cases:
            roles = team_gates.required_reviewers(changed)
            if should_include:
                self.assertIn("rule-auditor", roles, f"Expected rule-auditor for {changed}")
            else:
                self.assertNotIn("rule-auditor", roles, f"Did not expect rule-auditor for {changed}")

    def test_security_reviewer_for_security_files(self):
        """security-reviewer required for security files and prefixes."""
        test_cases = [
            (["workshop_validator/team_gates.py"], True),
            ([".github/workflows/test.yml"], True),
            ([".claude/agents/test.yaml"], True),
            (["workshop_validator/checks.py"], False),
        ]
        for changed, should_include in test_cases:
            roles = team_gates.required_reviewers(changed)
            if should_include:
                self.assertIn("security-reviewer", roles, f"Expected security-reviewer for {changed}")
            else:
                self.assertNotIn("security-reviewer", roles, f"Did not expect security-reviewer for {changed}")

    def test_privacy_reviewer_for_privacy_files(self):
        """privacy-reviewer required for privacy files."""
        test_cases = [
            (["workshop_validator/private_runner.py"], True),
            (["workshop_validator/report.py"], True),
            (["workshop_validator/step_board.py"], True),
            (["workshop_validator/automate.py"], True),
            (["workshop_validator/team_gates.py"], False),
        ]
        for changed, should_include in test_cases:
            roles = team_gates.required_reviewers(changed)
            if should_include:
                self.assertIn("privacy-reviewer", roles, f"Expected privacy-reviewer for {changed}")
            else:
                self.assertNotIn("privacy-reviewer", roles, f"Did not expect privacy-reviewer for {changed}")


if __name__ == "__main__":
    unittest.main()
