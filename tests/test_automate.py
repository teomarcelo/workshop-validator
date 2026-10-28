import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workshop_validator.automate import (
    FAILED, INCONCLUSIVE, SUCCESS, gate_outcome_of, load_aliases, load_gate, load_orgs, main,
    next_org_index, org_key, outcome_of, save_gate,
)
from workshop_validator.automate import _summary
from workshop_validator.report import ReportStep


def _run(*statuses, signed_in=True, notes=None):
    notes = notes or {}
    steps = tuple(
        ReportStep(f"s{i}", 1, f"Step {i}", status, note=notes.get(i, ""))
        for i, status in enumerate(statuses)
    )
    return SimpleNamespace(signed_in=signed_in, message="", report_steps=steps)


class GateTests(unittest.TestCase):
    def test_first_org_is_allowed_and_a_freeze_blocks_the_next(self):
        self.assertEqual(next_org_index({"org": 0, "frozen": False}), 0)
        self.assertIsNone(next_org_index({"org": 1, "frozen": True, "reason": "x"}))

    def test_old_gate_files_still_load(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"completed_clean": 1, "frozen": False, "reason": ""})
            self.assertEqual(load_gate(root)["org"], 1)

    def test_reset_gate_unfreezes_without_moving_the_org(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 1, "frozen": True, "reason": "x"})
            self.assertEqual(main(["--reset-gate", "--private-root", folder]), 0)
            gate = load_gate(root)
            self.assertEqual((gate["org"], gate["frozen"]), (1, False))
            self.assertEqual(oct(Path(folder, "gate.json").stat().st_mode & 0o777), "0o600")


class OutcomeTests(unittest.TestCase):
    def test_every_pass_is_a_success(self):
        self.assertEqual(outcome_of(_run("PASS", "PASS"))[0], SUCCESS)

    def test_a_screen_mismatch_is_a_real_failure(self):
        self.assertEqual(outcome_of(_run("PASS", "UI_MISMATCH", "NOT_RUN"))[0], FAILED)

    def test_sign_in_429_and_missing_checks_are_inconclusive(self):
        self.assertEqual(outcome_of(_run("PASS", signed_in=False))[0], INCONCLUSIVE)
        self.assertEqual(outcome_of(_run("PASS", "ENV_BLOCKED", "NOT_RUN"))[0], INCONCLUSIVE)
        self.assertEqual(outcome_of(_run("PASS", "NEEDS_REVIEW", "NOT_RUN"))[0], INCONCLUSIVE)
        self.assertEqual(outcome_of(_run("NOT_RUN"))[0], INCONCLUSIVE)
        loading = _run("PASS", "NOT_RUN", notes={1: "The page was still loading."})
        self.assertEqual(outcome_of(loading)[0], INCONCLUSIVE)


class PrivateFileTests(unittest.TestCase):
    def test_orgs_may_share_one_password(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "verification-orgs.json").write_text(
                json.dumps({"password": "pw", "usernames": ["a@example.test", "b@example.test"]}))
            orgs = load_orgs(Path(folder))
        self.assertEqual([o["username"] for o in orgs], ["a@example.test", "b@example.test"])
        self.assertTrue(all(o["password"] == "pw" for o in orgs))

    def test_only_approved_aliases_load(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "aliases.json").write_text(json.dumps({"aliases": [
                {"guide": "Save", "screen": "Save & Close", "approved": True},
                {"guide": "Next", "screen": "Continue", "approved": False},
            ]}))
            self.assertEqual(load_aliases(Path(folder)), {"Save": "Save & Close"})

    def test_each_user_gets_its_own_session_folder(self):
        self.assertNotEqual(org_key("a@example.test"), org_key("b@example.test"))
        self.assertEqual(org_key("A@example.test "), org_key("a@example.test"))
        self.assertNotIn("example", org_key("a@example.test"))


