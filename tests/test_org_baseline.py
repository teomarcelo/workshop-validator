"""Synthetic-only org baseline tests."""
import json
from pathlib import Path
import tempfile
import unittest

from workshop_validator.org_baseline import OrgBaselineError, load_org_baseline, require_org_baseline

DIGEST = "a" * 64
HOST = "synthetic.my.salesforce.com"

def fixture():
    return {"schema_version": 1, "guide_sha256": DIGEST, "org_hostname": HOST,
            "checks": [
                {"check_id": "identity", "category": "ORG_IDENTITY", "kind": "DOM_TEXT",
                 "role": "heading", "accessible_name": "Org label", "expected": "Synthetic Org"},
                {"check_id": "feature", "category": "PREREQUISITE", "kind": "DOM_VISIBLE",
                 "role": "button", "accessible_name": "Builder", "expected": True}]}

class Target:
    def __init__(self, count=1, visible=True, text="Synthetic Org"):
        self._count, self._visible, self._text = count, visible, text
    def count(self): return self._count
    def is_visible(self): return self._visible
    def inner_text(self): return self._text

class Page:
    url = "https://" + HOST + "/lightning/page/home"
    def __init__(self, targets=None):
        self.targets = targets or {"Org label": Target(), "Builder": Target()}
        self.queries = []
    def get_by_role(self, role, *, name, exact):
        assert exact
        self.queries.append((role, name))
        return self.targets[name]

class OrgBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "baseline.json"
    def load(self, data=None):
        self.path.write_text(json.dumps(fixture() if data is None else data))
        return load_org_baseline(self.path, pdf_digest=DIGEST)
    def test_successful_read_only_gate(self):
        page = Page()
        require_org_baseline(page, self.load())
        self.assertEqual(len(page.queries), 2)
    def test_wrong_pdf_rejected(self):
        self.load()
        with self.assertRaises(OrgBaselineError):
            load_org_baseline(self.path, pdf_digest="b" * 64)
    def test_missing_identity_or_prerequisite_rejected(self):
        for index in (0, 1):
            data = fixture()
            data["checks"] = [data["checks"][index]]
            with self.subTest(index=index), self.assertRaises(OrgBaselineError):
                self.load(data)
    def test_duplicate_check_id_rejected(self):
        data = fixture()
        data["checks"][1]["check_id"] = "identity"
        with self.assertRaises(OrgBaselineError): self.load(data)
    def test_identity_requires_text(self):
        data = fixture()
        data["checks"][0].update(kind="DOM_VISIBLE", expected=True)
        with self.assertRaises(OrgBaselineError): self.load(data)
    def test_invalid_hostname_rejected(self):
        for host in ("evil.example.com", "salesforce.com.evil.org", "*.salesforce.com", "HTTPS://salesforce.com"):
            data = fixture()
            data["org_hostname"] = host
            with self.subTest(host=host), self.assertRaises(OrgBaselineError): self.load(data)
    def test_weak_assertions_rejected(self):
        for value in (False, None, "true", 1):
            data = fixture()
            data["checks"][1]["expected"] = value
            with self.subTest(value=value), self.assertRaises(OrgBaselineError): self.load(data)
    def test_wrong_org_host_rejected_before_ui_queries(self):
        page = Page()
        page.url = "https://other.my.salesforce.com/"
        with self.assertRaises(OrgBaselineError): require_org_baseline(page, self.load())
        self.assertEqual(page.queries, [])
    def test_ambiguous_invisible_or_wrong_text_rejected(self):
        for target in (Target(count=2), Target(visible=False), Target(text="Other Org")):
            page = Page({"Org label": target, "Builder": Target()})
            with self.subTest(target=target), self.assertRaises(OrgBaselineError):
                require_org_baseline(page, self.load())
    def test_browser_error_does_not_leak(self):
        page = Page()
        page.get_by_role = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("SENSITIVE"))
        with self.assertRaisesRegex(OrgBaselineError, "^Baseline observation unavailable$"):
            require_org_baseline(page, self.load())
    def test_invalid_json_rejected(self):
        self.path.write_text("{bad")
        with self.assertRaises(OrgBaselineError):
            load_org_baseline(self.path, pdf_digest=DIGEST)

if __name__ == "__main__":
    unittest.main()
