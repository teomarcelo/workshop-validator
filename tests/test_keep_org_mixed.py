"""Tests for keep-org with mixed inconclusive and verified false failure steps (W-167)."""
import json
import tempfile
import unittest
from pathlib import Path

from workshop_validator.automate import main, load_gate, save_gate


def _make_run(folder, *, run_id="20990101T000000Z-org2", gate_org=2, summary_org=2,
              steps=None, verdicts=None):
    """Create a synthetic run directory with board.json, summary.json, verdicts.json, and gate.json.

    steps: list of (step_id, status) tuples for board.json
    verdicts: dict mapping step_id to list of verdict strings
    """
    root = Path(folder)
    run_dir = root / "runs" / "org-0000000000" / run_id
    run_dir.mkdir(parents=True)

    # Write summary.json
    summary = {"org": summary_org, "orgs": 3, "outcome": "failed", "run_dir": str(run_dir)}
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    # Write board.json
    if steps is None:
        steps = [("s1", "PASS"), ("s2", "UI_MISMATCH"), ("s3", "NOT_RUN")]
    board_steps = [{"step_id": sid, "status": status} for sid, status in steps]
    board = {"sections": [{"tasks": [{"steps": board_steps}]}]}
    (run_dir / "board.json").write_text(json.dumps(board), encoding="utf-8")

    # Write verdicts.json
    if verdicts is None:
        verdicts = {"s1": ["CONFIRMED_PASS"], "s2": ["FALSE_FAILURE"]}
    verdict_rows = [{"step_id": sid, "verdict": v, "frames": "f.png"}
                    for sid, vlist in verdicts.items() for v in vlist]
    verdicts_doc = {"run_id": run_id, "verdicts": verdict_rows}
    (run_dir / "verdicts.json").write_text(json.dumps(verdicts_doc), encoding="utf-8")

    # Write gate.json
    save_gate(root, {"org": gate_org, "frozen": True, "reason": "x", "last_run": run_id})

    return root, run_dir