class GateOutcomeTests(unittest.TestCase):
    """Tests for the org-move decision, which judges the stopping step only."""

    def test_mismatch_then_needs_review_stop_gives_inconclusive(self):
        # A non-stopping UI_MISMATCH followed by NEEDS_REVIEW that stops the walk
        result = _run("PASS", "UI_MISMATCH", "PASS", "NEEDS_REVIEW", "NOT_RUN", "NOT_RUN")
        # The stopping step (NEEDS_REVIEW) is inconclusive
        self.assertEqual(gate_outcome_of(result), INCONCLUSIVE)
        # But outcome_of still reports the earlier UI_MISMATCH as FAILED
        self.assertEqual(outcome_of(result)[0], FAILED)

    def test_mismatch_then_stopping_mismatch_gives_failed(self):
        # A UI_MISMATCH that doesn't stop, then a UI_MISMATCH that stops
        result = _run("PASS", "UI_MISMATCH", "PASS", "UI_MISMATCH", "NOT_RUN", "NOT_RUN")
        # The stopping step (second UI_MISMATCH) is a real failure
        self.assertEqual(gate_outcome_of(result), FAILED)
        self.assertEqual(outcome_of(result)[0], FAILED)

    def test_walk_completes_with_mismatches_gives_failed(self):
        # Walk runs to completion with UI_MISMATCH steps that don't stop
        result = _run("PASS", "UI_MISMATCH", "PASS", "UI_MISMATCH", "PASS")
        # No stopping step, so use overall outcome
        self.assertEqual(gate_outcome_of(result), FAILED)
        self.assertEqual(outcome_of(result)[0], FAILED)

    def test_time_limit_not_run_gives_inconclusive(self):
        # A NOT_RUN step with time limit note
        result = _run("PASS", "PASS", "NOT_RUN", notes={2: "exceeded its time limit"})
        # The stopping step has the time limit note
        self.assertEqual(gate_outcome_of(result), INCONCLUSIVE)
        self.assertEqual(outcome_of(result)[0], INCONCLUSIVE)



class OrgUseTests(unittest.TestCase):
    def test_failure_after_only_sign_in_keeps_the_org(self):
        from workshop_validator.automate import org_changed
        run = _run("PASS", "UI_MISMATCH")
        run.report_steps = (
            ReportStep("a", 1, "Log in", "PASS", ("a:already-signed-in",)),
            ReportStep("b", 1, "Open settings", "PASS", ("b:personal-information",)),
            ReportStep("c", 1, "Click Save", "UI_MISMATCH"),
        )
        self.assertFalse(org_changed(run))
        run.report_steps += (ReportStep("d", 1, "Save", "PASS", ("d:personal-saved",)),)
        self.assertTrue(org_changed(run))


def _keep_org_root(folder, *, verdict="FALSE_FAILURE", step="s2", gate_org=2, summary_org=2,
                   stop="s2", verdicts=None, run_id="20990101T000000Z-org2"):
    """A synthetic private root with one run that stopped at s2 on org 2."""
    root = Path(folder)
    run_dir = root / "runs" / "org-0000000000" / run_id
    run_dir.mkdir(parents=True)
    summary = {"org": summary_org, "orgs": 3, "outcome": "failed", "run_dir": str(run_dir)}
    if stop is not None:
        summary["stop_step"] = stop
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    if verdicts is None:
        verdicts = {"run_id": run_id, "verdicts": [
            {"step_id": "s1", "verdict": "CONFIRMED_PASS", "frames": "f1.png"},
            {"step_id": step, "verdict": verdict, "frames": "f2.png"},
        ]}
    if verdicts is not False:
        (run_dir / "verdicts.json").write_text(json.dumps(verdicts), encoding="utf-8")
    save_gate(root, {"org": gate_org, "frozen": True, "reason": "x", "last_run": run_id})
    return root, run_dir


