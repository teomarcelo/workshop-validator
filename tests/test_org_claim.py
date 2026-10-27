"""Org-claim tool on synthetic HTML only. No network, no real hostnames."""

import contextlib
import fcntl
import io
import json
import os
import stat
import tempfile
import threading
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
            self.assertEqual([p.name for p in root.iterdir() if not p.name.endswith(".lock")],
                             ["verification-orgs.json"])

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
            self.assertEqual([p.name for p in root.iterdir() if not p.name.endswith(".lock")],
                             ["verification-orgs.json"])

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
            with mock.patch.object(org_claim.sys, "stdin") as stdin:
                stdin.isatty.return_value = True
                code, _, claim = self._claim(folder, "--force")
            self.assertEqual(code, 0)
            claim.assert_called_once()

    def test_force_is_refused_without_an_owner_at_a_terminal(self):
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=0, last=INCONCLUSIVE)
            with mock.patch.object(org_claim.sys, "stdin") as stdin:
                stdin.isatty.return_value = False
                code, out, claim = self._claim(folder, "--force")
            self.assertEqual(code, org_claim.REFUSED)
            self.assertIn("REFUSED --force needs the owner at a terminal", out)
            claim.assert_not_called()

    def _marker(self, folder, last_run, result):
        path = Path(folder) / "team" / "claim-attempt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_run": last_run, "result": result}))

    def _with_run(self, folder, run_id):
        root = self._root(folder, orgs=2, gate_org=2, last=FAILED)
        gate = json.loads((root / "gate.json").read_text())
        save_gate(root, {**gate, "last_run": run_id})

    def test_second_claim_for_the_same_run_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            self._marker(folder, "run-1", "started")
            code, out, claim = self._claim(folder)
            self.assertEqual(code, org_claim.REFUSED)
            self.assertIn("REFUSED a claim was already tried for this run", out)
            claim.assert_not_called()

    def test_claim_is_allowed_after_a_claimed_attempt_or_for_a_new_run(self):
        for marker_run, result in (("run-1", "CLAIMED"), ("run-0", "started")):
            with tempfile.TemporaryDirectory() as folder:
                self._with_run(folder, "run-1")
                self._marker(folder, marker_run, result)
                code, _, claim = self._claim(folder)
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


PORTAL_URL = "https://portal.example.test/home"


class FakePage:
    """A synthetic page: goto follows a route table, clicks run hooks. No network."""

    def __init__(self, routes, on_click=None, on_eval=None):
        self.routes, self.on_click, self.on_eval = routes, on_click, on_eval
        self.url, self.html = "about:blank", ""
        self.filled, self.clicked = [], []

    def goto(self, url, wait_until=None):
        self.url, self.html = self.routes[url]

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, _js):
        html = self.html
        if self.on_eval:
            self.on_eval(self)
        return html

    def locator(self, selector):
        page = self
        return mock.Mock(first=mock.Mock(fill=lambda value: page.filled.append(selector)))

    def get_by_role(self, kind, name, exact=False):
        page = self

        def click():
            page.clicked.append(name)
            if page.on_click:
                page.on_click(page)
        return mock.Mock(first=mock.Mock(click=click))


def _routes(form_final="https://portal.example.test/events/new"):
    return {PORTAL_URL: (PORTAL_URL, PORTAL),
            "https://portal.example.test/events/new": (form_final, FORM)}


def _to_details(url):
    def hook(page):
        page.url, page.html = url, DETAILS
    return hook


