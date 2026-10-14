import json
import os
import tempfile
import unittest

from workshop_validator.private_runner import load_plan, load_source_review, PrivateRunnerError


class PrivatePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=os.path.join(self.tmp.name,'plan.json')

    def write(self, guide_sha256='a'*64, steps=None):
        data={"schema_version":1,"guide_sha256":guide_sha256,
              "steps":steps if steps is not None else [{
                "step_id":"S001","page":1,"title":"Synthetic save",
                "actions":[{"kind":"click","role":"button","accessible_name":"Save"}],
                "assertions":[{"assertion_id":"visible","kind":"DOM_VISIBLE",
                               "expected":True,"role":"status",
                               "accessible_name":"Save status"}]
              }]}
        with open(self.path,'w') as f:json.dump(data,f)

    def test_parse_pinned_guide_and_source_anchors(self):
        self.write()
        steps=load_plan(self.path,pdf_digest='a'*64,page_count=4)
        self.assertEqual(steps[0].step_id,'S001')
        self.assertEqual(steps[0].actions[0].kind,'click')
        self.assertTrue(steps[0].assertions[0].spec.expected)

    def test_wrong_guide_hash_is_rejected(self):
        self.write()
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='b'*64,page_count=4)

    def test_invalid_page_and_unsafe_action_rejected(self):
        self.write(steps=[{"step_id":"S01","page":99,"title":"Example",
             "actions":[{"kind":"javascript","role":"button","accessible_name":"Save"}],
             "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='a'*64,page_count=4)
        self.write(steps=[{"step_id":"S01","page":1,"title":"Example",
             "actions":[{"kind":"javascript","role":"button","accessible_name":"Save"}],
             "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='a'*64,page_count=4)



    def test_inline_note_placement_uses_private_explicit_anchors(self):
        from workshop_validator.private_runner import build_inline_note_placements
        from workshop_validator.pdf_annotations import ReviewNote
        self.write(steps=[{"step_id":"S001","page":1,
            "title":"Synthetic instruction", "inline_after_y":140,
            "actions":[{"kind":"click","role":"button","accessible_name":"Save"}],
            "assertions":[{"assertion_id":"ok","kind":"DOM_VISIBLE","expected":True,
                           "role":"status","accessible_name":"Status"}]}])
        note=ReviewNote(1,"S001","NEEDS_REVIEW","Synthetic step","Needs testing")
        placements=build_inline_note_placements(self.path,[note])
        self.assertEqual(len(placements),1)
        self.assertEqual(placements[0].insert_after_y,140)
        self.assertIn("behavioral verification",placements[0].summary)

    def test_missing_inline_anchor_is_rejected_not_placed_elsewhere(self):
        from workshop_validator.private_runner import build_inline_note_placements
        from workshop_validator.pdf_annotations import ReviewNote
        self.write()
        note=ReviewNote(1,"S001","NEEDS_REVIEW","Step","Missing evidence")
        with self.assertRaises(PrivateRunnerError):
            build_inline_note_placements(self.path,[note])

    def test_malformed_numeric_source_anchor_is_rejected(self):
        self.write(steps=[{"step_id":"S001","page":1,"title":"Step",
            "inline_after_y":"not-a-coordinate", "actions":[], "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest="a"*64,page_count=4)

if __name__ == "__main__":
    unittest.main()