class KeepOrgMixedTests(unittest.TestCase):
    """Tests for keep-org when a run has inconclusive steps and verified false failures."""

    def _keep(self, root, run_dir):
        return main(["--reset-gate", "--keep-org", str(run_dir), "--private-root", str(root)])

    def test_mixed_inconclusive_and_verified_false_failures_allowed(self):
        """The motivating case: NEEDS_REVIEW first, then multiple verified false failures."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "NEEDS_REVIEW"),  # First non-PASS, inconclusive
                ("s3", "UI_MISMATCH"),    # Real failure
                ("s4", "FUNCTIONAL_FAIL"), # Real failure
                ("s5", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                # s2 has no verdict (or could have UNCLEAR)
                "s3": ["FALSE_FAILURE"],
                "s4": ["FALSE_FAILURE"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            self.assertEqual(self._keep(root, run_dir), 0)
            gate = load_gate(root)
            self.assertEqual((gate["org"], gate["frozen"], gate["reason"]), (1, False, ""))
            self.assertEqual(gate["kept_org_reason"]["run_id"], run_dir.name)
            self.assertEqual(set(gate["kept_org_reason"]["verified_false_failures"]),
                             {"s3", "s4"})

    def test_needs_review_with_unclear_verdict_allowed(self):
        """NEEDS_REVIEW with UNCLEAR verdict is allowed (inconclusive)."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "NEEDS_REVIEW"),
                ("s3", "UI_MISMATCH"),
                ("s4", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["UNCLEAR"],  # Inconclusive verdict on inconclusive status
                "s3": ["FALSE_FAILURE"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            self.assertEqual(self._keep(root, run_dir), 0)
            gate = load_gate(root)
            self.assertEqual(gate["kept_org_reason"]["verified_false_failures"], ["s3"])

    def test_env_blocked_and_auth_blocked_allowed_as_inconclusive(self):
        """ENV_BLOCKED and AUTH_BLOCKED are inconclusive statuses."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "ENV_BLOCKED"),
                ("s3", "AUTH_BLOCKED"),
                ("s4", "UI_MISMATCH"),
                ("s5", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s4": ["FALSE_FAILURE"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            self.assertEqual(self._keep(root, run_dir), 0)
            gate = load_gate(root)
            self.assertEqual(gate["kept_org_reason"]["verified_false_failures"], ["s4"])

    def test_real_failure_without_verdict_refused(self):
        """UI_MISMATCH without any verdict is refused."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "NEEDS_REVIEW"),
                ("s3", "UI_MISMATCH"),  # No verdict
                ("s4", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                # s3 has no verdict
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

    def test_real_failure_with_real_verdict_refused(self):
        """FUNCTIONAL_FAIL with REAL verdict is refused."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "NEEDS_REVIEW"),
                ("s3", "FUNCTIONAL_FAIL"),
                ("s4", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s3": ["REAL"],  # Real failure confirmed
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

    def test_real_failure_with_unclear_verdict_refused(self):
        """UI_MISMATCH with UNCLEAR verdict is refused (not FALSE_FAILURE)."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "UI_MISMATCH"),
                ("s3", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["UNCLEAR"],  # Not definitely false
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

    def test_real_failure_with_mixed_verdicts_refused(self):
        """UI_MISMATCH with both FALSE_FAILURE and REAL is refused."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "UI_MISMATCH"),
                ("s3", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["FALSE_FAILURE", "REAL"],  # Mixed verdict
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

    def test_false_pass_on_any_step_refused(self):
        """FALSE_PASS verdict on any step (PASS, NEEDS_REVIEW, or failure) is refused."""
        # FALSE_PASS on a PASS step
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "UI_MISMATCH"),
                ("s3", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["FALSE_PASS"],  # Should have been a failure
                "s2": ["FALSE_FAILURE"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

        # FALSE_PASS on a NEEDS_REVIEW step
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "NEEDS_REVIEW"),
                ("s3", "UI_MISMATCH"),
                ("s4", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["FALSE_PASS"],  # Step-verifier found it was wrong
                "s3": ["FALSE_FAILURE"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

        # FALSE_PASS on a real failure step
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "UI_MISMATCH"),
                ("s3", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["FALSE_PASS"],  # Marked as wrong
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

    def test_old_simple_case_still_works(self):
        """The original case: stop step is a real failure with FALSE_FAILURE verdict."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "UI_MISMATCH"),  # First and only failure
                ("s3", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["FALSE_FAILURE"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            self.assertEqual(self._keep(root, run_dir), 0)
            gate = load_gate(root)
            self.assertEqual((gate["org"], gate["frozen"]), (1, False))
            self.assertEqual(gate["kept_org_reason"]["verified_false_failures"], ["s2"])

    def test_no_real_failures_refused(self):
        """A run with only PASS and NEEDS_REVIEW steps is refused (org did not move)."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "NEEDS_REVIEW"),
                ("s3", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["UNCLEAR"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

    def test_multiple_real_failures_all_must_be_verified(self):
        """When there are multiple real failures, all must have FALSE_FAILURE."""
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "UI_MISMATCH"),
                ("s3", "FUNCTIONAL_FAIL"),
                ("s4", "UI_MISMATCH"),
                ("s5", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s2": ["FALSE_FAILURE"],
                "s3": ["FALSE_FAILURE"],
                # s4 has no verdict
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)

    def test_non_failure_with_non_inconclusive_status_refused(self):
        """A non-failure step with a non-inconclusive status is refused."""
        # RUNNING is a status that exists during a walk but shouldn't be final
        with tempfile.TemporaryDirectory() as folder:
            steps = [
                ("s1", "PASS"),
                ("s2", "RUNNING"),  # Invalid final status
                ("s3", "UI_MISMATCH"),
                ("s4", "NOT_RUN"),
            ]
            verdicts = {
                "s1": ["CONFIRMED_PASS"],
                "s3": ["FALSE_FAILURE"],
            }
            root, run_dir = _make_run(folder, steps=steps, verdicts=verdicts)

            before = (root / "gate.json").read_bytes()
            self.assertEqual(self._keep(root, run_dir), 2)
            self.assertEqual((root / "gate.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
