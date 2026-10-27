"""Org-claim tool on synthetic HTML only. No network, no real hostnames."""

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workshop_validator import org_claim
from workshop_validator.automate import FAILED, INCONCLUSIVE, SUCCESS, load_orgs, save_gate

PORTAL = """
<html><body><header>Synthetic portal</header>
<nav><a href="/events">My Events</a> <a href="/events/new">Add Event</a></nav>
<a href="/files/synthetic-guide.pdf">Exercise guide</a>
<a href="https://example.test/files/other.PDF">Second guide</a>
<a href="/files/synthetic-guide.pdf">Duplicate link</a>
</body></html>
"""

FORM = """
<form>
  <label for="code">Event Code</label><input id="code" type="text">
  <button type="button">Cancel</button><button type="submit">Submit</button>
</form>
"""

DETAILS = """
<div class="card"><h2>Your org</h2>
<dl><dt>Username</dt><dd>learner.synthetic@example.test</dd>
<dt>Password</dt><dd>Synth3tic!Pass</dd></dl></div>
"""

DETAILS_INPUTS = """
<label for="u">Username:</label><input id="u" readonly value="learner.two@example.test">
<label for="p">Password:</label><input id="p" readonly type="text" value="An0ther$Synthetic">
"""

AMBIGUOUS = """
<dl><dt>Username</dt><dd>first@example.test</dd><dt>Password</dt><dd>FirstSynth1!</dd></dl>
<dl><dt>Username</dt><dd>second@example.test</dd><dt>Password</dt><dd>SecondSynth2!</dd></dl>
"""


class PageLogicTests(unittest.TestCase):
    def test_dry_run_finds_the_add_event_link_and_the_form(self):
        link = org_claim.find_add_event(PORTAL)
        self.assertEqual((link["kind"], link["href"]), ("link", "/events/new"))
        form = org_claim.find_event_form(FORM)
        self.assertEqual(form, {"field": 'input[id="code"]', "button": "Submit"})

    def test_form_found_by_placeholder_inside_shadow_flattened_markup(self):
        html = '<c-claim><div><input name="eventCode" placeholder="Enter event code"></div>' \
               '<button>Add</button></c-claim>'
        self.assertEqual(org_claim.find_event_form(html)["button"], "Add")

    def test_formless_component_page_uses_the_nearest_named_button(self):
        html = """<div class="modal"><div><div><div class="field">
            <input type="text" id="ec" name="ec" aria-label="Event Code"><button type="button"></button>
            </div></div></div>
            <div class="footer"><button type="button">Cancel</button>
            <button type="button" disabled>Add Event</button></div></div>
            <nav><button type="button">Menu</button></nav>"""
        self.assertEqual(org_claim.find_event_form(html), {"field": 'input[id="ec"]', "button": "Add Event"})

    def test_form_without_a_code_field_or_with_two_submit_buttons_is_not_ready(self):
        self.assertIsNone(org_claim.find_event_form('<form><input id="x"><button>Go</button></form>'))
        two = FORM.replace("<button type=\"button\">Cancel</button>", "<button>Send</button>")
        self.assertIsNone(org_claim.find_event_form(two))

    def test_sign_in_page_is_reported(self):
        login = '<form><input name="u"><input type="password" name="p"><button>Log In</button></form>'
        self.assertTrue(org_claim.sign_in_required(login))
        self.assertFalse(org_claim.sign_in_required(PORTAL))
        self.assertTrue(org_claim.sign_in_required(PORTAL + '<input type="password">'))
        self.assertTrue(org_claim.has_password_field('<input type="password">'))

    def test_extractor_reads_labelled_fields(self):
        self.assertEqual(org_claim.extract_credentials(DETAILS),
                         ("learner.synthetic@example.test", "Synth3tic!Pass"))
        self.assertEqual(org_claim.extract_credentials(DETAILS_INPUTS),
                         ("learner.two@example.test", "An0ther$Synthetic"))

    def test_extractor_refuses_ambiguous_or_badly_shaped_values(self):
        self.assertIsNone(org_claim.extract_credentials(AMBIGUOUS))
        self.assertIsNone(org_claim.extract_credentials(DETAILS.replace("learner.synthetic@example.test", "not-an-email")))
        self.assertIsNone(org_claim.extract_credentials(DETAILS.replace("Synth3tic!Pass", "********")))
        self.assertIsNone(org_claim.extract_credentials("<p>Username</p><p>Password</p>"))

    def test_guide_links_are_absolute_and_unique(self):
        links = org_claim.find_guide_links(PORTAL, "https://portal.example.test/home")
        self.assertEqual(links, ["https://portal.example.test/files/synthetic-guide.pdf",
                                 "https://example.test/files/other.PDF"])


