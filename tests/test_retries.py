import os
import tempfile
import unittest
from datetime import datetime, timezone

from workshop_validator.checkpoints import CheckpointStore, TransitionError
from workshop_validator.dependencies import Step
from workshop_validator.evidence import AssertionSpec
from workshop_validator.retries import RetryQueue, RetryPolicy, parse_retry_after


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path=os.path.join(self.folder.name,'run.sqlite')
        self.store=CheckpointStore(self.path)
        self.store.create_run('demo','synthetic',[
            Step('ai'),Step('dependent',('ai',)),Step('independent')],
            {s:[AssertionSpec('shown','DOM_VISIBLE',True)]
             for s in ('ai','dependent','independent')})
        self.queue=RetryQueue(self.path,RetryPolicy(max_attempts=3,base_seconds=60,max_backoff_seconds=200))

    def block(self):
        attempt=self.store.start_step('demo','ai')
        self.store.block_step('demo','ai',attempt,'ENV_BLOCKED')
        return attempt

    def test_rate_limit_allows_independent_and_holds_dependent(self):
        old=self.block()
        ticket=self.queue.record_429('demo','ai',now=1000)
        self.assertEqual(ticket.retry_at,1060)
        sched=self.store.schedule('demo')
        self.assertEqual(sched.runnable,('independent',))
        self.assertEqual(sched.blocked_by['dependent'],('ai',))
        self.assertEqual(self.queue.wake_due(now=1059),())
        self.assertEqual(self.queue.wake_due(now=1060),(('demo','ai'),))
        self.assertIn('ai',self.store.schedule('demo').runnable)
        self.assertEqual(self.store.status('demo','ai')[0],'NOT_RUN')
        # Old worker version cannot finish after the retry is claimed.
        with self.assertRaises(TransitionError):
            from workshop_validator.evidence import StepContext
            self.store.finish_step('demo','ai',old,
                StepContext('demo','synthetic','ai','executor','verifier'),())

    def test_honors_retry_after_and_exhaustion(self):
        self.block()
        t1=self.queue.record_429('demo','ai',now=1000,retry_after='300')
        self.assertEqual(t1.retry_at,1300)
        self.assertEqual(self.queue.wake_due(now=1299),())
        self.assertEqual(self.queue.wake_due(now=1300),(('demo','ai'),))
        self.block()
        t2=self.queue.record_429('demo','ai',now=1300)
        self.assertEqual(t2.failed_attempts,2)
        self.assertEqual(t2.retry_at,1420)
        self.queue.wake_due(now=1420)
        self.block()
        t3=self.queue.record_429('demo','ai',now=1420)
        self.assertTrue(t3.exhausted)
        self.assertIsNone(t3.retry_at)
        self.assertEqual(self.queue.wake_due(now=999999),())
        self.assertEqual(self.store.status('demo','ai')[0],'ENV_BLOCKED')

    def test_cannot_retry_successful_or_unclassified_step(self):
        with self.assertRaises(ValueError):
            self.queue.record_429('demo','independent',now=100)
        self.block()
        with self.assertRaises(ValueError):
            self.queue.record_429('demo','ai',now=float('nan'))

    def test_header_parsing(self):
        now=datetime(2026,10,13,12,0,tzinfo=timezone.utc).timestamp()
        self.assertEqual(parse_retry_after('120',now=now),120)
        self.assertEqual(parse_retry_after('Tue, 13 Oct 2026 12:02:00 GMT',now=now),120)
        self.assertEqual(parse_retry_after('garbage',now=now),None)


if __name__=='__main__':
    unittest.main()
