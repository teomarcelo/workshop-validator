"""V31: a browser download with the guide's file name is evidence for a Download step. Synthetic text only."""

import os
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer  # no reverse DNS lookup on bind, unlike HTTPServer

from workshop_validator.guide_walk import DOWNLOAD_NOTE, OUTSIDE_NOTE, WalkState, walk_guide

try:  # the module still loads on base code, so the fail-without proof can run
    from workshop_validator.guide_walk import _click_for_download
except ImportError:
    def _click_for_download(state, click, got):
        raise AssertionError("_click_for_download is missing")
from tests.test_guide_walk import Clock, Page, inventory_of, step

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

URL = "https://example.lightning.force.com/lightning/o/ContentDocument/home"
STEP_TEXT = "Click the dropdown arrow to the far right of {row} and click Download."
LOCAL_TEXT = "Open your Downloads folder and move the downloaded file to your desktop."


class FakeDownload:
    def __init__(self, filename):
        self.suggested_filename = filename
        self.calls = []

    def cancel(self):
        self.calls.append("cancel")

    def delete(self):
        self.calls.append("delete")


class _Info:
    def __init__(self, page):
        self.page = page

    @property
    def value(self):
        if self.page.fired is None:
            raise TimeoutError("no download")
        return self.page.fired


class _Expect:
    """Like Playwright's expect_download: no download by the end of the block raises."""

    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return _Info(self.page)

    def __exit__(self, exc_type, exc, tb):
        self.page.exits.append(exc is None)
        if exc is not None:
            return False
        if self.page.fired is None:
            raise TimeoutError("no download")
        return False


class DownloadPage(Page):
    def __init__(self, url):
        super().__init__(url)
        self.fired = None
        self.waits = []
        self.exits = []  # True: the block ended normally, so the wait for a download ran

    def expect_download(self, predicate=None, *, timeout=None):
        self.waits.append(timeout)
        return _Expect(self)


BUTTONS = ("Show Actions", "Show 2 more actions")


def files_page(rows, filename=None, page_class=DownloadPage):
    """Each row holds a link with its own name and its own actions button; Download may fire FILENAME."""
    page = page_class(URL)
    for row, button in zip(rows, BUTTONS):
        page.roles.add(("row", row))
        page.roles.add(("link", row))
        page.roles.add(("button", button))
        page.children[("row", row)] = [("link", row), ("button", button)]
    page.download = None if filename is None else FakeDownload(filename)

    def react(kind, name):
        if kind == "button" and name in BUTTONS:
            page.roles.add(("menuitem", "Download"))
        if kind == "menuitem" and name == "Download" and page.download is not None:
            page.fired = page.download

    page.on_click = react
    return page


