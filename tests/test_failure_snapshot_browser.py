"""Failure snapshot on a real Chromium page with synthetic HTML. No network.

Run with RUN_BROWSER_SMOKE=1.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

import fitz

from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.guide_walk import CheckResult, GuideCheck, _redact_aria_snapshot, _roots, walk_guide

EMAIL = "learner" + "@" + "example.com"
HOST = "example" + ".invalid"
BASE = f"https://{HOST}/lightning/setup/Home"
TYPED = "typed-" + EMAIL

HTML = f"""<!doctype html>
<html><head><title>Home | {HOST}</title>
<style>body {{ margin: 0; background: #ffffff; }} #mail {{ position: absolute; left: 20px; top: 20px;
width: 300px; height: 40px; background: #ffffff; border: 0; }}
#plain {{ position: absolute; left: 400px; top: 20px; width: 300px; height: 40px; background: #ffffff; }}</style></head>
<body>
<input id="mail" aria-label="Email" type="text">
<div id="plain" contenteditable="plaintext-only">{TYPED}</div>
<div style="margin-top: 80px">
<button>Save</button>
<button>Signed in as {EMAIL}</button>
<a href="#home">Setup Home</a>
<a href="#org">Open {HOST}</a>
<div role="tablist"><div role="tab" aria-selected="true">Details</div><div role="tab">Related</div></div>
<button style="display:none">Hidden button</button>
</div>
<div role="dialog" aria-modal="true"><h2>Welcome Tour</h2><p>Dialog body words</p>
<button>Close</button></div>
<aside aria-label="Help panel"><h3>Need help?</h3></aside>
<iframe srcdoc="<button>Frame Button</button><h1>Frame heading</h1>"></iframe>
</body></html>"""


def _magenta_at(png: bytes, x: int, y: int) -> bool:
    pix = fitz.Pixmap(png)
    r, g, b = pix.pixel(x, y)[:3]
    return r > 200 and g < 60 and b > 200


@unittest.skipUnless(
    sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
    "Browser smoke only (requires Chromium)",
)
class FailureSnapshotBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        executable = os.environ.get("CHROMIUM_PATH") or None
        cls.browser = cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 800, "height": 600})
        # Only the synthetic page is served; every other request is aborted.
        self.page.route("**/*", lambda route: route.fulfill(
            status=200, content_type="text/html", body=HTML,
        ) if route.request.url.startswith(BASE) else route.abort())
        self.page.goto(BASE + "?user=" + EMAIL)
        self.page.fill("#mail", TYPED)
        self.page.wait_for_function("document.querySelector('iframe').contentDocument.querySelector('button')")
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.page.close()
        self.tmp.cleanup()

    def snapshot(self):
        href = save_failure_snapshot(
            self.page, Path(self.tmp.name), 1, "S1", "UI_MISMATCH", f"missed for {EMAIL}",
            roots=_roots(self.page), redact_tree=_redact_aria_snapshot,
        )
        return href, Path(self.tmp.name) / href

    def test_real_page_snapshot_has_names_and_headings(self):
        href, path = self.snapshot()
        self.assertEqual(href, "snapshots/001-S1.json")
        data = json.loads(path.read_text())
        self.assertEqual(data["url_path"], "/lightning/setup/Home")
        self.assertEqual(data["title"], "Home | <host>")
        buttons = data["controls"]["button"]
        for name in ("Save", "Close", "Signed in as <email>", "Frame Button"):
            self.assertIn(name, buttons)
        self.assertNotIn("Hidden button", buttons)
        self.assertIn("Setup Home", data["controls"]["link"])
        self.assertIn("Open <host>", data["controls"]["link"])
        self.assertEqual(data["controls"]["tab"], ["Details", "Related"])
        self.assertEqual(data["controls"]["dialog"], ["Welcome Tour"])
        panels = {(p["role"], p["name"]): p["headings"] for p in data["panels"]}
        self.assertEqual(panels[("dialog", "")], ["Welcome Tour"])
        self.assertEqual(panels[("complementary", "Help panel")], ["Need help?"])
        # One dialog and one aside: the main document is scanned once, not twice.
        self.assertEqual(len(data["panels"]), 2)
        self.assertEqual(data["controls"]["button"].count("Save"), 1)
        self.assertIn("- dialog", data["tree"])
        self.assertEqual(data["truncated"], "")

    def test_real_page_snapshot_has_no_email_host_or_typed_value(self):
        _href, path = self.snapshot()
        text = path.read_text()
        for secret in (EMAIL, HOST, TYPED, "learner@", "example.com", "Dialog body words"):
            self.assertNotIn(secret, text)

    def test_real_page_png_written_with_input_masked(self):
        _href, path = self.snapshot()
        data = json.loads(path.read_text())
        self.assertEqual(data["screenshot"], "snapshots/001-S1.png")
        png = (path.parent / "001-S1.png").read_bytes()
        self.assertTrue(png.startswith(b"\x89PNG"))
        # The input box is painted with the mask colour, so the typed text is hidden.
        self.assertTrue(_magenta_at(png, 60, 40))
        # The plaintext-only editable is masked too.
        self.assertTrue(_magenta_at(png, 450, 40))
        self.assertFalse(_magenta_at(png, 700, 580))

    def test_real_page_walk_writes_snapshot_for_failed_step(self):
        steps = (
            GuideStep("S1", 1, None, None, "1", "Click Next.", (0, 0, 0, 0), None, ()),
            GuideStep("S2", 1, None, None, "2", "Click Save.", (0, 0, 0, 0), None, ()),
        )

        def run(state, item):
            if item.step_id == "S1":
                return CheckResult("UI_MISMATCH", "Next was not found.", ())
            return CheckResult("PASS", "saved", ("S2:read",))

        outcome = walk_guide(
            self.page, GuideInventory(1, steps, (), ()),
            checks=(GuideCheck("synthetic", lambda item: True, run),),
            timeout_s=0, interval_s=1, snapshot_dir=Path(self.tmp.name),
        )
        self.assertEqual(outcome.statuses, {"S1": "UI_MISMATCH", "S2": "PASS"})
        self.assertEqual(outcome.snapshots, {"S1": "snapshots/001-S1.json"})
        folder = Path(self.tmp.name) / "snapshots"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["001-S1.json", "001-S1.png"])


if __name__ == "__main__":
    unittest.main()
