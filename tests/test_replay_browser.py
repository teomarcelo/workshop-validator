"""Synthetic Chromium execute/omit/fault replay; no real org or guide."""
from dataclasses import replace
import os
import unittest

from workshop_validator.browser_worker import UIAction, UIAssertion, collect_observations, perform_actions
from workshop_validator.evidence import AssertionSpec, FUNCTIONAL_FAIL, PASS, StepContext, verify_step
from workshop_validator.replay_provenance import ReplaySample, assess_replay

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

HTML = '''<label for="generated">Generated type</label>
<textarea id="generated">list[object]</textarea>
<button onclick="document.getElementById('out').textContent='Saved: '+document.getElementById('generated').value">Save</button>
<div id="out" role="status" aria-label="Save result">Waiting</div>'''


@unittest.skipUnless(sync_playwright is not None and os.environ.get('RUN_BROWSER_SMOKE') == '1',
                     'Chromium synthetic test only')
class ReplayBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True,
            executable_path=os.environ.get('CHROMIUM_PATH') or None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def replay(self, wrong_literal=False, weak_oracle=False):
        target = UIAssertion(AssertionSpec('target', 'DOM_VISIBLE', True) if weak_oracle
                             else AssertionSpec('target', 'DOM_VALUE', 'list[object]'),
                             'textbox', 'Generated type')
        downstream = UIAssertion(AssertionSpec('saved', 'DOM_TEXT', 'Saved: list[object]'),
                                 'status', 'Save result')
        phases = {}
        def fresh():
            page = self.browser.new_page()
            self.addCleanup(page.close)
            page.set_content(HTML)
            return page
        def observe(page, phase, run, check):
            context = StepContext(run, 'synthetic-org', 'edit', 'browser', 'verifier')
            evidence = [replace(o, evidence_id=o.evidence_id + ':' + phase)
                        for o in collect_observations(page, context, (check,))]
            phases[phase] = verify_step(context, (check.spec,), evidence)
        def fill(page, value):
            perform_actions(page, (UIAction('fill', 'textbox', 'Generated type', value),))
        def save(page):
            perform_actions(page, (UIAction('click', 'button', 'Save'),))

        executed = fresh()
        observe(executed, 'executed_before', 'execute', target)
        fill(executed, 'wrong' if wrong_literal else 'list[object]')
        observe(executed, 'executed_after', 'execute', target)
        save(executed)
        observe(executed, 'executed_downstream', 'execute', downstream)

        omitted = fresh()
        observe(omitted, 'skipped_before', 'omit', target)
        save(omitted)  # only the authored fill is omitted
        observe(omitted, 'skipped_downstream', 'omit', downstream)

        broken = fresh()
        fill(broken, 'wrong')
        observe(broken, 'negative_control', 'fault', target)

        samples = [ReplaySample(
            phase=phase,
            run_id='execute' if phase.startswith('executed') else
                   'omit' if phase.startswith('skipped') else 'fault',
            org_id='synthetic-org', guide_sha256='a'*64,
            baseline_id='synthetic-resettable-form',
            reset_id='reset-execute' if phase.startswith('executed') else
                     'reset-omit' if phase.startswith('skipped') else 'reset-fault',
            step_id='edit', oracle_id='downstream' if 'downstream' in phase else 'target',
            verification=verdict, reset_verified=True, prerequisites_verified=True,
        ) for phase, verdict in phases.items()]
        return phases, assess_replay(samples)

    def test_sensitive_replay_is_instructor_review_only(self):
        phases, finding = self.replay()
        self.assertEqual(phases['negative_control'].status, FUNCTIONAL_FAIL)
        self.assertEqual(finding.classification, 'REDUNDANCY_CANDIDATE')
        self.assertTrue(finding.requires_instructor_review)

    def test_broken_value_that_still_passes_blocks_removal(self):
        phases, finding = self.replay(weak_oracle=True)
        self.assertEqual(phases['negative_control'].status, PASS)
        self.assertEqual(finding.classification, 'TEST_ORACLE_INSENSITIVE')
        self.assertFalse(finding.propose_guide_removal)

    def test_wrong_literal_action_fails_target_and_downstream(self):
        phases, finding = self.replay(wrong_literal=True)
        self.assertEqual(phases['executed_after'].status, FUNCTIONAL_FAIL)
        self.assertEqual(phases['executed_downstream'].status, FUNCTIONAL_FAIL)
        self.assertFalse(finding.propose_guide_removal)


if __name__ == '__main__':
    unittest.main()
