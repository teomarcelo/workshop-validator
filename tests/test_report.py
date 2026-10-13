import os
import tempfile
import unittest
from workshop_validator.report import ReportStep, render_report, write_private_report


class ReportTests(unittest.TestCase):
    def test_blocked_step_keeps_run_incomplete(self):
        html=render_report('demo',[ReportStep('1',1,'Open UI','PASS',('ev1',)),
                                   ReportStep('2',2,'Generate AI','ENV_BLOCKED')],
                           inventory_verified=True)
        self.assertIn('INCOMPLETE',html)
        self.assertNotIn('Result: <strong>VERIFIED</strong>',html)

    def test_only_verified_inventory_and_required_pass_is_green(self):
        item=ReportStep('1',1,'Open UI','PASS',('ev1',))
        self.assertIn('INCOMPLETE',render_report('run',[item]))
        self.assertIn('Result: <strong>VERIFIED</strong>',render_report('run',[item],inventory_verified=True))

    def test_untrusted_strings_are_escaped(self):
        html=render_report('<script>',[ReportStep('1',1,'<img src=x onerror=alert(1)>',
               'NEEDS_REVIEW',(),'<script>alert(1)</script>')])
        self.assertNotIn('<img src=',html)
        self.assertNotIn('Run: <script>',html)
        self.assertIn('&lt;script&gt;',html)

    def test_pass_without_evidence_is_rejected(self):
        with self.assertRaises(ValueError):
            render_report('run',[ReportStep('1',1,'Step','PASS')],inventory_verified=True)

    def test_invalid_status_or_duplicate_step_rejected(self):
        item=ReportStep('1',1,'Step','NOT_RUN')
        with self.assertRaises(ValueError):render_report('run',[item,item])
        with self.assertRaises(ValueError):render_report('run',[ReportStep('2',1,'Step','MADE_UP')])

    def test_private_report_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'report.html')
            write_private_report(path,render_report('run',[ReportStep('1',1,'Step','NOT_RUN')]))
            self.assertEqual(os.stat(path).st_mode & 0o777,0o600)
            with self.assertRaises(FileExistsError):
                write_private_report(path,'changed')

    def test_private_screenshot_link_is_relative_and_escaped(self):
        item=ReportStep('1',1,'Open page','PASS',('evidence-1',),
                        screenshot_href='screenshots/step-1.png')
        html=render_report('run',[item],inventory_verified=True)
        self.assertIn("href='screenshots/step-1.png'",html)
        self.assertIn('View screenshot',html)
        with self.assertRaises(ValueError):
            render_report('run',[ReportStep('1',1,'Step','PASS',('e1',),
                          screenshot_href='https://evil.example/secret')])
        with self.assertRaises(ValueError):
            render_report('run',[ReportStep('1',1,'Step','PASS',('e1',),
                          screenshot_href='screenshots/../../secret.png')])

if __name__=='__main__':unittest.main()
