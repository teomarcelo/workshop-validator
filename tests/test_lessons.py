import json
import tempfile
import unittest
from pathlib import Path

from workshop_validator import lessons
from workshop_validator.lessons import classify, compact_from, read_lessons, record_lessons, sanitize


class ClassifyTests(unittest.TestCase):
    def test_plain_pass_and_cascades_are_not_lessons(self):
        self.assertIsNone(classify("Clicked Save.", "PASS"))
        self.assertIsNone(classify("Not run because an earlier step failed.", "NOT_RUN"))
        self.assertIsNone(classify("", "NOT_RUN"))

    def test_kinds(self):
        self.assertEqual(classify("Einstein was already on in the provisioned org.", "PASS"), "observation")
        self.assertEqual(classify("The page was still loading.", "NOT_RUN"), "observation")
        self.assertEqual(classify("Could not find Save.", "UI_MISMATCH"), "finding")
        self.assertEqual(classify("No action yet.", "NEEDS_REVIEW"), "finding")
        self.assertEqual(classify("HTTP 429.", "ENV_BLOCKED"), "observation")
        self.assertEqual(classify("An unexpected error stopped the step.", "FUNCTIONAL_FAIL"), "mistake")

    def test_sanitize_drops_secrets_and_org_hosts(self):
        text = sanitize("password hunter2 user person@example.com at acme-dev-ed.my.salesforce.com/x orgfarm12")
        for bad in ("hunter2", "person", "example.com", "acme-dev-ed", "orgfarm12"):
            self.assertNotIn(bad, text)


class StoreTests(unittest.TestCase):
    def test_repeats_are_counted_not_appended(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [("Click Save", "UI_MISMATCH", "Could not find Save. The page showed: abc"),
                    ("Click Next", "PASS", "Clicked."),
                    ("Click Done", "NOT_RUN", "Not run because an earlier step failed.")]
            record_lessons(root, rows, today="2026-01-01")
            record_lessons(root, [("Click Save", "UI_MISMATCH", "Could not find Save. The page showed: xyz")],
                           today="2026-01-02")
            entries = read_lessons(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual((entries[0]["seen"], entries[0]["last"]), (2, "2026-01-02"))
            self.assertEqual(read_lessons(root, "Click Save")[0]["status"], "UI_MISMATCH")
            self.assertEqual(read_lessons(root, "Click Other"), [])
            self.assertEqual(oct(lessons.lessons_path(root).stat().st_mode & 0o777), "0o600")
            self.assertIn("seen 2×", lessons.lessons_path(root).read_text())

    def test_size_is_capped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            rows = [(f"Click button {i} " + "x" * 200, "UI_MISMATCH", "y" * 500) for i in range(800)]
            path = record_lessons(root, rows)
            self.assertLessEqual(len(read_lessons(root)), lessons.MAX_ENTRIES)
            self.assertLessEqual(path.stat().st_size, lessons.MAX_BYTES)

    def test_old_append_only_file_compacts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old = root / "old.md"
            line = "- **Finding.** Click Save — UI_MISMATCH. Could not find Save."
            old.write_text("# Workshop lessons\n\n" + "\n".join([line] * 50)
                           + "\n- **Finding.** The run froze. Could not find Save.\n")
            compact_from(root, old)
            entries = json.loads((root / "lessons.json").read_text())["entries"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["seen"], 50)


if __name__ == "__main__":
    unittest.main()


class RootCauseTests(unittest.TestCase):
    def test_root_cause_is_recorded_and_a_repeat_is_recurring(self):
        import tempfile
        from pathlib import Path
        from workshop_validator.lessons import CAUSE, format_lesson, read_lessons, record_cause, team_rules
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = record_cause(root, title="Click Save.", cause="Button matched twice.",
                                 fix="Scope to dialog.", never="Never click outside the open dialog.",
                                 pr="PR 1", today="2026-01-01")
            self.assertEqual(first["seen"], 1)
            self.assertNotIn("RECURRING", format_lesson(first))
            again = record_cause(root, title="Click Save.", cause="Button matched twice.", fix="Wait for dialog.",
                                 today="2026-01-02")
            self.assertEqual(again["seen"], 2)
            self.assertIn("RECURRING", format_lesson(again))
            self.assertEqual(again["fix"], "Wait for dialog.")
            self.assertEqual(len(read_lessons(root, kind=CAUSE)), 1)
            self.assertEqual(len(team_rules(root)), 1)
            self.assertIn("Root causes and team rules", (root / "lessons.md").read_text())
            self.assertEqual(oct((root / "lessons.json").stat().st_mode & 0o777), "0o600")

    def test_team_lesson_is_sanitized_and_kept_when_capped(self):
        import tempfile
        from pathlib import Path
        from workshop_validator import lessons
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            lessons.record_cause(root, kind=lessons.TEAM, title="check-fixer",
                                 cause="Pasted user@example.com into a test.", never="Synthetic text only.")
            rows = [(f"Step {i} " + "x" * 400, "UI_MISMATCH", "y" * 500) for i in range(800)]
            lessons.record_lessons(root, rows)
            team = lessons.read_lessons(root, kind=lessons.TEAM)
            self.assertEqual(len(team), 1)
            self.assertNotIn("@", team[0]["detail"])

    def test_bad_kind_or_empty_cause_is_refused(self):
        import tempfile
        from pathlib import Path
        from workshop_validator.lessons import record_cause
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                record_cause(Path(folder), title="t", cause="c", kind="finding")
            with self.assertRaises(ValueError):
                record_cause(Path(folder), title="t", cause="")

    def test_cli_record_and_rules(self):
        import io
        import tempfile
        from contextlib import redirect_stdout
        from workshop_validator.lessons import main
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(main(["--private-root", folder, "--record", "team", "--title", "rule-auditor",
                                   "--cause", "Vetoed a fuzzy click.", "--never", "No partial-name clicks."]), 0)
            out = io.StringIO()
            with redirect_stdout(out):
                main(["--private-root", folder, "--rules"])
            self.assertIn("No partial-name clicks.", out.getvalue())
