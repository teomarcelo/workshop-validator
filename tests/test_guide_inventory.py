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

    def test_roman_substeps_belong_to_their_letter_step(self):
        path = self.make_pdf([(72, "Task 1: Example"), (90, "1. Update your record."),
                              (126, "a. Enter the following:"), (156, "i. First Name: Ada"),
                              (153, "ii. Last Name: Lovelace"), (126, "b. Click Save.")])
        result = extract_inventory(path)
        numbers = [(s.number, s.parent_id) for s in result.steps]
        first, letter_a = result.steps[0].step_id, result.steps[1].step_id
        self.assertEqual(numbers, [("1", None), ("a", first), ("i", letter_a), ("ii", letter_a), ("b", first)])

    def test_large_heading_names_the_exercise(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        c = canvas.Canvas(path, pagesize=(612, 792))
        c.setFont("Helvetica", 16)
        c.drawString(72, 700, "Use Case 9-9: Synthetic Widget")
        c.drawString(72, 684, "Builder")
        c.setFont("Helvetica", 11)
        c.drawString(90, 650, "1. Open the widget page.")
        c.setFont("Helvetica", 16)
        c.drawString(72, 600, "Gadget Basics")
        c.setFont("Helvetica", 11)
        c.drawString(90, 560, "1. Open the gadget page.")
        c.showPage()
        c.save()
        result = extract_inventory(path)
        self.assertEqual([s.exercise for s in result.steps],
                         ["Use Case 9-9: Synthetic Widget Builder", "Gadget Basics"])

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
