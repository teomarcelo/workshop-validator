"""Instance switcher, history tabs and live report (synthetic data only)."""

import json
import tempfile
import unittest
from pathlib import Path

from workshop_validator.automate import LiveReport, append_history
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.step_board import (
    build_board, instance_runs_for, instance_switcher, main, refresh_instance_reports,
    render_live_report, render_run_report, write_history_index, write_run_report,
)


def _board(run_id):
    step = GuideStep("s1", 1, "Exercise 1", "Task 1", "1", "Click Next.", (90.0, 10.0, 200.0, 20.0), None, ())
    return build_board(GuideInventory(1, (step,), (), ()), notice="Synthetic", run_id=run_id)


def _summary(run_dir, instance):
    return {"org": 1, "orgs": 2, "outcome": "failed", "reason": "", "counts": {"PASS": 1, "UI_MISMATCH": 2},
            "instance": {"instance": instance}, "run_dir": str(run_dir), "guide_name": "guide.pdf"}


class SwitcherTests(unittest.TestCase):
    def test_one_instance_has_no_switcher(self):
        self.assertEqual(instance_switcher([{"instance": "AP52", "href": "x", "current": True}]), "")

    def test_report_links_to_the_latest_run_on_each_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a1 = root / "runs" / "org-a" / "20260101T000000Z-org1"
            a2 = root / "runs" / "org-a" / "20260102T000000Z-org1"
            b1 = root / "runs" / "org-b" / "20260103T000000Z-org2"
            for run_dir, key in ((a1, "AP52"), (a2, "AP52"), (b1, "USA9S")):
                summary = _summary(run_dir, key)
                append_history(root, {"run_id": run_dir.name, "guide": "guide.pdf", **summary})
                write_run_report(_board(run_dir.name), run_dir, summary=summary, guide_name="guide.pdf")
            history = json.loads((root / "reports" / "history.json").read_text())
            items = instance_runs_for(history, "guide.pdf", a2)
            self.assertEqual([i["instance"] for i in items], ["AP52", "USA9S"])
            self.assertTrue(items[0]["current"])
            self.assertEqual(items[1]["href"], "../../org-b/20260103T000000Z-org2/report.html")
            self.assertEqual(instance_runs_for(history, "guide.pdf", a1), [])  # superseded run
            written = refresh_instance_reports(root, "guide.pdf")
            self.assertEqual(len(written), 2)
            html = (b1 / "report.html").read_text()
            self.assertIn("Org instance:", html)
            self.assertIn("../../org-a/20260102T000000Z-org1/report.html", html)
            self.assertIn("aria-current='page' href='report.html'>USA9S", html)

    def test_header_names_the_instance(self):
        html = render_run_report(_board("r1"), summary=_summary("/tmp/x", "AP52"), guide_name="g")
        self.assertIn("AP52", html)


class HistoryTabTests(unittest.TestCase):
    def test_tabs_appear_for_several_instances_and_fail_counts_are_real(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for run_id, key in (("r1", "AP52"), ("r2", "USA9S")):
                append_history(root, {"run_id": run_id, "guide": "g", **_summary(root / run_id, key)})
            html = write_history_index(root).read_text()
            self.assertIn("data-filter='AP52'", html)
            self.assertIn("data-instance='USA9S'", html)
            self.assertIn("Fail: 2", html)

    def test_no_tabs_for_a_single_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            append_history(root, {"run_id": "r1", "guide": "g", **_summary(root / "r1", "AP52")})
            self.assertNotIn("data-filter=", write_history_index(root).read_text())


class LiveReportTests(unittest.TestCase):
    def test_live_page_refreshes_until_it_ends(self):
        live = render_live_report(guide_name="g", run_id="r", org=1, orgs=2, lines=["<b>step</b>"])
        self.assertIn("http-equiv='refresh'", live)
        self.assertIn("&lt;b&gt;step", live)
        ended = render_live_report(guide_name="g", run_id="r", org=1, orgs=2, lines=[], ended="Sign-in failed.")
        self.assertNotIn("http-equiv='refresh'", ended)
        self.assertIn("Sign-in failed.", ended)

    def test_opens_once_and_counts_finished_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            opened = []
            live = LiveReport(out, guide_name="g", run_id="r", org=1, orgs=1, opener=opened.append,
                              min_interval_s=0)
            live.update("starting", "go", force=True)
            (out / "progress.json").write_text(json.dumps({"step_id": "s1", "status": "PASS"}))
            live.update("running", "Step 2 of 3")
            self.assertEqual(len(opened), 1)
            self.assertTrue(opened[0].startswith("file://"))
            html = (out / "report.html").read_text()
            self.assertIn("Step 2 of 3", html)
            self.assertIn("<span class='stat-value'>1</span><span class='stat-label'>Passed", html)
            self.assertEqual(oct((out / "report.html").stat().st_mode & 0o777), "0o600")

    def test_no_opener_never_opens(self):
        with tempfile.TemporaryDirectory() as folder:
            LiveReport(Path(folder), guide_name="g", run_id="r", org=1, orgs=1, opener=None).update(
                "starting", "go", force=True)
            self.assertTrue((Path(folder) / "report.html").is_file())


class CliTests(unittest.TestCase):
    def test_documented_render_and_history_flags_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            run_dir = root / "runs" / "org-a" / "r1"
            summary = _summary(run_dir, "AP52")
            append_history(root, {"run_id": "r1", "guide": "guide.pdf", **summary})
            write_run_report(_board("r1"), run_dir, summary=summary, guide_name="guide.pdf")
            self.assertEqual(main(["--render", str(run_dir)]), 0)
            self.assertEqual(main(["--history", str(root)]), 0)
            self.assertEqual(main(["render", str(root / "missing")]), 1)


if __name__ == "__main__":
    unittest.main()
