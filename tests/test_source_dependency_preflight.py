"""Public-safe source-bound dependency preflight tests, for full repository CI."""
from hashlib import sha256
from types import SimpleNamespace
import unittest

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.reconciliation import (
    AddedAction, CandidateReview, PageReview, SourceReview, inventory_sha256,
)
from workshop_validator.private_preflight import (
    SourcePreflightError, require_executable_source_review,
)


def plan(step_id, page, dependencies=()):
    return SimpleNamespace(step_id=step_id, page=page,
                           actions=("click",), assertions=("DOM_VALUE",),
                           dependencies=dependencies, required=True)


class SourceDependencyPreflightTests(unittest.TestCase):
    def setUp(self):
        self.digest = sha256(b"synthetic dependency fixture").hexdigest()
        self.inventory = GuideInventory(2, (
            GuideStep("S1", 1, None, None, "1", "Synthetic edit", (1, 2, 3, 4), None, ()),
        ), (2,), ())
        self.review = SourceReview(
            self.digest, inventory_sha256(self.inventory), "synthetic-reviewer",
            "2026-10-14T15:00:00+00:00", (
                PageReview(1, True, True, True, (CandidateReview("S1", "ACTION"),)),
                PageReview(2, True, True, True,
                           added_actions=(AddedAction("S2", "synthetic visual anchor"),)),
            ),
        )

    def check(self, plans):
        return require_executable_source_review(self.inventory, self.review,
                                                self.digest, plans)

    def test_valid_cross_page_dependency(self):
        self.assertEqual(self.check((plan("S1", 1), plan("S2", 2, ("S1",)))),
                         ("S1", "S2"))

    def test_independent_branch_allowed(self):
        self.assertEqual(self.check((plan("S1", 1), plan("S2", 2))), ("S1", "S2"))

    def test_unknown_source_dependency_rejected(self):
        with self.assertRaises(SourcePreflightError):
            self.check((plan("S1", 1), plan("S2", 2, ("UNREVIEWED",))))

    def test_cycle_and_self_dependency_rejected(self):
        for plans in (
            (plan("S1", 1, ("S2",)), plan("S2", 2, ("S1",))),
            (plan("S1", 1, ("S1",)), plan("S2", 2)),
        ):
            with self.subTest(plans=plans), self.assertRaises(SourcePreflightError):
                self.check(plans)

    def test_duplicate_dependency_rejected(self):
        with self.assertRaises(SourcePreflightError):
            self.check((plan("S1", 1), plan("S2", 2, ("S1", "S1"))))

    def test_malformed_dependencies_rejected(self):
        for deps in ("S1", ("",), ("S1", 7), (["S1"],), None):
            with self.subTest(deps=deps), self.assertRaises(SourcePreflightError):
                self.check((plan("S1", 1), plan("S2", 2, deps)))

    def test_wrong_page_rejected(self):
        with self.assertRaises(SourcePreflightError):
            self.check((plan("S1", 2), plan("S2", 2)))


if __name__ == "__main__":
    unittest.main()
