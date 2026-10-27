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


if __name__ == "__main__":
    unittest.main()