class WriterTests(unittest.TestCase):
    def test_append_is_atomic_0600_in_loader_format_and_silent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "verification-orgs.json"
            path.write_text(json.dumps({"usernames": ["a@example.test"], "password": "Shared1!x"}))
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                number = org_claim.append_org(root, "b@example.test", "NewSynth9!")
            self.assertEqual(number, 2)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            orgs = load_orgs(root)
            self.assertEqual([o["username"] for o in orgs], ["a@example.test", "b@example.test"])
            self.assertEqual(orgs[0]["password"], "Shared1!x")
            self.assertEqual([p.name for p in root.iterdir()], ["verification-orgs.json"])

    def test_a_failed_write_leaves_the_old_file_untouched(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "verification-orgs.json"
            path.write_text(json.dumps({"orgs": [{"username": "a@example.test", "password": "Old1!xxx"}]}))
            before = path.read_text()
            with mock.patch("workshop_validator.org_claim.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    org_claim.append_org(root, "b@example.test", "NewSynth9!")
            self.assertEqual(path.read_text(), before)
            self.assertEqual([p.name for p in root.iterdir()], ["verification-orgs.json"])

    def test_temp_file_is_private_before_it_replaces_the_org_file(self):
        with tempfile.TemporaryDirectory() as folder:
            modes = []
            real = os.replace

            def spy(src, dst):
                modes.append(stat.S_IMODE(os.stat(src).st_mode))
                real(src, dst)

            with mock.patch("workshop_validator.org_claim.os.replace", side_effect=spy):
                org_claim.append_org(Path(folder), "a@example.test", "Synth1!xx")
            self.assertEqual(modes, [0o600])

    def test_duplicate_org_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            org_claim.append_org(root, "a@example.test", "Synth1!xx")
            with self.assertRaises(org_claim.ClaimError):
                org_claim.append_org(root, "A@example.test", "Synth2!xx")

    def test_guides_are_saved_0600_and_non_pdf_is_skipped(self):
        with tempfile.TemporaryDirectory() as folder:
            saved = org_claim.save_guides(Path(folder), [("https://x.test/a.pdf", b"%PDF-1.4 x"),
                                                         ("https://x.test/b.pdf", b"<html>")])
            self.assertEqual(saved, 1)
            files = list((Path(folder) / "team" / "guide-inbox").iterdir())
            self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)


class ClaimGateTests(unittest.TestCase):
    def _root(self, folder, *, orgs, gate_org, last):
        root = Path(folder)
        (root / "verification-orgs.json").write_text(json.dumps(
            {"orgs": [{"username": f"u{i}@example.test", "password": "Synth1!xx"} for i in range(orgs)]}))
        save_gate(root, {"org": gate_org, "frozen": last != SUCCESS, "reason": "", "last_outcome": last})
        return root

    def _claim(self, folder, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(org_claim, "claim") as claim, \
                mock.patch.object(org_claim, "load_config", return_value={"portal_url": "https://x.test"}):
            claim.return_value = "org-claim: CLAIMED org 3"
            code = org_claim.main(["--claim", "--private-root", folder, *extra])
        return code, out.getvalue(), claim

    def test_refused_while_an_unused_org_remains(self):
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=1, last=SUCCESS)
            code, out, claim = self._claim(folder)
            self.assertEqual(code, org_claim.REFUSED)
            self.assertIn("unused org remains", out)
            claim.assert_not_called()

    def test_refused_after_an_inconclusive_run(self):
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=2, last=INCONCLUSIVE)
            code, out, claim = self._claim(folder)
            self.assertEqual(code, org_claim.REFUSED)
            claim.assert_not_called()

    def test_allowed_when_every_org_is_used_and_the_rule_says_move_on(self):
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=2, last=FAILED)
            code, out, claim = self._claim(folder)
            self.assertEqual((code, out.strip()), (0, "org-claim: CLAIMED org 3"))
            claim.assert_called_once()

    def test_force_skips_the_rule(self):
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=0, last=INCONCLUSIVE)
            code, _, claim = self._claim(folder, "--force")
            self.assertEqual(code, 0)
            claim.assert_called_once()

    def test_errors_never_echo_their_message(self):
        with tempfile.TemporaryDirectory() as folder:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    mock.patch.object(org_claim, "load_config", return_value={"portal_url": "https://x.test"}), \
                    mock.patch.object(org_claim, "dry_run", side_effect=RuntimeError("https://secret.example.test")):
                code = org_claim.main(["--private-root", folder])
            self.assertEqual(code, org_claim.INCONCLUSIVE)
            self.assertNotIn("secret", out.getvalue())

    def test_config_must_be_private(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "org-claim.json"
            path.write_text(json.dumps({"portal_url": "https://x.test", "event_code": "SYNTH"}))
            os.chmod(path, 0o644)
            with self.assertRaises(org_claim.ClaimError):
                org_claim.load_config(Path(folder))
            os.chmod(path, 0o600)
            self.assertEqual(org_claim.load_config(Path(folder))["portal_url"], "https://x.test")


if __name__ == "__main__":
    unittest.main()
