import os
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from workshop_validator.browser_worker import (BrowserActionError, UIAction,
    UIAssertion, check_authored_step, perform_actions)
from workshop_validator.evidence import AssertionSpec, StepContext


HTML = '''
<label for="agent">Agent name</label><input id="agent" type="text">
<button id="save">Save</button>
<div role="status" aria-label="Save status" hidden>Not saved</div>
<script>
document.querySelector('#save').addEventListener('click', () => {
 const status=document.querySelector('[role=status]');
 status.hidden = false;
 status.innerText = 'Saved: ' + document.querySelector('#agent').value;
});
</script>
'''


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1", "Browser smoke only (requires Chromium)")
class BrowserWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        executable=os.environ.get('CHROMIUM_PATH') or None
        cls.browser=cls.pw.chromium.launch(headless=True, executable_path=executable)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page=self.browser.new_page()
        self.page.set_content(HTML)
        self.context=StepContext('synthetic-run','synthetic-org','step-1','browser','verifier')
        self.actions=[UIAction('fill','textbox','Agent name','Hello World'),
                      UIAction('click','button','Save')]
        self.assertions=[
            UIAssertion(AssertionSpec('visible','DOM_VISIBLE',True), 'status','Save status'),
            UIAssertion(AssertionSpec('text','DOM_TEXT','Saved: Hello World'), 'status','Save status'),
        ]

    def tearDown(self):
        self.page.close()

    def test_synthetic_pdf_step_to_browser_verification(self):
        # This is a deliberately authored action mapping, not LLM translation.
        import tempfile
        from reportlab.pdfgen import canvas
        from workshop_validator.guide_inventory import extract_inventory
        with tempfile.TemporaryDirectory() as temp:
            path=os.path.join(temp,'synthetic_guide.pdf')
            c=canvas.Canvas(path,pagesize=(612,792),pageCompression=0)
            c.drawString(72,690,'Task 1: Synthetic only')
            c.drawString(90,664,'1. Enter an agent name')
            c.drawString(90,638,'2. Click Save')
            c.showPage()
            c.save()
            guide=extract_inventory(path)
        self.assertEqual([step.number for step in guide.steps],['1','2'])
        self.assertFalse(guide.to_dict()['verified_complete'])
        result=check_authored_step(self.page,self.context,self.actions,self.assertions)
        self.assertEqual(result.status,'PASS')

    def test_synthetic_end_to_end_pass(self):
        result=check_authored_step(self.page,self.context,self.actions,self.assertions)
        self.assertEqual(result.status,'PASS')
        self.assertEqual(len(result.evidence_ids),2)

    def test_independent_gate_rejects_expected_text_mismatch(self):
        self.assertions[1]=UIAssertion(
            AssertionSpec('text','DOM_TEXT','Saved: Different'), 'status','Save status')
        result=check_authored_step(self.page,self.context,self.actions,self.assertions)
        self.assertEqual(result.status,'FUNCTIONAL_FAIL')

    def test_missing_button_fails_without_false_pass(self):
        with self.assertRaises(BrowserActionError):
            perform_actions(self.page,[UIAction('click','button','Nonexistent')],timeout_ms=200)

    def test_ambiguous_evidence_does_not_pass(self):
        self.page.evaluate("const el=document.querySelector('[role=status]');const clone=el.cloneNode(true);clone.hidden=false;el.after(clone)")
        result=check_authored_step(self.page,self.context,self.actions,self.assertions)
        self.assertNotEqual(result.status,'PASS')

    def test_unsupported_action_fails_closed(self):
        with self.assertRaises(BrowserActionError):
            perform_actions(self.page,[UIAction('javascript','button','Save')])

if __name__ == '__main__':
    unittest.main()
