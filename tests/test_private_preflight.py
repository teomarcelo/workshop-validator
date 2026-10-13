"""Synthetic-only preflight tests; no org credentials or private guides."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile
import unittest

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.reconciliation import (
    AddedAction, CandidateReview, PageReview, SourceReview, inventory_sha256,
)
from workshop_validator.private_preflight import SourcePreflightError, require_executable_source_review
from workshop_validator.private_runner import PrivateRunnerError, run_private


def plan(step_id, page, actions=("click",), assertions=("DOM_VISIBLE",)):
    return SimpleNamespace(step_id=step_id, page=page, actions=actions, assertions=assertions)


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.digest = sha256(b"synthetic fixture").hexdigest()
        self.inventory = GuideInventory(2, (
            GuideStep("P001N001", 1, None, None, "1", "Synthetic action", (1, 2, 3, 4), None, ()),
        ), (2,), ())
        self.pages = (
            PageReview(1, True, True, True, (CandidateReview("P001N001", "ACTION"),)),
            PageReview(2, True, True, True, added_actions=(AddedAction("P002A001", "diagram"),)),
        )
        self.review = SourceReview(self.digest, inventory_sha256(self.inventory),
            "synthetic-reviewer", "2026-10-13T22:00:00+00:00", self.pages)
        self.plans = (plan("P001N001", 1), plan("P002A001", 2))

    def check(self, review=None, plans=None, digest=None):
        return require_executable_source_review(self.inventory,
            self.review if review is None else review,
            self.digest if digest is None else digest,
            self.plans if plans is None else plans)

    def test_complete_page_bound_mapping(self):
        self.assertEqual(self.check(), ("P001N001", "P002A001"))

    def test_missing_review_rejected(self):
        with self.assertRaises(SourcePreflightError):
            require_executable_source_review(self.inventory, None, self.digest, self.plans)

    def test_incomplete_page_review_rejected(self):
        with self.assertRaises(SourcePreflightError):
            self.check(review=replace(self.review, pages=self.pages[:1]))

    def test_changed_guide_digest_rejected(self):
        with self.assertRaises(SourcePreflightError):
            self.check(digest="b" * 64)

    def test_unresolved_review_rejected(self):
        page = replace(self.pages[0], candidates=(CandidateReview("P001N001", "UNRESOLVED"),))
        with self.assertRaises(SourcePreflightError):
            self.check(review=replace(self.review, pages=(page, self.pages[1])))

    def test_missing_or_duplicate_plan_rejected(self):
        for plans in (self.plans[:1], (self.plans[0], self.plans[0])):
            with self.subTest(plans=plans), self.assertRaises(SourcePreflightError):
                self.check(plans=plans)

    def test_unreviewed_extra_plan_rejected(self):
        with self.assertRaises(SourcePreflightError):
            self.check(plans=self.plans + (plan("EXTRA", 1),))

    def test_wrong_page_for_extracted_or_added_action_rejected(self):
        for plans in ((plan("P001N001", 2), self.plans[1]),
                      (self.plans[0], plan("P002A001", 1))):
            with self.subTest(plans=plans), self.assertRaises(SourcePreflightError):
                self.check(plans=plans)

    def test_missing_action_or_assertion_rejected(self):
        for plans in ((plan("P001N001", 1, actions=()), self.plans[1]),
                      (plan("P001N001", 1, assertions=()), self.plans[1])):
            with self.subTest(plans=plans), self.assertRaises(SourcePreflightError):
                self.check(plans=plans)

    def test_no_reviewed_actions_rejected(self):
        inv = GuideInventory(1, (), (1,), ())
        review = SourceReview(self.digest, inventory_sha256(inv), "synthetic-reviewer",
            "2026-10-13T22:00:00+00:00", (PageReview(1, True, True, True),))
        with self.assertRaises(SourcePreflightError):
            require_executable_source_review(inv, review, self.digest, ())

    def test_runner_blocks_missing_review_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "no-output"
            with (patch("workshop_validator.private_runner.file_sha256", return_value=self.digest),
                  patch("workshop_validator.private_runner.extract_inventory", return_value=self.inventory),
                  patch("workshop_validator.private_runner.load_plan", return_value=self.plans)):
                with self.assertRaisesRegex(PrivateRunnerError, "page review"):
                    run_private("synthetic.pdf", "plan.json", out)
            self.assertFalse(out.exists())

    def test_runner_blocks_incomplete_review_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "no-output"
            with (patch("workshop_validator.private_runner.file_sha256", return_value=self.digest),
                  patch("workshop_validator.private_runner.extract_inventory", return_value=self.inventory),
                  patch("workshop_validator.private_runner.load_plan", return_value=self.plans),
                  patch("workshop_validator.private_runner.load_source_review",
                        return_value=replace(self.review, pages=self.pages[:1]))):
                with self.assertRaises(PrivateRunnerError):
                    run_private("synthetic.pdf", "plan.json", out, source_review_path="review.json")
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
