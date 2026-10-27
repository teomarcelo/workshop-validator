import subprocess
import tempfile
import unittest
from pathlib import Path

from workshop_validator.public_safety import inspect_tracked


class PublicSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def check(self, name, content="Public-safe synthetic example"):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return inspect_tracked(self.root, [name])

    def test_safe_source_and_docs_allowed(self):
        self.assertEqual(self.check("workshop_validator/demo.py"), ())
        self.assertEqual(self.check("templates/private-runner/workflow.yml"), ())
        self.assertEqual(self.check("README.md"), ())

    def test_private_artifact_paths_rejected(self):
        for name in ("guide.pdf", "screenshots/example.txt", "trace.har",
                     "private-output/result.json", "session.sqlite", ".env.local"):
            with self.subTest(name=name):
                self.assertEqual(self.check(name), ("private_artifact_path",))

    def test_path_traversal_and_symlinks_rejected(self):
        self.assertEqual(inspect_tracked(self.root, ["../outside.py"]), ("invalid_path",))
        (self.root / "target.py").write_text("safe", encoding="utf-8")
        (self.root / "link.py").symlink_to(self.root / "target.py")
        self.assertEqual(inspect_tracked(self.root, ["link.py"]), ("symlink_not_allowed",))

    def test_binary_unknown_and_oversized_rejected(self):
        self.assertEqual(self.check("archive.bin"), ("unexpected_file_type",))
        self.assertEqual(self.check("data.txt", chr(0)), ("binary_or_oversized",))
        self.assertEqual(self.check("large.txt", "x" * (1024 * 1024 + 1)),
                         ("binary_or_oversized",))

    def test_home_paths_in_content_rejected(self):
        home1 = "/" + "Users" + "/alice/file.txt"
        home2 = "/" + "home" + "/bob/file.txt"
        home3 = "C:" + "\\" + "Users" + "\\charlie\\file.txt"
        self.assertEqual(self.check("test.py", f"path = '{home1}'"),
                         ("home_path_in_content",))
        self.assertEqual(self.check("test.py", f"path = '{home2}'"),
                         ("home_path_in_content",))
        self.assertEqual(self.check("test.py", f"path = r'{home3}'"),
                         ("home_path_in_content",))

    def test_guide_name_pattern_rejected(self):
        guide1 = "ABC" + "123" + "_FULL_" + "v1.pdf"
        guide2 = "AGT" + "503" + "_FULL_" + "SU26v2-EN-EG.pdf"
        self.assertEqual(self.check("test.py", f"file = '{guide1}'"),
                         ("guide_name_in_content",))
        self.assertEqual(self.check("test.py", guide2),
                         ("guide_name_in_content",))

    def test_public_safety_like_file_with_home_path_on_non_pattern_line_blocked(self):
        # A file similar to public_safety.py with a home path NOT on a pattern line
        home_path = "/" + "Users" + "/someone/private.txt"
        content = f'''"""Safety module."""
import re
PATTERN = re.compile(r"safe")  # public-safety: pattern
leaked_path = "{home_path}"  # This should be caught
'''
        self.assertEqual(self.check("safety.py", content),
                         ("home_path_in_content",))

    def test_marker_ignored_in_other_files(self):
        # Marker should be ignored in files other than public_safety.py
        home_path = "/" + "Users" + "/leak/file.txt"
        self.assertEqual(self.check("test.py", f"leak = '{home_path}'  # public-safety: pattern"),
                         ("home_path_in_content",))

    def test_marker_ignored_on_non_regex_lines_in_public_safety(self):
        # Marker should be ignored on non-regex lines even in public_safety.py
        home_path = "/" + "Users" + "/leak/file.txt"
        content = f'leaked_var = "{home_path}"  # public-safety: pattern\n'
        self.assertEqual(self.check("workshop_validator/public_safety.py", content),
                         ("home_path_in_content",))

    def test_marker_ignored_in_differently_named_file(self):
        # Marker should be ignored even on regex lines in differently named files
        home_path = "/" + "Users" + "/leak/file.txt"
        content = f'import re\nPATTERN = re.compile(r"{home_path}")  # public-safety: pattern\n'
        self.assertEqual(self.check("workshop_validator/safety_check.py", content),
                         ("home_path_in_content",))

    def test_clean_synthetic_content_passes(self):
        self.assertEqual(self.check("test.py", "def example(): pass"), ())
        self.assertEqual(self.check("README.md", "# Synthetic Example"), ())


# ---- F6 item 6: author email checks ---------------------------------------


class PublicSafetyAuthorEmailTests(unittest.TestCase):
    """F6 item 6a: public_safety fails when commit author/committer email is not GitHub noreply or allowed bot."""

    def test_non_github_noreply_email_rejected(self):
        """Commits with non-GitHub noreply emails should be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Create base commit on origin/main
            (repo / "base.txt").write_text("base")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "branch", "origin_main"], cwd=repo, check=True, capture_output=True)

            # Change email and create a new commit
            subprocess.run(["git", "config", "user.email", "someone@host.internal.example"], cwd=repo, check=True, capture_output=True)
            (repo / "test.txt").write_text("test")
            subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "test"], cwd=repo, check=True, capture_output=True)

            # Check should fail
            from workshop_validator.public_safety import check_commit_emails
            ok, msg = check_commit_emails(repo, "origin_main")
            self.assertFalse(ok)
            self.assertIn("internal.example", msg)

    def test_github_noreply_email_accepted(self):
        """Commits with GitHub noreply emails should be accepted."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "base@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Create base commit on origin/main
            (repo / "base.txt").write_text("base")
            subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "branch", "origin_main"], cwd=repo, check=True, capture_output=True)

            # Create a new commit with GitHub noreply email
            subprocess.run(["git", "config", "user.email", "1+x@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)
            (repo / "test.txt").write_text("test")
            subprocess.run(["git", "add", "test.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "test"], cwd=repo, check=True, capture_output=True)

            # Check should pass
            from workshop_validator.public_safety import check_commit_emails
            ok, msg = check_commit_emails(repo, "origin_main")
            self.assertTrue(ok)


class PublicSafetyMainCleanTests(unittest.TestCase):
    """F6 item 6b: public_safety --main-clean mode fails when main checkout has untracked or modified files."""

    def test_main_clean_mode_fails_with_untracked_files(self):
        """--main-clean should fail when untracked files exist."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Create initial commit
            (repo / "tracked.txt").write_text("tracked")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

            # Add untracked file
            (repo / "untracked.txt").write_text("untracked")

            # Check should fail
            from workshop_validator.public_safety import check_main_clean
            ok, msg = check_main_clean(repo)
            self.assertFalse(ok)
            self.assertIn("untracked or modified", msg)

    def test_main_clean_mode_passes_on_clean_tree(self):
        """--main-clean should pass when tree is clean."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # Create initial commit
            (repo / "tracked.txt").write_text("tracked")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)

            # Check should pass
            from workshop_validator.public_safety import check_main_clean
            ok, msg = check_main_clean(repo)
            self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
