"""F14 fix round 1: a tracked or untracked path that is not valid UTF-8 fails the gate.

Lossy decoding (errors="replace") let two different byte names decode to the same
text, so a file could hide behind a clean twin. main() must refuse such names.
"""

import io
import subprocess
import unittest
from contextlib import redirect_stderr
from unittest import mock

from workshop_validator import public_safety

_REAL_RUN = subprocess.run


def _fake_listing(tracked: bytes, untracked: bytes = b""):
    """Fake only the two `git ls-files` listings; every other command runs for real."""

    def run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "ls-files"] and "--cached" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=tracked, stderr=b"")
        if cmd[:2] == ["git", "ls-files"] and "--others" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=untracked, stderr=b"")
        return _REAL_RUN(cmd, *args, **kwargs)

    return run


class NonUtf8PathTests(unittest.TestCase):
    def _main(self, tracked: bytes, untracked: bytes = b""):
        err = io.StringIO()
        with mock.patch.object(public_safety.subprocess, "run", side_effect=_fake_listing(tracked, untracked)):
            with redirect_stderr(err):
                code = public_safety.main([])
        return code, err.getvalue()

    def test_non_utf8_tracked_name_fails_the_gate(self):
        code, err = self._main(b"README.md\x00a\xff.md\x00")
        self.assertEqual(code, 1)
        self.assertIn("non-UTF-8", err)

    def test_non_utf8_untracked_name_fails_the_gate(self):
        code, err = self._main(b"README.md\x00", b"b\xfe.txt\x00")
        self.assertEqual(code, 1)
        self.assertIn("non-UTF-8", err)


if __name__ == "__main__":
    unittest.main()
