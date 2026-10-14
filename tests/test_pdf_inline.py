import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from workshop_validator.pdf_inline import InlineNote, annotate_pdf_inline

try:
    import fitz
except ImportError:
    fitz = None

@unittest.skipUnless(fitz is not None, "PyMuPDF required")
class InlinePDFTests(unittest.TestCase):
    def synthetic(self,folder):
        source=Path(folder)/"synthetic.pdf"
        doc=fitz.open()
        page=doc.new_page(width=612,height=792)
        page.insert_text((90,105),"1. First synthetic instruction")
        page.insert_text((90,200),"2. Second synthetic instruction")
        doc.new_page(width=612,height=792).insert_text((90,120),"Unchanged page")
        doc.save(source)
        doc.close()
        return source

    def test_note_is_inline_between_steps_without_covering_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source=self.synthetic(folder)
            target=Path(folder)/"inline.pdf"
            result=annotate_pdf_inline(source,[
                InlineNote(1,"Step 1",125,"Check current generated type",
                           "Test saving without editing it")],target)
            self.assertEqual(result["total_pages"],3)
            self.assertEqual(result["affected_source_pages"],[1])
            with fitz.open(source) as a,fitz.open(target) as b:
                self.assertGreater(b[1].rect.height,a[0].rect.height)
                text=b[1].get_text(sort=True)
                self.assertLess(text.index("First synthetic"),text.index("INSTRUCTOR CHECK"))
                self.assertLess(text.index("INSTRUCTOR CHECK"),text.index("Second synthetic"))
                self.assertIn("Step 1",b[0].get_text())
                self.assertEqual(hashlib.sha256(a[1].get_pixmap().samples).digest(),
                                 hashlib.sha256(b[2].get_pixmap().samples).digest())
            self.assertEqual(os.stat(target).st_mode&0o777,0o600)

    def test_multiple_notes_and_no_notes(self):
        with tempfile.TemporaryDirectory() as folder:
            source=self.synthetic(folder)
            target=Path(folder)/"multiple.pdf"
            result=annotate_pdf_inline(source,[
                InlineNote(1,"One",125,"Finding one","Retest"),
                InlineNote(1,"Two",225,"Finding two","Retest downstream")],target)
            self.assertEqual(result["note_count"],2)
            with fitz.open(target) as pdf:
                self.assertGreater(pdf[1].rect.height,930)
                self.assertEqual(len(pdf),3)
            zero=Path(folder)/"none.pdf"
            annotate_pdf_inline(source,[],zero,quick_summary=False)
            with fitz.open(zero) as pdf:
                self.assertEqual(len(pdf),2)

    def test_bad_anchors_and_overwrite_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            source=self.synthetic(folder)
            target=Path(folder)/"review.pdf"
            for note in [InlineNote(1,"X",12,"Check","Try"),
                         InlineNote(3,"X",100,"Check","Try")]:
                with self.assertRaises(ValueError):
                    annotate_pdf_inline(source,[note],target)
            with self.assertRaises(ValueError):
                annotate_pdf_inline(source,[InlineNote(1,"X",140,"Check","Try")],source)
            annotate_pdf_inline(source,[],target)
            with self.assertRaises(FileExistsError):
                annotate_pdf_inline(source,[],target)

if __name__=="__main__":
    unittest.main()
