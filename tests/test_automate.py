import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from workshop_validator.automate import (
    FAILED, INCONCLUSIVE, SUCCESS, load_aliases, load_gate, load_orgs, main,
    next_org_index, org_key, outcome_of, save_gate,
)
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


if __name__ == "__main__":
    unittest.main()