class KeepOrgTests(unittest.TestCase):
    def _keep(self, root, run_dir):
        return main(["--reset-gate", "--keep-org", str(run_dir), "--private-root", str(root)])

    def assertRefusedUnchanged(self, root, run_dir):
        before = Path(root, "gate.json").read_bytes()
        self.assertEqual(self._keep(root, run_dir), 2)
        self.assertEqual(Path(root, "gate.json").read_bytes(), before)

    def test_false_failure_stop_keeps_the_runs_org(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            self.assertEqual(self._keep(root, run_dir), 0)
            gate = load_gate(root)
            self.assertEqual((gate["org"], gate["frozen"], gate["reason"]), (1, False, ""))
            self.assertEqual(gate["kept_org_reason"], {
                "step_id": "s2", "verdict": "FALSE_FAILURE", "run_id": run_dir.name})
            self.assertEqual(next_org_index(gate), 1)
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["gate.json", "runs"])

    def test_old_summary_reads_the_stop_from_board_order_and_verifier_list_format(self):
        with tempfile.TemporaryDirectory() as folder:
            run_id = "20990101T000000Z-org2"
            verifier_form = {"run_id": run_id, "mode": "full", "verdicts": [
                {"step_id": "s2", "status": "UI_MISMATCH", "verdict": "FALSE_FAILURE",
                 "evidence": "control visible", "frames": ["a.png", "b.png"]}]}
            root, run_dir = _keep_org_root(folder, stop=None, verdicts=verifier_form, run_id=run_id)
            steps = [{"step_id": sid, "status": status} for sid, status in
                     (("s1", "PASS"), ("s2", "UI_MISMATCH"), ("s3", "NOT_RUN"))]
            board = {"sections": [{"tasks": [{"steps": steps[:2]}, {"steps": steps[2:]}]}]}
            (run_dir / "board.json").write_text(json.dumps(board), encoding="utf-8")
            self.assertEqual(self._keep(root, run_dir), 0)
            self.assertEqual(load_gate(root)["org"], 1)

    def test_bare_list_of_verdict_rows_is_read(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [{"step_id": "s2", "verdict": "FALSE_FAILURE", "frames": "f.png"}]
            root, run_dir = _keep_org_root(folder, verdicts=rows)
            self.assertEqual(self._keep(root, run_dir), 0)
            self.assertEqual(load_gate(root)["org"], 1)

    def test_refuses_unless_the_stop_verdict_is_false_failure(self):
        for verdict in ("REAL", "UNCLEAR", "CONFIRMED_PASS", "FALSE_PASS"):
            with self.subTest(verdict=verdict), tempfile.TemporaryDirectory() as folder:
                self.assertRefusedUnchanged(*_keep_org_root(folder, verdict=verdict))

    def test_refuses_when_false_failure_is_on_another_step(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertRefusedUnchanged(*_keep_org_root(folder, step="s3"))

    def test_refuses_when_the_board_stop_is_another_step(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder, stop=None)
            # s2 failed first, but the walk stopped at s3: only s3's verdict counts.
            steps = [{"step_id": "s1", "status": "PASS"}, {"step_id": "s2", "status": "UI_MISMATCH"},
                     {"step_id": "s3", "status": "NEEDS_REVIEW"}, {"step_id": "s4", "status": "NOT_RUN"}]
            (run_dir / "board.json").write_text(
                json.dumps({"sections": [{"tasks": [{"steps": steps}]}]}), encoding="utf-8")
            self.assertRefusedUnchanged(root, run_dir)

    def test_refuses_when_files_are_missing_or_malformed(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertRefusedUnchanged(*_keep_org_root(folder, verdicts=False))
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            (run_dir / "verdicts.json").write_text("{not json", encoding="utf-8")
            self.assertRefusedUnchanged(root, run_dir)
        with tempfile.TemporaryDirectory() as folder:
            no_rows = {"run_id": "20990101T000000Z-org2"}
            self.assertRefusedUnchanged(*_keep_org_root(folder, verdicts=no_rows))
        good = {"step_id": "s2", "verdict": "FALSE_FAILURE", "frames": "f.png"}
        for bad in ("junk", {"step_id": "s1", "verdict": "MAYBE"}, {"verdict": "REAL"}):
            with self.subTest(row=bad), tempfile.TemporaryDirectory() as folder:
                self.assertRefusedUnchanged(*_keep_org_root(folder, verdicts=[good, bad]))
        for org, gate_org in ((True, 1), (0, 0), ("2", 2)):
            with self.subTest(summary_org=org), tempfile.TemporaryDirectory() as folder:
                self.assertRefusedUnchanged(
                    *_keep_org_root(folder, summary_org=org, gate_org=gate_org))
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder, stop=None)
            (run_dir / "board.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
            self.assertRefusedUnchanged(root, run_dir)
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            root, run_dir = _keep_org_root(folder)
            moved = Path(elsewhere, "summary.json")
            (run_dir / "summary.json").rename(moved)
            (run_dir / "summary.json").symlink_to(moved)
            self.assertRefusedUnchanged(root, run_dir)
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            (run_dir / "summary.json").unlink()
            self.assertRefusedUnchanged(root, run_dir)
        with tempfile.TemporaryDirectory() as folder:
            self.assertRefusedUnchanged(*_keep_org_root(folder, stop=None))  # no board.json

    def test_refuses_when_the_stop_step_also_has_another_verdict(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [{"step_id": "s2", "verdict": "REAL", "frames": "a.png"},
                    {"step_id": "s2", "verdict": "FALSE_FAILURE", "frames": "b.png"}]
            self.assertRefusedUnchanged(*_keep_org_root(folder, verdicts=rows))

    def test_refuses_a_summary_whose_stop_step_is_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [{"step_id": "s2", "verdict": "FALSE_FAILURE", "frames": "a.png"}]
            self.assertRefusedUnchanged(*_keep_org_root(folder, stop="", verdicts=rows))

    def test_refuses_verdicts_written_for_another_run(self):
        with tempfile.TemporaryDirectory() as folder:
            other = {"run_id": "another-run", "verdicts": [
                {"step_id": "s2", "verdict": "FALSE_FAILURE", "frames": "f.png"}]}
            self.assertRefusedUnchanged(*_keep_org_root(folder, verdicts=other))

    def test_refuses_when_the_gate_is_not_exactly_the_runs_org_plus_one(self):
        for gate_org in (1, 3):
            with self.subTest(gate_org=gate_org), tempfile.TemporaryDirectory() as folder:
                self.assertRefusedUnchanged(*_keep_org_root(folder, gate_org=gate_org))

    def test_refuses_a_run_folder_outside_the_private_runs_folder(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as elsewhere:
            root, run_dir = _keep_org_root(folder)
            outside = Path(elsewhere) / run_dir.name
            outside.mkdir()
            for name in ("summary.json", "verdicts.json"):
                (outside / name).write_bytes((run_dir / name).read_bytes())
            self.assertRefusedUnchanged(root, outside)
            link = root / "runs" / "org-0000000000" / "linked"
            link.symlink_to(outside, target_is_directory=True)
            self.assertRefusedUnchanged(root, link)
            self.assertRefusedUnchanged(root, root / "runs")

    def assertRefusedQuietly(self, root, run_dir):
        """Exit 2, gate.json byte-identical, one output line, no private path."""
        gate_file = Path(root, "gate.json")
        before = gate_file.read_bytes()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self._keep(root, run_dir), 2)
        self.assertEqual(gate_file.read_bytes(), before)
        text = out.getvalue()
        self.assertEqual(len(text.strip().splitlines()), 1, text)
        self.assertTrue(text.startswith("Refused: "), text)
        self.assertNotIn(str(Path(root).resolve()), text)
        self.assertNotIn(str(root), text)

    def test_refuses_an_older_run_replayed_after_a_newer_real_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_a = _keep_org_root(folder)
            self.assertEqual(self._keep(root, run_a), 0)
            self.assertEqual(load_gate(root)["org"], 1)
            # Run B on the same org really fails: the gate moves on and freezes.
            run_b = run_a.parent / "20990102T000000Z-org2"
            run_b.mkdir()
            (run_b / "summary.json").write_text(json.dumps(
                {"org": 2, "outcome": "failed", "stop_step": "s5"}), encoding="utf-8")
            (run_b / "verdicts.json").write_text(json.dumps({"run_id": run_b.name, "verdicts": [
                {"step_id": "s5", "verdict": "REAL", "frames": "f.png"}]}), encoding="utf-8")
            save_gate(root, {"org": 2, "frozen": True, "reason": "x", "last_outcome": "failed",
                             "last_run": run_b.name})
            self.assertRefusedQuietly(root, run_a)

    def test_refuses_a_second_keep_for_the_same_run(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            save_gate(root, {"org": 2, "frozen": True, "reason": "x", "last_run": run_dir.name,
                             "kept_org_reason": {"step_id": "s2", "verdict": "FALSE_FAILURE",
                                                 "run_id": run_dir.name}})
            self.assertRefusedQuietly(root, run_dir)

    def test_a_kept_reason_that_is_not_an_object_does_not_crash(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            save_gate(root, {"org": 2, "frozen": True, "reason": "x", "last_run": run_dir.name,
                             "kept_org_reason": "junk"})
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self._keep(root, run_dir), 0)
            self.assertEqual(load_gate(root)["kept_org_reason"]["run_id"], run_dir.name)

    def test_refuses_a_run_folder_at_the_wrong_depth_inside_runs(self):
        for depth in ("shallow", "deep"):
            with self.subTest(depth=depth), tempfile.TemporaryDirectory() as folder:
                root, run_dir = _keep_org_root(folder)
                if depth == "shallow":
                    other = root / "runs" / run_dir.name
                else:
                    other = run_dir / run_dir.name
                other.mkdir()
                for name in ("summary.json", "verdicts.json"):
                    (other / name).write_bytes((run_dir / name).read_bytes())
                self.assertRefusedQuietly(root, other)

    def test_mistyped_or_unreadable_files_refuse_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = [{"step_id": "s2", "verdict": ["FALSE_FAILURE"], "frames": "f.png"}]
            self.assertRefusedQuietly(*_keep_org_root(folder, verdicts=rows))
        with tempfile.TemporaryDirectory() as folder:
            self.assertRefusedQuietly(*_keep_org_root(folder, stop=["s2"]))
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            Path(root, "gate.json").write_text(json.dumps([{"org": 2}]), encoding="utf-8")
            self.assertRefusedQuietly(root, run_dir)

    @unittest.skipIf(os.geteuid() == 0, "root reads any file")
    def test_unreadable_verdicts_file_refuses_without_a_traceback(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            verdicts = run_dir / "verdicts.json"
            verdicts.chmod(0)
            try:
                self.assertRefusedQuietly(root, run_dir)
            finally:
                verdicts.chmod(0o600)

    def test_load_gate_refuses_a_gate_that_is_not_an_object(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "gate.json").write_text("[1, 2]", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_gate(Path(folder))

    def test_refuses_a_summary_that_is_not_an_object(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            (run_dir / "summary.json").write_text(json.dumps([{"org": 2}]), encoding="utf-8")
            self.assertRefusedQuietly(root, run_dir)

    def test_refuses_a_verdicts_object_with_no_run_id(self):
        with tempfile.TemporaryDirectory() as folder:
            unnamed = {"verdicts": [{"step_id": "s2", "verdict": "FALSE_FAILURE", "frames": "f.png"}]}
            self.assertRefusedQuietly(*_keep_org_root(folder, verdicts=unnamed))

    def test_keep_org_needs_reset_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            root, run_dir = _keep_org_root(folder)
            before = Path(root, "gate.json").read_bytes()
            with self.assertRaises(SystemExit) as caught, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                main(["--keep-org", str(run_dir), "--private-root", str(root)])
            self.assertEqual(caught.exception.code, 2)
            self.assertIn("--keep-org needs --reset-gate", err.getvalue())
            self.assertEqual(Path(root, "gate.json").read_bytes(), before)

    def test_plain_reset_gate_still_keeps_the_moved_org_and_ignores_verdicts(self):
        with tempfile.TemporaryDirectory() as folder:
            root, _ = _keep_org_root(folder)
            self.assertEqual(main(["--reset-gate", "--private-root", str(root)]), 0)
            gate = load_gate(root)
            self.assertEqual((gate["org"], gate["frozen"]), (2, False))
            self.assertNotIn("kept_org_reason", gate)

    def test_summary_records_the_stop_step(self):
        result = _run("PASS", "UI_MISMATCH", "NOT_RUN")
        summary = _summary(result, 1, 3, FAILED, "x", Path("run"))
        self.assertEqual(summary["stop_step"], "s1")
        self.assertIsNone(_summary(_run("NOT_RUN"), 0, 3, INCONCLUSIVE, "", Path("r"))["stop_step"])


if __name__ == "__main__":
    unittest.main()
