import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path

from workshop_validator.checkpoints import CheckpointStore, TransitionError
from workshop_validator.dependencies import Step
from workshop_validator.evidence import AssertionSpec, Observation, StepContext


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "private-checkpoints.sqlite3"
        self.store = CheckpointStore(self.db_path)
        self.steps = [Step("A"), Step("B", ("A",)), Step("C")]
        self.store.create_run("run-test", "org-test", self.steps, {
            "A": [AssertionSpec("saved", "DOM_VISIBLE", True)],
            "B": [AssertionSpec("name", "DOM_TEXT", "Created", "contains")],
            "C": [AssertionSpec("http", "API_STATUS", 200)],
        })

    @staticmethod
    def context(step="A", **kwargs):
        data = dict(run_id="run-test", org_id="org-test", step_id=step,
                    executor_id="browser", verifier_id="independent")
        data.update(kwargs)
        return StepContext(**data)

    @staticmethod
    def observed(step="A", actual=True, **kwargs):
        kinds = {"A": ("saved", "DOM_VISIBLE"),
                 "B": ("name", "DOM_TEXT"),
                 "C": ("http", "API_STATUS")}
        assertion_id, kind = kinds[step]
        data = dict(evidence_id="evidence-1", assertion_id=assertion_id,
                    kind=kind, actual=actual, run_id="run-test", org_id="org-test",
                    step_id=step, collector_id="browser")
        data.update(kwargs)
        return Observation(**data)

    def test_persisted_verified_pass_unlocks_dependent(self):
        self.assertEqual(self.store.schedule("run-test").runnable, ("A", "C"))
        token = self.store.start_step("run-test", "A")
        self.assertEqual(self.store.schedule("run-test").waiting, {"B": ("A",)})
        verdict = self.store.finish_step("run-test", "A", token,
                                         self.context(), [self.observed()])
        self.assertEqual(verdict.status, "PASS")
        reopened = CheckpointStore(self.db_path)
        self.assertEqual(reopened.status("run-test", "A"), ("PASS", 2))
        self.assertEqual(reopened.schedule("run-test").runnable, ("B", "C"))

    def test_rejects_direct_pass_from_executor(self):
        token = self.store.start_step("run-test", "A")
        with self.assertRaises(TransitionError):
            self.store.block_step("run-test", "A", token, "PASS")
        self.assertEqual(self.store.status("run-test", "A"), ("RUNNING", 1))

    def test_wrong_observation_fails_without_unblocking(self):
        token = self.store.start_step("run-test", "A")
        verdict = self.store.finish_step("run-test", "A", token,
                                         self.context(), [self.observed(actual=False)])
        self.assertEqual(verdict.status, "FUNCTIONAL_FAIL")
        self.assertEqual(self.store.schedule("run-test").blocked_by, {"B": ("A",)})

    def test_missing_evidence_needs_review(self):
        token = self.store.start_step("run-test", "A")
        verdict = self.store.finish_step("run-test", "A", token, self.context(), [])
        self.assertEqual(verdict.status, "NEEDS_REVIEW")
        self.assertFalse(self.store.schedule("run-test").complete)

    def test_429_environment_block_preserves_independent_progress(self):
        token = self.store.start_step("run-test", "A")
        self.store.block_step("run-test", "A", token, "ENV_BLOCKED")
        schedule = CheckpointStore(self.db_path).schedule("run-test")
        self.assertEqual(schedule.blocked_by, {"B": ("A",)})
        self.assertEqual(schedule.runnable, ("C",))

    def test_double_claim_and_stale_finish_rejected(self):
        token = self.store.start_step("run-test", "A")
        another = CheckpointStore(self.db_path)
        with self.assertRaises(TransitionError):
            another.start_step("run-test", "A")
        with self.assertRaises(TransitionError):
            another.finish_step("run-test", "A", token + 1,
                                self.context(), [self.observed()])
        self.assertEqual(another.status("run-test", "A"), ("RUNNING", token))

    def test_wrong_context_rejected_without_mutation(self):
        token = self.store.start_step("run-test", "A")
        with self.assertRaises(TransitionError):
            self.store.finish_step("run-test", "A", token,
                                   self.context(org_id="different"), [self.observed()])
        self.assertEqual(self.store.status("run-test", "A"), ("RUNNING", token))

    def test_unknown_run_and_step_rejected(self):
        with self.assertRaises(KeyError):
            self.store.start_step("missing", "A")
        with self.assertRaises(KeyError):
            self.store.start_step("run-test", "missing")

    def test_dependency_cannot_start_early(self):
        with self.assertRaises(TransitionError):
            self.store.start_step("run-test", "B")

    def test_duplicate_run_rejected(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_run("run-test", "org-test", self.steps, {})

    def test_invalid_manifest_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.store.create_run("bad", "org-test", [Step("x", ("y",)), Step("y", ("x",))], {})
        with self.assertRaisesRegex(ValueError, "unknown step"):
            self.store.create_run("bad2", "org-test", [Step("x")], {"other": []})

    def test_trusted_assertions_are_not_supplied_at_finish(self):
        token = self.store.start_step("run-test", "A")
        verdict = self.store.finish_step("run-test", "A", token,
                                         self.context(), [self.observed(actual="True")])
        self.assertEqual(verdict.status, "NEEDS_REVIEW")

    def test_observation_values_are_not_saved_in_checkpoint(self):
        token = self.store.start_step("run-test", "C")
        secret_like_value = "UNIQUE_RAW_OBSERVATION_DO_NOT_STORE"
        verdict = self.store.finish_step("run-test", "C", token,
                                         self.context("C"), [self.observed("C", secret_like_value)])
        self.assertEqual(verdict.status, "NEEDS_REVIEW")
        with closing(sqlite3.connect(self.db_path)) as db:
            serialized = db.execute("SELECT verification_json FROM checkpoints "
                                    "WHERE run_id=? AND step_id=?", ("run-test", "C")).fetchone()[0]
        self.assertNotIn(secret_like_value, serialized)

    def test_no_required_assertions_can_never_pass(self):
        self.store.create_run("run-empty", "org-test", [Step("empty")], {})
        token = self.store.start_step("run-empty", "empty")
        verdict = self.store.finish_step("run-empty", "empty", token,
                    StepContext("run-empty", "org-test", "empty", "worker", "verifier"), [])
        self.assertEqual(verdict.status, "NEEDS_REVIEW")

    def test_complete_requires_all_required_verified(self):
        self.assertFalse(self.store.schedule("run-test").complete)
        for step, value in (("A", True), ("B", "Created"), ("C", 200)):
            token = self.store.start_step("run-test", step)
            self.assertEqual(self.store.finish_step("run-test", step, token,
                self.context(step), [self.observed(step, value)]).status, "PASS")
        self.assertTrue(self.store.schedule("run-test").complete)


if __name__ == "__main__":
    unittest.main()
