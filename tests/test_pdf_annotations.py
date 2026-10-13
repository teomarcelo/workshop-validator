import hashlib
import os
import tempfile
import unittest

from workshop_validator.pdf_annotations import ReviewNote, annotate_pdf

try:
    import fitz
except ImportError:
    fitz = None


@unittest.skipUnless(fitz is not None, "PyMuPDF required for synthetic PDF tests")
class AnnotationTests(unittest.TestCase):
    def make_source(self, directory):
        path = os.path.join(directory, "synthetic_source.pdf")
        doc = fitz.open()
        for index in range(2):
            page = doc.new_page(width=612, height=792)
            page.insert_text((80, 120), f"SYNTHETIC STEP {index + 1}", fontsize=15)
        doc.save(path)
        doc.close()
        return path

    def test_annotated_pdf_preserves_original_pages_pixel_identically(self):
        with tempfile.TemporaryDirectory() as folder:
            original = self.make_source(folder)
            output = os.path.join(folder, "annotated.pdf")
            result = annotate_pdf(original, [
                ReviewNote(1, "P001N001", "NEEDS_REVIEW", "Open a synthetic page",
                           "The expected label may need review.", "", (), True),
            ], output)
            self.assertEqual(result["source_pages"], 2)
            self.assertEqual(result["review_sheets"], 1)
            with fitz.open(original) as before, fitz.open(output) as after:
                self.assertEqual(after.page_count, 3)
                for original_index, annotated_index in ((0, 0), (1, 2)):
                    a = before[original_index].get_pixmap(alpha=False)
                    b = after[annotated_index].get_pixmap(alpha=False)
                    self.assertEqual(hashlib.sha256(a.samples).digest(),
                                     hashlib.sha256(b.samples).digest())
                text = after[1].get_text()
                self.assertIn("ILLUSTRATIVE ONLY", text)
                self.assertIn("ORIGINAL GUIDE INSTRUCTION", text)

    def test_multiple_notes_and_long_text_wrap_across_pages(self):
        with tempfile.TemporaryDirectory() as folder:
            src = self.make_source(folder)
            out = os.path.join(folder, "annotated.pdf")
            long_text = "A long finding must wrap safely. " * 110
            result = annotate_pdf(src, [
                ReviewNote(1, "A", "UI_MISMATCH", "Original", long_text,
                           "Proposed change after review", ("evidence-1",)),
                ReviewNote(2, "B", "ENV_BLOCKED", "Other", "429 rate limit")
            ], out)
            self.assertGreaterEqual(result["review_sheets"], 3)
            with fitz.open(out) as d:
                self.assertEqual(len(d), result["total_pages"])
                self.assertIn("STATUS: UI_MISMATCH", d[1].get_text())

    def test_refuse_source_overwrite_and_unapproved_status(self):
        with tempfile.TemporaryDirectory() as folder:
            src = self.make_source(folder)
            note = ReviewNote(1, "a", "PASS", "Original", "Finding")
            with self.assertRaises(ValueError):
                annotate_pdf(src, [note], os.path.join(folder, "out.pdf"))
            with self.assertRaises(ValueError):
                annotate_pdf(src, [ReviewNote(1, "a", "NEEDS_REVIEW", "Original", "Finding")], src)

    def test_private_file_permissions_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            src = self.make_source(folder)
            out = os.path.join(folder, "output.pdf")
            note = ReviewNote(2, "s1", "NEEDS_REVIEW", "Example", "Needs review")
            annotate_pdf(src, [note], out)
            self.assertEqual(os.stat(out).st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                annotate_pdf(src, [note], out)


if __name__ == "__main__":
    unittest.main()
