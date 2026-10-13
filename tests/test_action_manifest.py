import dataclasses
from hashlib import sha256
import unittest

from workshop_validator.action_manifest import (
    ActionManifest, AuthoredStep, ManifestError, compile_action_manifest,
)
from workshop_validator.browser_worker import UIAction, UIAssertion
from workshop_validator.evidence import AssertionSpec
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.reconciliation import (
    AddedAction, CandidateReview, PageReview, SourceReview, inventory_sha256,
)


class ActionManifestTests(unittest.TestCase):
    def setUp(self):
        self.inventory = GuideInventory(2, (
            GuideStep('P001N001', 1, None, None, '1', 'Synthetic fill',
                      (90, 20, 200, 40), None, ()),
            GuideStep('P002N001', 2, None, None, '1', 'Synthetic save',
                      (90, 20, 200, 40), None, ()),
        ), (), ())
        self.pdf_hash = sha256(b'synthetic-only').hexdigest()
        self.pages = (
            PageReview(1, True, True, True,
                       (CandidateReview('P001N001', 'ACTION'),)),
            PageReview(2, True, True, True,
                       (CandidateReview('P002N001', 'ACTION'),)),
        )
        self.review = SourceReview(self.pdf_hash, inventory_sha256(self.inventory),
                                   'synthetic-reviewer', '2026-10-13T20:00:00+00:00',
                                   self.pages)
        self.authored = (
            AuthoredStep('P001N001', 1, 'page1/region1', (),
                         (UIAction('fill', 'textbox', 'Agent name', 'Demo'),),
                         (UIAssertion(AssertionSpec('filled', 'DOM_TEXT', 'Demo'),
                                      'textbox', 'Agent name'),)),
            AuthoredStep('P002N001', 2, 'page2/region1', ('P001N001',),
                         (UIAction('click', 'button', 'Save'),),
                         (UIAssertion(AssertionSpec('visible', 'DOM_VISIBLE', True),
                                      'status', 'Save status'),)),
        )

    def compile(self, **changes):
        return compile_action_manifest(changes.get('inventory', self.inventory),
                                       changes.get('review', self.review),
                                       changes.get('pdf_hash', self.pdf_hash),
                                       changes.get('authored', self.authored))

    def test_compiles_reconciled_manifest_and_checkpoint_inputs(self):
        plan = self.compile()
        self.assertIsInstance(plan, ActionManifest)
        self.assertEqual([s.step_id for s in plan.steps], ['P001N001', 'P002N001'])
        self.assertEqual(plan.steps[1].dependency_ids, ('P001N001',))
        self.assertTrue(all(s.required for s in plan.steps))
        steps, specs = plan.checkpoint_inputs()
        self.assertEqual(steps, plan.steps)
        self.assertEqual(specs['P001N001'][0].expected, 'Demo')
        self.assertEqual(plan.pdf_sha256, self.pdf_hash)

    def test_rejects_missing_and_extra_authored_actions(self):
        for authored in (self.authored[:1], self.authored +
                        (dataclasses.replace(self.authored[0], step_id='EXTRA'),)):
            with self.subTest(authored=authored), self.assertRaisesRegex(ManifestError, 'one_to_one'):
                self.compile(authored=authored)

    def test_rejects_duplicate_authored_action(self):
        with self.assertRaisesRegex(ManifestError, 'one_to_one'):
            self.compile(authored=(self.authored[0], self.authored[0]))

    def test_rejects_wrong_page_or_missing_anchor(self):
        for item in (dataclasses.replace(self.authored[0], page=2),
                     dataclasses.replace(self.authored[0], source_ref='')):
            with self.subTest(item=item), self.assertRaisesRegex(ManifestError, 'source_page'):
                self.compile(authored=(item, self.authored[1]))

    def test_rejects_incomplete_source_review(self):
        review = dataclasses.replace(self.review, pages=self.pages[:1])
        with self.assertRaisesRegex(ManifestError, 'source_reconciliation'):
            self.compile(review=review)
        with self.assertRaisesRegex(ManifestError, 'source_reconciliation'):
            self.compile(pdf_hash=sha256(b'changed').hexdigest())

    def test_rejects_unmapped_added_visual_action(self):
        page1 = dataclasses.replace(self.pages[0],
                                    added_actions=(AddedAction('P001A001', 'diagram1'),))
        review = dataclasses.replace(self.review, pages=(page1, self.pages[1]))
        with self.assertRaisesRegex(ManifestError, 'one_to_one'):
            self.compile(review=review)
        added = dataclasses.replace(self.authored[0], step_id='P001A001',
                                    source_ref='diagram1')
        plan = self.compile(review=review, authored=self.authored + (added,))
        self.assertEqual(len(plan.steps), 3)

    def test_added_action_anchor_must_match_review(self):
        page1 = dataclasses.replace(self.pages[0],
                                    added_actions=(AddedAction('P001A001', 'diagram1'),))
        review = dataclasses.replace(self.review, pages=(page1, self.pages[1]))
        added = dataclasses.replace(self.authored[0], step_id='P001A001',
                                    source_ref='different-diagram')
        with self.assertRaisesRegex(ManifestError, 'source_page'):
            self.compile(review=review, authored=self.authored + (added,))

    def test_rejects_dependency_cycle_and_unknown_prerequisite(self):
        for deps in (('P002N001',), ('MISSING',), ('P001N001',)):
            first = dataclasses.replace(self.authored[0], dependency_ids=deps)
            with self.subTest(deps=deps), self.assertRaisesRegex(ManifestError, 'dependencies'):
                self.compile(authored=(first, self.authored[1]))

    def test_rejects_unmapped_or_unverifiable_step(self):
        for item in (dataclasses.replace(self.authored[0], actions=()),
                     dataclasses.replace(self.authored[0], assertions=())):
            with self.subTest(item=item), self.assertRaisesRegex(ManifestError, 'missing_browser'):
                self.compile(authored=(item, self.authored[1]))

    def test_rejects_unsupported_or_unsafe_actions(self):
        for action in (UIAction('navigate', 'link', 'Next'),
                       UIAction('click', 'button', 'Save', 'unexpected'),
                       UIAction('fill', 'textbox', 'Agent name', None),
                       UIAction('click', '', 'Save')):
            first = dataclasses.replace(self.authored[0], actions=(action,))
            with self.subTest(action=action), self.assertRaises(ManifestError):
                self.compile(authored=(first, self.authored[1]))

    def test_rejects_bad_assertion_specs(self):
        specs = (AssertionSpec('bad', 'API_STATUS', 200),
                 AssertionSpec('bad', 'DOM_VISIBLE', 'True'),
                 AssertionSpec('bad', 'DOM_VISIBLE', True, 'contains'),
                 AssertionSpec('', 'DOM_TEXT', 'x'))
        for spec in specs:
            item = UIAssertion(spec, 'status', 'Save status')
            first = dataclasses.replace(self.authored[0], assertions=(item,))
            with self.subTest(spec=spec), self.assertRaises(ManifestError):
                self.compile(authored=(first, self.authored[1]))

    def test_rejects_duplicate_assertion_ids(self):
        item = self.authored[0].assertions[0]
        first = dataclasses.replace(self.authored[0], assertions=(item, item))
        with self.assertRaisesRegex(ManifestError, 'duplicate'):
            self.compile(authored=(first, self.authored[1]))

    def test_excluded_candidates_are_not_executable(self):
        page1 = dataclasses.replace(self.pages[0], candidates=(
            CandidateReview('P001N001', 'NOT_ACTION', rationale='Synthetic heading'),))
        review = dataclasses.replace(self.review, pages=(page1, self.pages[1]))
        with self.assertRaisesRegex(ManifestError, 'dependencies'):
            self.compile(review=review, authored=self.authored[1:])
        only = dataclasses.replace(self.authored[1], dependency_ids=())
        plan = self.compile(review=review, authored=(only,))
        self.assertEqual([s.step_id for s in plan.steps], ['P002N001'])


if __name__ == '__main__':
    unittest.main()
