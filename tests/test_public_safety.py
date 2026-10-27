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

            # Check should fail (without printing email/domain per F6-fix item 5)
            from workshop_validator.public_safety import check_commit_emails
            ok, msg = check_commit_emails(repo, "origin_main")
            self.assertFalse(ok)
            # Should NOT print email or domain
            self.assertNotIn("internal.example", msg)
            self.assertNotIn("someone@", msg)
            # Should print count only (case insensitive)
            self.assertIn("non-github noreply email", msg.lower())

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


class PublicSafetyMainCheckoutBranchTests(unittest.TestCase):
    """F9: check_main_clean also fails when main checkout is not on branch main or HEAD is detached."""

    def test_main_checkout_detached_head_rejected(self):
        """Main checkout with detached HEAD should be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()

            # Initialize main checkout
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)

            # Create initial commit on main
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Detach HEAD
            sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=main_repo, check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "checkout", "--detach", sha[:7]], cwd=main_repo, check=True, capture_output=True)

            # Check should fail
            from workshop_validator.public_safety import check_main_clean
            ok, msg = check_main_clean(main_repo)
            self.assertFalse(ok)
            self.assertIn("detached", msg.lower())
            self.assertIn(sha[:7], msg)

    def test_main_checkout_on_other_branch_rejected(self):
        """Main checkout on a branch other than main should be rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()

            # Initialize main checkout
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)

            # Create initial commit on main
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Create and checkout a different branch
            subprocess.run(["git", "checkout", "-b", "feature"], cwd=main_repo, check=True, capture_output=True)

            # Check should fail
            from workshop_validator.public_safety import check_main_clean
            ok, msg = check_main_clean(main_repo)
            self.assertFalse(ok)
            self.assertIn("feature", msg)
            self.assertIn("not main", msg)

    def test_main_checkout_on_main_passes(self):
        """Main checkout on branch main with clean tree should pass."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()

            # Initialize main checkout
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)

            # Create initial commit on main
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Check should pass
            from workshop_validator.public_safety import check_main_clean
            ok, msg = check_main_clean(main_repo)
            self.assertTrue(ok)

    def test_worktree_sees_main_checkout_branch(self):
        """A worktree should check the main checkout's branch, not its own."""
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            main_repo.mkdir()

            # Initialize main checkout
            subprocess.run(["git", "init"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=main_repo, check=True, capture_output=True)

            # Create initial commit on main
            (main_repo / "file.txt").write_text("content")
            subprocess.run(["git", "add", "file.txt"], cwd=main_repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "initial"], cwd=main_repo, check=True, capture_output=True)

            # Checkout a different branch in main
            subprocess.run(["git", "checkout", "-b", "review"], cwd=main_repo, check=True, capture_output=True)

            # Create a worktree on main branch
            worktree_path = Path(tmp) / "worktree"
            subprocess.run(["git", "worktree", "add", "-b", "wt", str(worktree_path), "main"], cwd=main_repo, check=True, capture_output=True)

            # Check from worktree should fail because main checkout is on review branch
            from workshop_validator.public_safety import check_main_clean
            ok, msg = check_main_clean(worktree_path)
            self.assertFalse(ok)
            self.assertIn("review", msg)


if __name__ == "__main__":
    unittest.main()
