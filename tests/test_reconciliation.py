import dataclasses
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.reconciliation import (
    AddedAction, CandidateReview, PageReview, SourceReview,
    file_sha256, inventory_sha256, reconcile_inventory,
)


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.inventory = GuideInventory(2, (
            GuideStep("P001N001", 1, None, None, "1", "Synthetic action", (90, 20, 200, 40), None, ()),
            GuideStep("P002N001", 2, None, None, "1", "Synthetic table action", (90, 20, 200, 40), None,
                      ("table_or_visual_context_required",)),
        ), (), ("Page 2: image-only content needs visual review",))
        self.pdf_hash = sha256(b"synthetic PDF bytes").hexdigest()
        self.pages = (
            PageReview(1, True, True, True, (CandidateReview("P001N001", "ACTION"),)),
            PageReview(2, True, True, True, (
                CandidateReview("P002N001", "ACTION", ("table_or_visual_context_required",)),
            )),
        )
        self.review = SourceReview(self.pdf_hash, inventory_sha256(self.inventory),
                                   "reviewer-1", "2026-10-13T20:00:00+00:00",
                                   self.pages, self.inventory.warnings)

    def result(self, review=None, inventory=None, pdf_hash=None):
        return reconcile_inventory(inventory or self.inventory, review or self.review,
                                   pdf_hash or self.pdf_hash)

    def test_complete_only_with_full_attestation(self):
        result = self.result()
        self.assertTrue(result.complete)
        self.assertEqual(result.action_ids, ("P001N001", "P002N001"))
        self.assertEqual(result.reviewed_pages, 2)
        self.assertFalse(result.issues)

    def test_pdf_digest_change_invalidates_review(self):
        result = self.result(pdf_hash=sha256(b"changed PDF").hexdigest())
        self.assertFalse(result.complete)
        self.assertIn("pdf_digest_mismatch", result.issues)
        self.assertFalse(result.action_ids)

    def test_inventory_edit_invalidates_review(self):
        changed = dataclasses.replace(self.inventory, page_count=3)
        self.assertIn("inventory_digest_mismatch", self.result(inventory=changed).issues)

    def test_missing_page_is_not_complete(self):
        review = dataclasses.replace(self.review, pages=self.pages[:1])
        self.assertIn("missing_page_reviews", self.result(review).issues)

    def test_duplicate_page_is_rejected(self):
        review = dataclasses.replace(self.review, pages=self.pages + self.pages[:1])
        self.assertIn("page_1:duplicate_review", self.result(review).issues)

    def test_candidate_must_be_explicitly_reviewed(self):
        p1 = dataclasses.replace(self.pages[0], candidates=())
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertIn("page_1:missing_candidates", self.result(review).issues)

    def test_duplicate_candidate_is_rejected(self):
        p1 = dataclasses.replace(self.pages[0], candidates=self.pages[0].candidates * 2)
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertIn("page_1:duplicate_candidate", self.result(review).issues)

    def test_unreviewed_candidate_warning_is_rejected(self):
        p2 = dataclasses.replace(self.pages[1], candidates=(CandidateReview("P002N001", "ACTION"),))
        review = dataclasses.replace(self.review, pages=(self.pages[0], p2))
        self.assertIn("page_2:unreviewed_candidate_warnings", self.result(review).issues)

    def test_unreviewed_global_warning_is_rejected(self):
        review = dataclasses.replace(self.review, reviewed_inventory_warnings=())
        self.assertIn("unreviewed_inventory_warnings", self.result(review).issues)

    def test_unresolved_candidate_blocks_completion(self):
        p1 = dataclasses.replace(self.pages[0], candidates=(CandidateReview("P001N001", "UNRESOLVED"),))
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertIn("page_1:unresolved_candidate", self.result(review).issues)

    def test_exclusion_requires_reason(self):
        p1 = dataclasses.replace(self.pages[0], candidates=(CandidateReview("P001N001", "NOT_ACTION"),))
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertIn("page_1:missing_exclusion_rationale", self.result(review).issues)
        p1 = dataclasses.replace(p1, candidates=(CandidateReview("P001N001", "NOT_ACTION", rationale="Heading only"),))
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertTrue(self.result(review).complete)
        self.assertEqual(self.result(review).action_ids, ("P002N001",))

    def test_uninspected_visuals_and_unlisted_actions_block(self):
        p2 = dataclasses.replace(self.pages[1], visuals_checked=False, no_other_actions=False)
        review = dataclasses.replace(self.review, pages=(self.pages[0], p2))
        self.assertIn("page_2:inspection_incomplete", self.result(review).issues)
        self.assertIn("page_2:unlisted_actions_possible", self.result(review).issues)

    def test_added_action_requires_anchor_and_unique_id(self):
        p1 = dataclasses.replace(self.pages[0], added_actions=(AddedAction("P001A001", "figure A"),))
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertTrue(self.result(review).complete)
        self.assertIn("P001A001", self.result(review).action_ids)
        p1 = dataclasses.replace(p1, added_actions=(AddedAction("P001N001", "figure A"),))
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertIn("page_1:duplicate_action_id", self.result(review).issues)
        p1 = dataclasses.replace(p1, added_actions=(AddedAction("P001A001", ""),))
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1]))
        self.assertIn("page_1:invalid_added_action", self.result(review).issues)

    def test_empty_page_still_requires_explicit_review(self):
        inventory = GuideInventory(1, (), (1,), ())
        review = SourceReview(self.pdf_hash, inventory_sha256(inventory), "reviewer-1",
                              "2026-10-13T20:00:00+00:00", (PageReview(1, True, True, True),))
        self.assertTrue(self.result(review, inventory).complete)
        self.assertEqual(self.result(review, inventory).action_ids, ())

    def test_review_metadata_is_required(self):
        review = dataclasses.replace(self.review, reviewer_id="", reviewed_at="2026-10-13T20:00:00")
        self.assertIn("missing_reviewer", self.result(review).issues)
        self.assertIn("invalid_review_timestamp", self.result(review).issues)

    def test_unknown_page_or_candidate_is_rejected(self):
        p1 = dataclasses.replace(self.pages[0], candidates=(CandidateReview("OTHER", "ACTION"),))
        review = dataclasses.replace(self.review, pages=(p1, self.pages[1], PageReview(3, True, True, True)))
        self.assertIn("unknown_page", self.result(review).issues)
        self.assertIn("page_1:unknown_candidate", self.result(review).issues)

    def test_file_digest_uses_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synthetic.pdf"
            path.write_bytes(b"synthetic PDF bytes")
            self.assertEqual(file_sha256(path), self.pdf_hash)


if __name__ == "__main__":
    unittest.main()
