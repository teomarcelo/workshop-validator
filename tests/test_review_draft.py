import os
import tempfile
import unittest
from reportlab.pdfgen import canvas

from workshop_validator.review_draft import make_draft
from workshop_validator.guide_inventory import extract_inventory
from workshop_validator.reconciliation import (
    CandidateReview, PageReview, SourceReview, reconcile_inventory
)


class ReviewDraftTests(unittest.TestCase):
    def test_every_page_and_candidate_explicitly_unresolved(self):
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'synthetic.pdf')
            doc=canvas.Canvas(path,pagesize=(612,792))
            doc.drawString(74,700,'Task 1: Sample')
            doc.drawString(90,650,'1. Click Save')
            doc.showPage()
            doc.drawString(74,700,'A second page with only a picture')
            doc.showPage()
            doc.save()
            draft=make_draft(path)
            self.assertEqual(len(draft['pages']),2)
            self.assertFalse(draft['pages'][0]['text_checked'])
            self.assertFalse(draft['pages'][1]['no_other_actions'])
            self.assertEqual(draft['pages'][0]['candidates'][0]['disposition'],'UNRESOLVED')
            inv=extract_inventory(path)
            review=SourceReview(draft['pdf_sha256'],draft['inventory_sha256'],
                                draft['reviewer_id'],draft['reviewed_at'],
                                tuple(PageReview(page=p['page'],
                                  text_checked=p['text_checked'],
                                  visuals_checked=p['visuals_checked'],
                                  no_other_actions=p['no_other_actions'],
                                  candidates=tuple(CandidateReview(**c) for c in p['candidates']),
                                  unresolved=tuple(p['unresolved']))
                                  for p in draft['pages']),
                                tuple(draft['reviewed_inventory_warnings']))
            result=reconcile_inventory(inv,review,draft['pdf_sha256'])
            self.assertFalse(result.complete)
            self.assertIn('missing_reviewer',result.issues)


if __name__=='__main__':
    unittest.main()