class PortalBoundaryTests(unittest.TestCase):
    """The browser never leaves the https portal host, redirects included."""

    def _claim(self, root, page):
        with mock.patch.object(org_claim, "_browser", side_effect=lambda run: run(None, page)):
            return org_claim.claim(root, {"portal_url": PORTAL_URL, "event_code": "SYNTHETIC"})

    def test_a_redirect_to_plain_http_stops_before_the_form(self):
        page = FakePage(_routes("http://portal.example.test/events/new"))
        with self.assertRaises(org_claim.ClaimError) as caught:
            org_claim._open_form(page, PORTAL_URL)
        self.assertEqual(str(caught.exception), "left the https portal")

    def test_a_redirect_to_another_host_stops_before_the_form(self):
        page = FakePage(_routes("https://elsewhere.example.test/events/new"))
        with self.assertRaises(org_claim.ClaimError) as caught:
            org_claim._open_form(page, PORTAL_URL)
        self.assertEqual(str(caught.exception), "left the https portal")

    def test_the_code_is_never_filled_after_a_late_redirect(self):
        def late(page):
            if page.html == FORM:
                page.url = "https://elsewhere.example.test/events/new"
        with tempfile.TemporaryDirectory() as folder:
            page = FakePage(_routes(), on_click=_to_details("https://portal.example.test/done"), on_eval=late)
            with self.assertRaises(org_claim.ClaimError) as caught:
                self._claim(Path(folder), page)
            self.assertEqual(str(caught.exception), "left the https portal")
            self.assertEqual((page.filled, page.clicked), ([], []))

    def test_no_org_is_saved_from_a_page_off_the_portal(self):
        with tempfile.TemporaryDirectory() as folder:
            page = FakePage(_routes(), on_click=_to_details("https://elsewhere.example.test/done"))
            with self.assertRaises(org_claim.ClaimError) as caught:
                self._claim(Path(folder), page)
            self.assertEqual(str(caught.exception), "left the https portal")
            self.assertFalse((Path(folder) / "verification-orgs.json").exists())

    def test_a_claim_on_the_portal_saves_and_marks_the_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 0, "last_outcome": FAILED, "last_run": "run-7"})
            page = FakePage(_routes(), on_click=_to_details("https://portal.example.test/done"))
            self.assertEqual(self._claim(root, page), "org-claim: CLAIMED org 1")
            marker = root / "team" / "claim-attempt"
            self.assertEqual(json.loads(marker.read_text()), {"last_run": "run-7", "result": "CLAIMED"})
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

    def test_the_attempt_is_marked_before_the_submit_click(self):
        seen = []
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 0, "last_outcome": FAILED, "last_run": "run-8"})
            marker = root / "team" / "claim-attempt"

            def on_click(page):
                seen.append(json.loads(marker.read_text()) if marker.exists() else None)
                page.url, page.html = "https://elsewhere.example.test/x", DETAILS
            with self.assertRaises(org_claim.ClaimError):
                self._claim(root, FakePage(_routes(), on_click=on_click))
            self.assertEqual(seen, [{"last_run": "run-8", "result": "started"}])
            self.assertEqual(json.loads(marker.read_text())["result"], "started")

    def test_guides_only_from_https_links_on_the_portal_host(self):
        html = PORTAL + '<a href="http://portal.example.test/files/plain.pdf">Plain</a>' \
                        '<a href="/files/moved.pdf">Moved</a>'
        page = FakePage({PORTAL_URL: (PORTAL_URL, html)})
        fetched = []

        def get(url):
            fetched.append(url)
            final = "https://elsewhere.example.test/m.pdf" if url.endswith("moved.pdf") else url
            return mock.Mock(ok=True, url=final, body=lambda: b"%PDF-1.4 synthetic")
        context = mock.Mock(request=mock.Mock(get=get))
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(org_claim, "_browser", side_effect=lambda run: run(context, page)):
            line = org_claim.guides(Path(folder), {"portal_url": PORTAL_URL})
        self.assertEqual(line, "org-claim: GUIDES 1 (skipped 3 off-portal links)")
        self.assertEqual(fetched, ["https://portal.example.test/files/synthetic-guide.pdf",
                                   "https://portal.example.test/files/moved.pdf"])


class LockAndInboxTests(unittest.TestCase):
    def test_append_waits_for_the_org_file_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            org_claim.append_org(root, "a@example.test", "Synth1!xx")
            lock = os.open(root / ".verification-orgs.json.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                worker = threading.Thread(target=org_claim.append_org,
                                          args=(root, "b@example.test", "Synth2!xx"))
                worker.start()
                worker.join(0.5)
                self.assertTrue(worker.is_alive(), "append_org did not wait for the lock")
                self.assertEqual(len(load_orgs(root)), 1)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)
            worker.join(5)
            self.assertEqual([o["username"] for o in load_orgs(root)], ["a@example.test", "b@example.test"])

    def test_inbox_folder_is_private_even_when_it_already_existed(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder) / "team" / "guide-inbox"
            inbox.mkdir(parents=True)
            os.chmod(inbox, 0o755)
            org_claim.save_guides(Path(folder), [("https://x.test/a.pdf", b"%PDF-1.4 x")])
            self.assertEqual(stat.S_IMODE(inbox.stat().st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
