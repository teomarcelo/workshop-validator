import os
import tempfile
import unittest

from workshop_validator.guide_inventory import clean, extract_inventory

try:
    from reportlab.pdfgen import canvas
except ImportError:
    canvas = None


@unittest.skipIf(canvas is None, "reportlab required for synthetic PDF fixtures")
class GuideInventoryTests(unittest.TestCase):
    def make_pdf(self, *pages):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        c = canvas.Canvas(path, pagesize=(612, 792), pageCompression=0)
        for lines in pages:
            for i, (x, text) in enumerate(lines):
                c.drawString(x, 690 - i * 25, text)
            c.showPage()
        c.save()
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_normalize_preserves_unrecoverable_text(self):
        self.assertEqual(clean("Update the fi\u200b"), "Update the fi")

    def test_numbered_steps_and_coordinates(self):
        path = self.make_pdf([(72, "Exercise 1-1: Synthetic"),
                              (72, "Task 1: Example"),
                              (90, "1. Open Settings"),
                              (90, "2. Click Save")])
        result = extract_inventory(path)
        self.assertEqual(result.page_count, 1)
        self.assertEqual([s.number for s in result.steps], ["1", "2"])
        self.assertEqual(result.steps[0].step_id, "P001N001")
        self.assertEqual(result.steps[0].task, "Task 1: Example")
        self.assertAlmostEqual(result.steps[0].bbox[0], 90, delta=1)
        self.assertFalse(result.to_dict()["verified_complete"])

    def test_nested_steps_with_cross_page_parent(self):
        path = self.make_pdf([(72, "Task 1: Example"), (90, "1. Do the following:")],
                             [(126, "a. Open the settings tab"), (126, "b. Click Save")])
        result = extract_inventory(path)
        self.assertEqual(len(result.steps), 3)
        self.assertEqual(result.steps[1].parent_id, result.steps[0].step_id)
        self.assertEqual(result.steps[2].parent_id, result.steps[0].step_id)
        self.assertFalse(result.steps[1].warnings)

    def test_ambiguous_table_instruction_is_flagged(self):
        path = self.make_pdf([(72, "Task 1: Example"),
                              (90, "1. Enter the following values:"),
                              (90, "2. Click Save")])
        result = extract_inventory(path)
        self.assertIn("table_or_visual_context_required", result.steps[0].warnings)

    def test_truncated_instruction_is_flagged(self):
        path = self.make_pdf([(72, "Task 1: Example"),
                              (90, "1. Update the fi")])
        self.assertIn("possibly_truncated_text", extract_inventory(path).steps[0].warnings)

    def test_nonsequential_numbering_is_flagged(self):
        path = self.make_pdf([(72, "Task 1: Example"), (90, "1. First"),
                              (90, "4. Fourth")])
        result = extract_inventory(path)
        self.assertIn("numbering_review_required", result.steps[1].warnings)

    def test_empty_page_never_verified_complete(self):
        path = self.make_pdf([(72, "Exercise 1-1: Empty")])
        result = extract_inventory(path)
        self.assertEqual(result.pages_without_steps, (1,))
        self.assertFalse(result.to_dict()["verified_complete"])

if __name__ == "__main__":
    unittest.main()
