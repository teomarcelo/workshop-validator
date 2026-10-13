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


if __name__ == "__main__":
    unittest.main()
