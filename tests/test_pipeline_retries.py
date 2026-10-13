import os
import tempfile
import unittest

from workshop_validator.browser_worker import UIAssertion
from workshop_validator.evidence import AssertionSpec, Observation
from workshop_validator.pipeline import PlannedStep, validate_plan, RateLimitDetected
from workshop_validator.retries import RetryPolicy, RetryQueue


class PipelineRetryIntegrationTests(unittest.TestCase):
    def fixture(self):
        spec=lambda name: (UIAssertion(AssertionSpec('ready','DOM_VISIBLE',True),
                                      'status',name),)
        return (
            PlannedStep('setup',1,'Setup',(),spec('ready')),
            PlannedStep('rate_limited',1,'AI generate',(),spec('ready'),('setup',)),
            PlannedStep('after_ai',2,'Downstream',(),spec('ready'),('rate_limited',)),
            PlannedStep('independent',2,'Unrelated',(),spec('ready')),
        )

    def test_retries_429_after_cooldown_while_independent_branch_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            state=os.path.join(folder,'run.sqlite')
            clock=[1000.]
            sleeps=[]
            called=[]
            attempts={'rate_limited':0}

            def execute(page, step, context):
                called.append(step.step_id)
                if step.step_id=='rate_limited':
                    attempts['rate_limited'] += 1
                    if attempts['rate_limited']==1:
                        raise RateLimitDetected(retry_after='120')
                return [Observation(f'evidence-{step.step_id}-{len(called)}',
                    'ready','DOM_VISIBLE',True,
                    context.run_id, context.org_id,context.step_id,context.executor_id)]

            def sleep(seconds):
                sleeps.append(seconds)
                clock[0] += seconds
            steps=self.fixture()
            result=validate_plan(None,state,'demo','synthetic',steps,
                source_action_ids=tuple(s.step_id for s in steps),
                source_reconciled=True,
                execute_step=execute,
                retry_policy=RetryPolicy(max_attempts=3,base_seconds=60,max_backoff_seconds=3600),
                max_wait_seconds=600, clock=lambda:clock[0],sleeper=sleep)
            self.assertTrue(result.fully_verified)
            self.assertEqual(result.statuses['rate_limited'],'PASS')
            self.assertEqual(result.statuses['after_ai'],'PASS')
            self.assertEqual(attempts['rate_limited'],2)
            self.assertEqual(sleeps,[120])
            self.assertLess(called.index('independent'),called.index('after_ai'))
            self.assertFalse(RetryQueue(state).tickets('demo')[0].exhausted)

    def test_short_wait_budget_leaves_blocked_run_incomplete(self):
        with tempfile.TemporaryDirectory() as folder:
            state=os.path.join(folder,'run.sqlite')
            steps=self.fixture()
            def execute(page,step,context):
                if step.step_id=='rate_limited':
                    raise RateLimitDetected(retry_after='7200')
                return [Observation('ev-'+step.step_id,'ready','DOM_VISIBLE',True,
                    context.run_id,context.org_id,context.step_id,context.executor_id)]
            result=validate_plan(None,state,'demo','synthetic',steps,
                source_action_ids=tuple(s.step_id for s in steps),
                source_reconciled=True,execute_step=execute,
                retry_policy=RetryPolicy(max_attempts=3),
                max_wait_seconds=20, clock=lambda:1000.,sleeper=lambda secs:self.fail('unexpected sleep'))
            self.assertFalse(result.fully_verified)
            self.assertEqual(result.statuses['rate_limited'],'ENV_BLOCKED')
            self.assertEqual(result.statuses['independent'],'PASS')
            self.assertEqual(result.statuses['after_ai'],'NOT_RUN')

if __name__=='__main__':
    unittest.main()