class DownloadEvidenceTests(unittest.TestCase):
    def walk(self, page, *texts):
        clock = Clock()
        items = [
            step(f"s{i}", "Exercise 1: Files", "Task 2: Download", str(i + 1), text)
            for i, text in enumerate(texts)
        ]
        return walk_guide(
            page, inventory_of(*items), checks=None,
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )

    def test_exact_file_name_is_pass_with_download_evidence(self):
        page = files_page(["Report Alpha"], "Report Alpha.csv")
        outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertEqual(outcome.statuses["s0"], "PASS")
        self.assertIn("download started: Report Alpha.csv", outcome.notes["s0"])
        self.assertTrue(outcome.evidence["s0"])
        self.assertIn(("click", "menuitem", "Download"), page.events)

    def test_download_is_cancelled_and_deleted(self):
        page = files_page(["Report Alpha"], "Report Alpha.csv")
        self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertIn("delete", page.download.calls)
        self.assertIn("cancel", page.download.calls)

    def test_name_match_ignores_case_and_spacing(self):
        page = files_page(["Report Alpha"], "report   ALPHA.CSV")
        outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertEqual(outcome.statuses["s0"], "PASS")

    def test_other_file_name_is_needs_review_and_deleted(self):
        page = files_page(["Report Alpha", "Report Beta"], "Report Beta.csv")
        outcome = self.walk(
            page, STEP_TEXT.format(row="Report Alpha"), STEP_TEXT.format(row="Report Beta"),
        )
        self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")
        self.assertIn(DOWNLOAD_NOTE, outcome.notes["s0"])
        self.assertIn("a file with a different name", outcome.notes["s0"])
        self.assertIn("delete", page.download.calls)
        self.assertEqual(outcome.statuses["s1"], "NOT_RUN")  # the wrong file freezes the walk

    def test_other_file_name_is_not_printed_in_the_note(self):
        page = files_page(["Report Alpha"], "Gamma Private Notes.csv")
        outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")
        self.assertNotIn("Gamma", outcome.notes["s0"])
        self.assertNotIn("Private Notes", outcome.notes["s0"])
        self.assertIn("a file with a different name", outcome.notes["s0"])

    def test_extra_dotted_part_before_extension_is_not_a_match(self):
        for filename in ("Report Alpha.v2.pdf", "Report Alpha.pdf.exe"):
            with self.subTest(filename=filename):
                page = files_page(["Report Alpha"], filename)
                outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
                self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")
                self.assertNotIn("download started:", outcome.notes["s0"])

    def test_non_download_menu_item_gets_no_download_wait(self):
        page = files_page(["Report Alpha"], "Report Alpha.csv")
        react = page.on_click

        def with_edit(kind, name):
            react(kind, name)
            if kind == "button" and name in BUTTONS:
                page.roles.add(("menuitem", "Edit"))

        page.on_click = with_edit
        text = "Click the dropdown arrow to the far right of Report Alpha and click Edit."
        outcome = self.walk(page, text)
        self.assertIn(("click", "menuitem", "Edit"), page.events)
        self.assertEqual(page.waits, [])
        self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")
        self.assertEqual(
            outcome.notes["s0"],
            "Opened the dropdown in the Report Alpha row and clicked Edit. No change could be read from the page.",
        )

    def test_longer_file_name_is_not_a_match(self):
        page = files_page(["Report Alpha"], "Report Alpha copy.csv")
        outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")

    def test_empty_file_name_is_needs_review(self):
        page = files_page(["Report Alpha"], "")
        outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")

    def test_no_download_event_is_needs_review_and_stops(self):
        page = files_page(["Report Alpha", "Report Beta"], None)
        outcome = self.walk(
            page, STEP_TEXT.format(row="Report Alpha"), STEP_TEXT.format(row="Report Beta"),
        )
        self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")
        self.assertIn(DOWNLOAD_NOTE, outcome.notes["s0"])
        self.assertEqual(outcome.statuses["s1"], "NOT_RUN")

    def test_download_wait_is_bounded(self):
        page = files_page(["Report Alpha"], "Report Alpha.csv")
        self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertTrue(page.waits)
        for timeout in page.waits:
            self.assertIsNotNone(timeout)
            self.assertGreaterEqual(timeout, 1000)
            self.assertLessEqual(timeout, 15000)

    def test_page_without_download_wait_stays_needs_review(self):
        page = files_page(["Report Alpha"], "Report Alpha.csv", page_class=Page)
        outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertEqual(outcome.statuses["s0"], "NEEDS_REVIEW")
        self.assertIn(DOWNLOAD_NOTE, outcome.notes["s0"])

    def test_local_step_after_download_is_not_run_and_walk_continues(self):
        page = files_page(["Report Alpha", "Report Beta"], "Report Alpha.csv")
        outcome = self.walk(
            page, STEP_TEXT.format(row="Report Alpha"), LOCAL_TEXT, STEP_TEXT.format(row="Report Beta"),
        )
        self.assertEqual(outcome.statuses["s0"], "PASS")
        self.assertEqual(outcome.statuses["s1"], "NOT_RUN")
        self.assertEqual(outcome.notes["s1"], OUTSIDE_NOTE)
        self.assertIn(("click", "button", "Show 2 more actions"), page.events)
        self.assertNotEqual(outcome.statuses["s2"], "NOT_RUN")

    def test_unclicked_download_item_does_not_wait_or_pass(self):
        page = files_page(["Report Alpha"], "Report Alpha.csv")
        page.fired = page.download  # a stray download from before; it must not count
        react = page.on_click

        def refuse(kind, name):
            if kind == "menuitem":
                raise RuntimeError("covered")
            react(kind, name)

        page.on_click = refuse
        outcome = self.walk(page, STEP_TEXT.format(row="Report Alpha"))
        self.assertNotEqual(outcome.statuses["s0"], "PASS")
        self.assertEqual(outcome.statuses["s0"], "UI_MISMATCH")
        self.assertTrue(page.exits)
        self.assertNotIn(True, page.exits)

    def test_wait_is_capped_by_step_time_left(self):
        page = DownloadPage(URL)
        page.fired = FakeDownload("x.csv")
        state = WalkState(page, clock=lambda: 100.0, step_deadline=103.0)
        got = []
        self.assertTrue(_click_for_download(state, lambda: True, got))
        self.assertEqual(page.waits, [3000.0])
        self.assertEqual(got, [page.fired])

    def test_wait_is_at_least_one_second(self):
        page = DownloadPage(URL)
        page.fired = FakeDownload("x.csv")
        state = WalkState(page, clock=lambda: 200.0, step_deadline=100.0)
        _click_for_download(state, lambda: True, [])
        self.assertEqual(page.waits, [1000.0])

    def test_local_step_is_not_run_with_outside_note_and_next_step_runs(self):
        page = files_page(["Report Alpha"], "Report Alpha.csv")
        outcome = self.walk(page, LOCAL_TEXT, STEP_TEXT.format(row="Report Alpha"))
        self.assertEqual(outcome.statuses["s0"], "NOT_RUN")
        self.assertEqual(outcome.notes["s0"], OUTSIDE_NOTE)
        self.assertIn(("click", "button", "Show Actions"), page.events)

    def test_browser_step_naming_downloads_row_is_not_outside(self):
        page = files_page(["Downloads Summary"], "Downloads Summary.csv")
        outcome = self.walk(page, STEP_TEXT.format(row="Downloads Summary"))
        self.assertEqual(outcome.statuses["s0"], "PASS")
        self.assertNotEqual(outcome.notes["s0"], OUTSIDE_NOTE)


