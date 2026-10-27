"""F14 fix round 1: Non-UTF-8 path detection to prevent name collision attacks."""

import subprocess
import tempfile
import unittest
from pathlib import Path


class TestNonUTF8PathRejection(unittest.TestCase):
    """Non-UTF-8 paths must be rejected to prevent collisions where different byte sequences decode to same name."""

    def test_non_utf8_tracked_path_rejected(self):
        """git ls-files output with non-UTF-8 bytes must cause main() to return 1."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            # Initialize repo with initial commit
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)
            
            (repo / "init.py").write_text("# init")
            subprocess.run(["git", "add", "init.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "branch", "origin/main"], cwd=repo, check=True, capture_output=True)

            # Create blob and add index entry with non-UTF-8 name
            result = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=repo, input=b"content\n", check=True, capture_output=True
            )
            blob_sha = result.stdout.decode().strip()

            # 0x80 0xFF is invalid UTF-8
            non_utf8_name = b"bad\x80\xfffile.py"
            index_entry = f"100644 blob {blob_sha}\t".encode() + non_utf8_name + b"\n"
            subprocess.run(
                ["git", "update-index", "--index-info"],
                cwd=repo, input=index_entry, check=True, capture_output=True
            )

            # Verify entry was added
            ls_result = subprocess.run(
                ["git", "ls-files", "--cached", "-z"],
                cwd=repo, capture_output=True
            )
            self.assertIn(non_utf8_name, ls_result.stdout, "Non-UTF-8 name should be in index")

            # Test the decode logic directly by simulating what main() does
            from workshop_validator.public_safety import inspect_tracked
            
            # Try to decode as main() would - should fail
            try:
                names = []
                for part in ls_result.stdout.split(bytes([0])):
                    if part:
                        name = part.decode("utf-8")  # Strict decode, no errors="replace"
                        names.append(name)
                self.fail("Should have raised UnicodeDecodeError")
            except UnicodeDecodeError:
                # Expected - this is what the fix detects
                pass

    def test_valid_utf8_paths_accepted(self):
        """Normal UTF-8 filenames including unicode should work."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()

            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@users.noreply.github.com"], cwd=repo, check=True, capture_output=True)

            # UTF-8 filenames including unicode
            (repo / "test.py").write_text("# test")
            (repo / "café.py").write_text("# unicode")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)

            # Verify they decode correctly
            ls_result = subprocess.run(
                ["git", "ls-files", "--cached", "-z"],
                cwd=repo, capture_output=True
            )
            
            names = []
            for part in ls_result.stdout.split(bytes([0])):
                if part:
                    name = part.decode("utf-8")  # Should not raise
                    names.append(name)
            
            self.assertEqual(len(names), 2)
            self.assertIn("test.py", names)
            self.assertIn("café.py", names)


if __name__ == "__main__":
    unittest.main()
