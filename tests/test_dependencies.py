import unittest

from workshop_validator.dependencies import Step, plan_steps


class DependencyGateTests(unittest.TestCase):
    def setUp(self):
        self.steps = [
            Step("A"),
            Step("B", ("A",)),
            Step("C", ("B",)),
            Step("D"),
            Step("E", ("D",)),
        ]

    def test_blocked_branch_does_not_halt_independent_branch(self):
        plan = plan_steps(self.steps, {"A": "PASS", "B": "ENV_BLOCKED"})
        self.assertEqual(plan.runnable, ("D",))
        self.assertEqual(plan.blocked_by, {"C": ("B",)})
        self.assertEqual(plan.waiting, {"E": ("D",)})
        self.assertFalse(plan.complete)

    def test_transitive_blocking(self):
        plan = plan_steps(self.steps, {"A": "ENV_BLOCKED"})
        self.assertEqual(plan.blocked_by, {"B": ("A",), "C": ("A",)})
        self.assertEqual(plan.runnable, ("D",))

    def test_pending_prerequisites_are_not_runnable(self):
        plan = plan_steps(self.steps, {})
        self.assertEqual(plan.runnable, ("A", "D"))
        self.assertEqual(plan.waiting, {"B": ("A",), "C": ("B",), "E": ("D",)})

    def test_verified_recovery_unlocks_downstream(self):
        plan = plan_steps(self.steps, {"A": "PASS", "B": "PASS", "D": "PASS"})
        self.assertEqual(plan.runnable, ("C", "E"))
        self.assertEqual(plan.blocked_by, {})

    def test_rejects_premature_pass(self):
        with self.assertRaisesRegex(ValueError, "Premature PASS"):
            plan_steps(self.steps, {"B": "PASS"})

    def test_rejects_cycle(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            plan_steps([Step("A", ("B",)), Step("B", ("A",))], {})

    def test_rejects_unknown_dependency(self):
        with self.assertRaisesRegex(ValueError, "Unknown dependency"):
            plan_steps([Step("A", ("missing",))], {})

    def test_rejects_duplicate_step(self):
        with self.assertRaisesRegex(ValueError, "Duplicate step_id"):
            plan_steps([Step("A"), Step("A")], {})

    def test_rejects_unrecognized_status(self):
        with self.assertRaisesRegex(ValueError, "Invalid status"):
            plan_steps([Step("A")], {"A": "SUCCESS"})

    def test_empty_manifest_is_not_complete(self):
        self.assertFalse(plan_steps([], {}).complete)

    def test_optional_step_can_remain_blocked_when_required_pass(self):
        plan = plan_steps([Step("A"), Step("B", required=False)],
                          {"A": "PASS", "B": "ENV_BLOCKED"})
        self.assertTrue(plan.complete)

    def test_retry_wait_is_not_automatically_retried(self):
        plan = plan_steps([Step("A"), Step("B")], {"A": "RETRY_WAIT"})
        self.assertEqual(plan.runnable, ("B",))


if __name__ == "__main__":
    unittest.main()