ROW_PAGE = """<!doctype html><html><body>
<table><tbody>
<tr role="row" aria-label="Report Alpha"><td><a href="#">Report Alpha</a></td>
<td><button aria-haspopup="true" aria-label="Show Actions"
 onclick="document.getElementById('m').hidden=false">v</button></td></tr>
</tbody></table>
<div role="menu" id="m" hidden><a role="menuitem" href="/file">Download</a></div>
</body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/file":
            body = b"a,b\n1,2\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", 'attachment; filename="Report Alpha.csv"')
        else:
            body = ROW_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
                     "Synthetic Chromium smoke test only")
class RealDownloadTests(unittest.TestCase):
    def test_attachment_download_is_pass_and_deleted(self):
        server = ThreadingTCPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        folder = tempfile.mkdtemp(prefix="v31-")
        self.addCleanup(shutil.rmtree, folder, True)
        pw = sync_playwright().start()
        self.addCleanup(pw.stop)
        browser = pw.chromium.launch(headless=True, downloads_path=folder)
        self.addCleanup(browser.close)
        page = browser.new_page(accept_downloads=True)
        page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
        seen = []
        page.on("download", lambda download: seen.append(download))
        item = step("s0", "Exercise 1: Files", "Task 2: Download", "1", STEP_TEXT.format(row="Report Alpha"))
        outcome = walk_guide(page, inventory_of(item), checks=None, timeout_s=5, interval_s=0.2)
        self.assertEqual(outcome.statuses["s0"], "PASS", outcome.notes["s0"])
        self.assertIn("download started: Report Alpha.csv", outcome.notes["s0"])
        self.assertEqual(len(seen), 1)
        try:
            kept = seen[0].path()  # waits for the download to end; deleted or cancelled may raise
        except Exception:
            kept = None
        self.assertFalse(kept is not None and os.path.exists(kept), kept)
        self.assertEqual(os.listdir(folder), [])


if __name__ == "__main__":
    unittest.main()
