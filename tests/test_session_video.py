"""Session video is recorded with a fake browser and linked from results.

These tests do not open a real browser and do not contact Salesforce.
"""

import io
import os
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop_validator.guide_inventory import GuideInventory
from workshop_validator.local_site import create_app
from workshop_validator.private_runner import run_uploaded_guide

try:
    from reportlab.pdfgen import canvas
except ImportError:
    canvas = None


VIDEO_BYTES = b"fake-session-video"


def pdf_bytes():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    destination = Path(path)
    try:
        document = canvas.Canvas(str(destination), pagesize=(612, 792), pageCompression=0)
        document.drawString(72, 690, "Exercise 9: Placeholder")
        document.drawString(72, 665, "Task 1: Nothing executable")
        document.drawString(90, 640, "1. Read the printed instructions")
        document.showPage()
        document.save()
        return destination.read_bytes()
    finally:
        destination.unlink(missing_ok=True)


class Harness:
    def __init__(self):
        self.launch_kwargs = None
        self.context_kwargs = None
        self.context_closed = False
        self.browser_closed = False
        self.navigations = []


def fake_playwright(harness):
    class FakeVideo:
        def __init__(self, folder):
            self.folder = Path(folder)
            self.raw = self.folder / "raw-session.webm"

        def path(self):
            if not harness.context_closed:
                raise RuntimeError("video is not finalized")
            return self.raw

    class FakePage:
        def __init__(self, folder):
            self.url = "https://example.lightning.force.com/lightning/page/home"
            self._video = FakeVideo(folder)

        @property
        def video(self):
            return self._video

        def set_default_timeout(self, timeout):
            harness.timeout = timeout

        def goto(self, url, **kwargs):
            harness.navigations.append(url)
            raise AssertionError("fake page must not navigate")

        def locator(self, selector):
            class Box:
                def count(self):
                    return 0

                def is_visible(self):
                    return False

                def inner_text(self, timeout=None):
                    return ""

                @property
                def first(self):
                    return self

            return Box()

    class FakeContext:
        def __init__(self, **kwargs):
            harness.context_kwargs = kwargs

        def new_page(self):
            return FakePage(harness.context_kwargs.get("record_video_dir", ""))

        def close(self):
            harness.context_closed = True
            folder = harness.context_kwargs.get("record_video_dir")
            if not folder:
                return
            directory = Path(folder)
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o755)
            raw = directory / "raw-session.webm"
            raw.write_bytes(VIDEO_BYTES)
            os.chmod(raw, 0o644)

    class FakeBrowser:
        def new_context(self, **kwargs):
            return FakeContext(**kwargs)

        def new_page(self, **kwargs):
            raise AssertionError("expected a recording context")

        def close(self):
            harness.browser_closed = True

    class FakeChromium:
        def launch(self, **kwargs):
            harness.launch_kwargs = kwargs
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def sync_playwright():
        return FakePlaywright()

    return sync_playwright


class SessionVideoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.private = self.root / "private"
        self.password = "correct-horse-battery"
        self.username = "reviewer@example.com"

    def test_results_page_links_the_saved_recording(self):
        if canvas is None:
            self.skipTest("reportlab required")
        harness = Harness()
        with self.patched(harness):
            client, location = self.upload()
            response, html = self.wait(client, location, lambda body: "<video" in body)
        run_id = location.rsplit("/", 1)[-1]
        href = f"/runs/{run_id}/video"
        self.assert_context_recorded(harness, self.private / run_id)
        self.assertIn(f"<video controls preload='metadata' src='{href}'>", html)
        self.assertIn(f"<a href='{href}'>Open the recording</a>", html)
        self.assertIn("Browser recording", html)
        self.assertIn("Signed in to the trial org", html)
        self.assertIn("media-src 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotIn(self.password, html)
        self.assertNotIn(self.username, html)
        self.assertNotIn(str(self.private), html)
        self.assertEqual(harness.navigations, [])
        video = self.private / run_id / "walk.webm"
        self.assert_private_file(video)
        media = client.get(href)
        self.addCleanup(media.close)
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media.mimetype, "video/webm")
        self.assertEqual(media.data, VIDEO_BYTES)
        self.assertNotIn("attachment", media.headers.get("Content-Disposition", "").lower())
        self.assertIn("private", media.headers["Cache-Control"])
        self.assertNotIn("no-store", media.headers["Cache-Control"])
        queried = client.get(href + "?path=/etc/passwd")
        self.addCleanup(queried.close)
        self.assertEqual(queried.data, VIDEO_BYTES)
        other = "cd" * 16
        planted = self.private / other
        planted.mkdir()
        (planted / "walk.webm").write_bytes(b"other-run")
        (video.parent / "secret.webm").write_bytes(b"secret-file")
        denied_other = client.get(f"/runs/{other}/video")
        denied_name = client.get(f"/runs/{run_id}/secret.webm")
        self.assertEqual(denied_other.status_code, 404)
        self.assertEqual(denied_name.status_code, 404)
        self.assertNotIn(b"other-run", denied_other.data)
        self.assertNotIn(b"secret-file", denied_name.data)
        outside = self.root / "outside.webm"
        outside.write_bytes(b"outside-video")
        video.unlink()
        video.symlink_to(outside)
        denied_link = client.get(href)
        self.assertEqual(denied_link.status_code, 404)
        self.assertNotIn(b"outside-video", denied_link.data)
        self.assertEqual(list(self.repo.rglob("*.webm")), [])

    def test_early_failure_still_shows_the_recording(self):
        if canvas is None:
            self.skipTest("reportlab required")
        harness = Harness()

        def explode(*args, **kwargs):
            raise RuntimeError("stopped early")

        with self.patched(harness), patch(
            "workshop_validator.private_runner.walk_guide", explode
        ):
            client, location = self.upload()
            response, html = self.wait(
                client,
                location,
                lambda body: "could not be completed" in body and "<video" in body,
            )
        run_id = location.rsplit("/", 1)[-1]
        href = f"/runs/{run_id}/video"
        self.assert_context_recorded(harness, self.private / run_id)
        self.assertIn(f"src='{href}'", html)
        self.assertIn("Open the recording", html)
        self.assertNotIn("http-equiv", html)
        self.assertNotIn(self.password, html)
        self.assertNotIn("stopped early", html)
        self.assertEqual(harness.navigations, [])
        video = self.private / run_id / "walk.webm"
        self.assert_private_file(video)
        media = client.get(href)
        self.addCleanup(media.close)
        self.assertEqual(media.status_code, 200)
        self.assertEqual(media.data, VIDEO_BYTES)
        self.assertEqual(response.status_code, 200)

    def test_recording_directory_inside_the_repo_is_refused(self):
        harness = Harness()
        inside = self.repo / "captured"
        inventory = GuideInventory(1, (), (), ())
        with self.patched(harness):
            result = run_uploaded_guide(
                self.username, self.password, inventory,
                output_dir=inside, repo_root=self.repo,
            )
        self.assertTrue(result.signed_in)
        self.assertIsNotNone(harness.context_kwargs)
        self.assertNotIn("record_video_dir", harness.context_kwargs)
        self.assertEqual(harness.context_kwargs["viewport"], {"width": 1500, "height": 1000})
        self.assertTrue(harness.context_closed)
        self.assertTrue(harness.browser_closed)
        self.assertFalse(inside.exists())
        self.assertEqual(list(self.repo.rglob("*.webm")), [])
        self.assertEqual(list(self.repo.rglob("browser-state.json")), [])
        self.assertEqual(harness.navigations, [])
        self.assertNotIn(self.password, result.message)

    def patched(self, harness):
        return _patches(harness)

    def upload(self):
        app = create_app(background=True, private_root=self.private, repo_root=self.repo)
        client = app.test_client()
        token = re.search(
            r"name='csrf_token' value='([^']+)'",
            client.get("/").get_data(as_text=True),
        ).group(1)
        response = client.post("/run", data={
            "csrf_token": token,
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(pdf_bytes()), "guide.pdf"),
            "no_ai_team": "1",
        })
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        self.assertRegex(location, r"^/runs/[0-9a-f]{32}$")
        return client, location

    def wait(self, client, location, ready):
        deadline = time.monotonic() + 5
        last = ""
        response = None
        while time.monotonic() < deadline:
            response = client.get(location)
            last = response.get_data(as_text=True)
            if ready(last):
                return response, last
            time.sleep(0.02)
        self.fail(last[:1500])

    def assert_context_recorded(self, harness, folder):
        self.assertEqual(harness.launch_kwargs["headless"], False)
        self.assertNotIn("channel", harness.launch_kwargs)
        self.assertEqual(
            Path(harness.context_kwargs["record_video_dir"]).resolve(),
            folder.resolve(),
        )
        self.assertEqual(harness.context_kwargs["viewport"], {"width": 1500, "height": 1000})
        self.assertEqual(
            harness.context_kwargs["record_video_size"],
            {"width": 1500, "height": 1000},
        )
        self.assertIn("Chrome/", harness.context_kwargs["user_agent"])
        self.assertNotIn("Headless", harness.context_kwargs["user_agent"])
        self.assertEqual(harness.context_kwargs["accept_downloads"], False)
        self.assertNotIn("storage_state", harness.context_kwargs)
        self.assertTrue(harness.context_closed)
        self.assertTrue(harness.browser_closed)

    def assert_private_file(self, video):
        self.assertTrue(video.is_file())
        self.assertFalse(video.is_symlink())
        self.assertEqual(video.read_bytes(), VIDEO_BYTES)
        self.assertEqual(os.stat(video).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(video.parent).st_mode & 0o777, 0o700)
        self.assertNotIn(self.repo.resolve(), video.resolve().parents)


class _patches:
    def __init__(self, harness):
        self.harness = harness
        self._stack = None

    def __enter__(self):
        from contextlib import ExitStack
        self._stack = ExitStack()
        self._stack.__enter__()
        self._stack.enter_context(patch(
            "playwright.sync_api.sync_playwright", fake_playwright(self.harness),
        ))
        self._stack.enter_context(patch(
            "workshop_validator.private_runner.login_to_salesforce",
            lambda page, username, password: None,
        ))
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._stack.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
