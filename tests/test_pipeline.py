import os
import tempfile
import unittest

from workshop_validator.browser_worker import UIAction, UIAssertion
from workshop_validator.evidence import AssertionSpec
from workshop_validator.pipeline import (PlannedStep, validate_plan,
                                          RateLimitDetected, PlanMismatch)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


class PlanShapeTests(unittest.TestCase):
    def test_missing_source_actions_fails_before_database_is_created(self):
        s = PlannedStep("one", 1, "A",
            (), (UIAssertion(AssertionSpec("visible", "DOM_VISIBLE", True), "button", "Save"),))
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'results.sqlite')
            with self.assertRaises(PlanMismatch):
                validate_plan(None,path,'run','synthetic',(s,),source_action_ids=("one","two"),source_reconciled=True)
            self.assertFalse(os.path.exists(path))

    def test_reconciled_flag_requires_source_ids(self):
        s = PlannedStep("one",1,"A",(),
            (UIAssertion(AssertionSpec("visible","DOM_VISIBLE",True),"button","Save"),))
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(PlanMismatch):
                validate_plan(None,os.path.join(d,'db'),'run','org',(s,),source_reconciled=True)


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
                     "Chromium smoke requires RUN_BROWSER_SMOKE=1")
class PipelineBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page=self.browser.new_page()
        self.page.set_content('''
          <label for="agent">Agent name</label><input id="agent">
          <button id="save" onclick="document.getElementById('result').innerText='Saved: '+document.getElementById('agent').value">Save</button>
          <div role="status" aria-label="Save status" id="result">Not saved</div>
        ''')
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.addCleanup(self.page.close)

    def plan(self):
        return (
            PlannedStep('P001N001',1,'Enter agent name',
                (UIAction('fill','textbox','Agent name','Hello World'),),
                (UIAssertion(AssertionSpec('textbox','DOM_VISIBLE',True),'textbox','Agent name'),)),
            PlannedStep('P001N002',1,'Click Save',
                (UIAction('click','button','Save'),),
                (UIAssertion(AssertionSpec('saved','DOM_TEXT','Saved: Hello World'),'status','Save status'),),
                ('P001N001',)),
        )

    def test_full_synthetic_exercise_pipeline_reports_verified(self):
        result=validate_plan(self.page,os.path.join(self.folder.name,'db'),
                             'synthetic-run','synthetic-org',self.plan(),
                             source_reconciled=True,source_action_ids=('P001N001','P001N002'))
        self.assertTrue(result.fully_verified)
        self.assertEqual(result.statuses,{'P001N001':'PASS','P001N002':'PASS'})
        self.assertIn('Result: <strong>VERIFIED</strong>',result.html)
        self.assertIn('P001N002',result.html)

    def test_missing_source_review_never_reports_verified(self):
        result=validate_plan(self.page,os.path.join(self.folder.name,'db'),
                             'synthetic-run','synthetic-org',self.plan())
        self.assertFalse(result.fully_verified)
        self.assertIn('Result: <strong>INCOMPLETE</strong>',result.html)

    def test_observed_http429_holds_dependents_continues_independent(self):
        base=self.plan()[0]
        blocked=PlannedStep('blocked',1,'AI generation',(),
            (UIAssertion(AssertionSpec('text','DOM_TEXT','success'),'status','Save status'),),
            ('P001N001',))
        dependent=PlannedStep('dependent',2,'Follow up',(),
            (UIAssertion(AssertionSpec('text','DOM_TEXT','success'),'status','Save status'),),
            ('blocked',))
        independent=PlannedStep('independent',2,'Open page',(),
            (UIAssertion(AssertionSpec('visible','DOM_VISIBLE',True),'button','Save'),))
        from workshop_validator.browser_worker import perform_actions,collect_observations
        def executor(page, step, context):
            if step.step_id=='blocked':
                raise RateLimitDetected('429')
            perform_actions(page,step.actions)
            return collect_observations(page,context,step.assertions)
        steps=(base,blocked,dependent,independent)
        result=validate_plan(self.page,os.path.join(self.folder.name,'db'),
                             'synthetic-run','synthetic-org',steps,
                             source_action_ids=tuple(s.step_id for s in steps),
                             source_reconciled=True,execute_step=executor)
        self.assertEqual(result.statuses['blocked'],'ENV_BLOCKED')
        self.assertEqual(result.statuses['dependent'],'NOT_RUN')
        self.assertEqual(result.statuses['independent'],'PASS')
        self.assertFalse(result.fully_verified)

    def test_bad_selector_is_never_passed(self):
        missing=PlannedStep('missing',1,'Click an absent button',
            (UIAction('click','button','Does Not Exist'),),
            (UIAssertion(AssertionSpec('visible','DOM_VISIBLE',True),'button','Save'),))
        result=validate_plan(self.page,os.path.join(self.folder.name,'db'),
                             'synthetic-run','synthetic-org',(missing,),
                             source_reconciled=True,source_action_ids=('missing',))
        self.assertEqual(result.statuses['missing'],'NEEDS_REVIEW')
        self.assertFalse(result.fully_verified)


if __name__ == '__main__':
    unittest.main()
