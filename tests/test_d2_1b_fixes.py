"""Tests for D2-1b fixes: cap fail-closed, slot following, min length, etc."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class D2_1b_FixesTests(unittest.TestCase):
    """Test cap fail-closed, assignedSlot following, min length filter, slider valuetext."""

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_cap_fails_closed_300_filler_inputs(self):
        """HIGH: Real value after 300 filler inputs must not leak (cap fails closed)."""
        # Create 300 filler inputs plus one real input = 301 total unique values
        filler_inputs = "".join([f'<input type="text" value="zqFiller{i:04d}" />' for i in range(300)])
        html = f"""<!DOCTYPE html>
        <html><body>
            {filler_inputs}
            <input type="text" id="real-input" value="zqRealValue456" />
            <button id="btn">zqRealValue456</button>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(html_content)

    href = save_failure_snapshot(
        page, run_dir, 1, "TEST", "UI_MISMATCH", "test",
        roots=[page], redact_tree=lambda x: x
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text()

            # Button name must be absent because cap was exceeded (fail closed)
            self.assertNotIn("zqrealvalue456", snapshot_text.lower(),
                            "Value leaked despite cap exceeded (should fail closed)")

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_cap_fails_closed_301_unique_inputs(self):
        """HIGH: Real value after 301 unique inputs must not leak (cap fails closed)."""
        html = """<!DOCTYPE html>
        <html><body>
            <div id="container"></div>
            <input type="text" id="real-input" value="zqRealValue789" />
            <button id="btn">zqRealValue789</button>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(html_content)

    # Create 301 UNIQUE inputs dynamically (each with different value)
    page.evaluate('''
        const container = document.getElementById("container");
        for (let i = 0; i < 301; i++) {{
            const input = document.createElement("input");
            input.type = "text";
            input.value = "UniqueValue" + i.toString().padStart(4, '0');
            container.appendChild(input);
        }}
    ''')

    href = save_failure_snapshot(
        page, run_dir, 1, "TEST", "UI_MISMATCH", "test",
        roots=[page], redact_tree=lambda x: x
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text()

            # Button name must be absent because cap was exceeded
            self.assertNotIn("zqrealvalue789", snapshot_text.lower(),
                            "Value leaked despite cap exceeded (should fail closed)")

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_assignedslot_following_plain_host(self):
        """HIGH: Light-DOM button slotted into shadow with role=combobox, plain host (no role on host)."""
        html = """<!DOCTYPE html>
        <html><body>
            <x-cb id="custom-host">
                <button id="light-btn">zqslotted12</button>
            </x-cb>
            <button id="plain-btn">Plainsave</button>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.route("https://app.example.com/", lambda route: route.fulfill(
        status=200,
        content_type="text/html",
        body=html_content
    ))
    page.goto("https://app.example.com/")

    # Set up shadow DOM: plain host with shadow root containing role=combobox
    page.evaluate('''
        const host = document.getElementById("custom-host");
        const shadow = host.attachShadow({{mode: 'open'}});
        const wrapper = document.createElement("div");
        wrapper.setAttribute("role", "combobox");
        const slot = document.createElement("slot");
        wrapper.appendChild(slot);
        shadow.appendChild(wrapper);
    ''')

    href = save_failure_snapshot(
        page, run_dir, 1, "TEST", "UI_MISMATCH", "test",
        roots=[page], redact_tree=lambda x: x
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text().lower()

            # Slotted button must be absent (it's inside a control via slot)
            self.assertNotIn("zqslotted12", snapshot_text,
                            "Slotted button leaked (should be blocked as inside combobox)")
            # Plain button must be present
            self.assertIn("plainsave", snapshot_text,
                          "Plain button missing (should be present)")

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_min_length_3_chars(self):
        """Medium: Values shorter than 3 characters are not used for matching."""
        html = """<!DOCTYPE html>
        <html><body>
            <div role="slider" id="slider">s</div>
            <button id="save-btn">Save</button>
            <button id="short-btn">s</button>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(html_content)

    href = save_failure_snapshot(
        page, run_dir, 1, "TEST", "UI_MISMATCH", "test",
        roots=[page], redact_tree=lambda x: x
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text()

            # "Save" button must keep its name (short value "s" should not blank it)
            self.assertIn('"save"', snapshot_text.lower(),
                          "Save button missing (short value should not blank it)")

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_slider_aria_valuetext(self):
        """Low: Collect aria-valuetext for role=slider."""
        html = """<!DOCTYPE html>
        <html><body>
            <div role="slider" id="slider" aria-valuetext="50 percent">50%</div>
            <button id="btn">Slider at 50 percent</button>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(html_content)

    href = save_failure_snapshot(
        page, run_dir, 1, "TEST", "UI_MISMATCH", "test",
        roots=[page], redact_tree=lambda x: x
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text()

            # Button name should be blanked because aria-valuetext matches
            self.assertNotIn("50 percent", snapshot_text.lower(),
                            "Button name not blanked by slider aria-valuetext")

    @unittest.skipUnless(os.getenv("RUN_BROWSER_SMOKE"), "browser test")
    def test_aria_hidden_case_insensitive(self):
        """Low: aria-hidden compared case-insensitively."""
        html = """<!DOCTYPE html>
        <html><body>
            <button id="btn">Visible <span aria-hidden="TRUE">zqHiddenText999</span></button>
        </body></html>
        """

        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
sys.path.insert(0, str(Path(__file__).parent.parent))
from workshop_validator.failure_snapshot import save_failure_snapshot
from workshop_validator.guide_walk import _redact_aria_snapshot

run_dir = Path({repr(tmp)}) / "run"
run_dir.mkdir()

html_content = {repr(html)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.set_content(html_content)

    href = save_failure_snapshot(
        page, run_dir, 1, "TEST", "UI_MISMATCH", "test",
        roots=[page], redact_tree=lambda x: x
    )
    print(json.dumps({{"href": href}}))
    browser.close()
"""
            script_path = Path(tmp) / "run_test.py"
            script_path.write_text(script)

            repo_root = Path(__file__).parent.parent
            env = os.environ.copy()
            env["PYTHONPATH"] = str(repo_root)
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            output = json.loads(result.stdout.strip())
            snapshot_path = Path(tmp) / "run" / output["href"]
            snapshot_text = snapshot_path.read_text()

            # Hidden text should not appear
            self.assertNotIn("zqhiddentext999", snapshot_text.lower(),
                            "aria-hidden='TRUE' text leaked (should be case-insensitive)")


if __name__ == "__main__":
    unittest.main()
