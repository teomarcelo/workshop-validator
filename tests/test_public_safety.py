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


if __name__ == "__main__":
    unittest.main()
