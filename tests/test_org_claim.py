"""Org-claim tool on synthetic HTML only. No network, no real hostnames."""

import contextlib
import fcntl
import hashlib
import io
import itertools
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from workshop_validator import org_claim
from workshop_validator import automate
from workshop_validator.automate import FAILED, INCONCLUSIVE, SUCCESS, load_gate, load_orgs, save_gate

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

OLD_BLOCK = """
<section class="event"><h3>Earlier event</h3><p>OrgClaim Code: SYNTH-OLD</p>
  <div><span class="icon"></span>Salesforce Org Username:</div><div>old.learner@example.test</div>
  <div><span class="icon"></span>Salesforce Org Password:</div><div>OldSynth1!pass</div>
</section>
"""

NEW_BLOCK = """
<section class="event"><h3>Just redeemed</h3><p>OrgClaim Code: SYNTH-NEW</p>
  <div><span class="icon"></span>Salesforce Org Username:</div><div>new.learner@example.test</div>
  <div><span class="icon"></span>Salesforce Org Password:</div><div>NewSynth2!pass</div>
</section>
"""

OTHER_NEW_BLOCK = NEW_BLOCK.replace("new.learner", "other.learner").replace("NewSynth2", "OtherSynth3")

SALESFORCE_ORG_LABELS = """
<div class="credentials">
  <div><span class="icon"></span>Salesforce Org Username:</div>
  <div>salesforce.user@example.test</div>
  <div><span class="icon"></span>Salesforce Org Password:</div>
  <div>Salesforce1!Pass</div>
</div>
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

    def test_extractor_reads_salesforce_org_username_and_password_labels(self):
        """W-097: accept 'Salesforce Org Username:' and 'Salesforce Org Password:' labels."""
        self.assertEqual(org_claim.extract_credentials(SALESFORCE_ORG_LABELS),
                         ("salesforce.user@example.test", "Salesforce1!Pass"))

    def test_extractor_refuses_ambiguous_or_badly_shaped_values(self):
        self.assertIsNone(org_claim.extract_credentials(AMBIGUOUS))
        self.assertIsNone(org_claim.extract_credentials(DETAILS.replace("learner.synthetic@example.test", "not-an-email")))
        self.assertIsNone(org_claim.extract_credentials(DETAILS.replace("Synth3tic!Pass", "********")))
        self.assertIsNone(org_claim.extract_credentials("<p>Username</p><p>Password</p>"))

    def test_two_event_blocks_pick_the_one_new_username_and_its_own_password(self):
        """Brief point 2: the old block is excluded; the password comes from the new block."""
        page = OLD_BLOCK + NEW_BLOCK
        self.assertEqual(org_claim.extract_credentials(page, {"old.learner@example.test"}),
                         ("new.learner@example.test", "NewSynth2!pass"))
        self.assertEqual(org_claim.extract_credentials(NEW_BLOCK + OLD_BLOCK, {"OLD.learner@example.test"}),
                         ("new.learner@example.test", "NewSynth2!pass"))

    def test_two_new_usernames_or_only_old_ones_are_not_read(self):
        page = OLD_BLOCK + NEW_BLOCK + OTHER_NEW_BLOCK
        self.assertIsNone(org_claim.extract_credentials(page, {"old.learner@example.test"}))
        self.assertIsNone(org_claim.extract_credentials(OLD_BLOCK, {"old.learner@example.test"}))

    def test_a_new_username_without_a_password_in_its_own_block_is_not_read(self):
        """The password is never borrowed from another block."""
        new_without = NEW_BLOCK.replace("NewSynth2!pass", "")
        new_without = new_without.replace('<div><span class="icon"></span>Salesforce Org Password:</div><div></div>', "")
        self.assertIsNone(org_claim.extract_credentials(OLD_BLOCK + new_without, {"old.learner@example.test"}))

    def test_usernames_on_page_collects_labelled_and_email_values(self):
        seen = org_claim.usernames_on_page(OLD_BLOCK + "<p>Signed in as Owner.Synthetic@example.test</p>")
        self.assertEqual(seen, {"old.learner@example.test", "owner.synthetic@example.test"})

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
            # The gate is pointed at the new org by claim(), after CLAIMED is marked.
            files = sorted([p.name for p in root.iterdir() if not p.name.endswith(".lock")])
            self.assertEqual(files, ["verification-orgs.json"])

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

    def _claim(self, folder, *extra, event_code="SYNTH"):
        """main --claim with the real claim() and its locks; only the browser is replaced."""
        out = io.StringIO()
        config = {"portal_url": "https://x.test", "event_code": event_code}
        with contextlib.redirect_stdout(out), mock.patch.object(org_claim, "_browser") as browser, \
                mock.patch.object(org_claim, "load_config", return_value=config):
            browser.return_value = "org-claim: CLAIMED org 3"
            code = org_claim.main(["--claim", "--private-root", folder, *extra])
        return code, out.getvalue(), browser

    def test_w097_allowed_with_inconclusive_gate_and_unused_org(self):
        """W-097: claim allowed with inconclusive gate and unused org left."""
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=1, last=INCONCLUSIVE)
            code, out, claim = self._claim(folder)
            self.assertEqual((code, out.strip()), (0, "org-claim: CLAIMED org 3"))
            claim.assert_called_once()

    def test_w043_refused_while_an_unused_org_remains_changed_to_w097_allowed(self):
        """W-043 test changed for W-097: claim now allowed regardless of unused orgs."""
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=1, last=SUCCESS)
            code, out, claim = self._claim(folder)
            self.assertEqual(code, 0)
            claim.assert_called_once()

    def test_w043_refused_after_an_inconclusive_run_changed_to_w097_allowed(self):
        """W-043 test changed for W-097: claim now allowed regardless of last outcome."""
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=2, last=INCONCLUSIVE)
            code, out, claim = self._claim(folder)
            self.assertEqual(code, 0)
            claim.assert_called_once()

    def test_w043_allowed_when_every_org_is_used_changed_to_w097_still_allowed(self):
        """W-043 test changed for W-097: still allowed with event_code set."""
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=2, last=FAILED)
            code, out, claim = self._claim(folder)
            self.assertEqual((code, out.strip()), (0, "org-claim: CLAIMED org 3"))
            claim.assert_called_once()

    def test_w097_force_is_accepted_as_noop_alias(self):
        """W-097: --force accepted as no-op alias, no tty check."""
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=0, last=INCONCLUSIVE)
            code, _, claim = self._claim(folder, "--force")
            self.assertEqual(code, 0)
            claim.assert_called_once()

    def test_w097_refused_without_event_code(self):
        """W-097: claim refused when event_code not set in org-claim.json."""
        with tempfile.TemporaryDirectory() as folder:
            self._root(folder, orgs=2, gate_org=0, last=INCONCLUSIVE)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), \
                    mock.patch.object(org_claim, "load_config", return_value={"portal_url": "https://x.test"}):
                code = org_claim.main(["--claim", "--private-root", folder])
            self.assertEqual(code, org_claim.REFUSED)
            self.assertIn("no event_code", out.getvalue())

    def _marker(self, folder, last_run, result):
        path = Path(folder) / "team" / "claim-attempt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_run": last_run, "result": result}))

    def _with_run(self, folder, run_id):
        root = self._root(folder, orgs=2, gate_org=2, last=FAILED)
        gate = json.loads((root / "gate.json").read_text())
        save_gate(root, {**gate, "last_run": run_id})

    def test_second_claim_for_same_run_with_claimed_status_is_refused(self):
        """W-097: only CLAIMED status blocks the next claim for the same run."""
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            self._marker(folder, "run-1", "CLAIMED")
            code, out, claim = self._claim(folder)
            self.assertEqual(code, org_claim.REFUSED)
            self.assertIn("REFUSED a claim was already tried for this run", out)
            claim.assert_not_called()

    def test_the_marker_is_reserved_before_the_browser_opens(self):
        """The first claim reserves the marker; a finished "started" claim does not block the next."""
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            first, _, claim = self._claim(folder)  # claim() mocked: no browser, marker stays "started"
            self.assertEqual(first, 0)
            marker = Path(folder) / "team" / "claim-attempt"
            self.assertTrue(marker.exists())
            digest = hashlib.sha256(b"SYNTH").hexdigest()
            self.assertEqual(json.loads(marker.read_text()),
                             {"last_run": "run-1", "result": "started", "code_sha256": digest,
                              "code_attempts": {digest: 1}})
            self.assertNotIn("SYNTH", marker.read_text().replace(digest, ""))
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)
            # Now with marker at "started", another claim should be allowed (W-097)
            second, out, again = self._claim(folder)
            self.assertEqual(second, 0)  # W-097: allowed despite "started" marker
            again.assert_called_once()

    def test_only_one_of_many_concurrent_claims_runs(self):
        """The claim-run flock is held for the whole claim: of 8 concurrent claims one runs."""
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            start, release, entered, results = threading.Barrier(8), threading.Event(), [], []
            config = {"portal_url": "https://x.test", "event_code": "SYNTH"}

            def browser(_run):
                entered.append(1)
                release.wait(5)
                return "org-claim: CLAIMED org 3"

            def go():
                start.wait()
                try:
                    results.append(org_claim.claim(Path(folder), config))
                except org_claim.ClaimRefused as exc:
                    results.append(str(exc))
            with mock.patch.object(org_claim, "_browser", side_effect=browser):
                workers = [threading.Thread(target=go) for _ in range(8)]
                for worker in workers:
                    worker.start()
                for _ in range(100):  # the seven refusals return while the first claim runs
                    if len(results) == 7:
                        break
                    threading.Event().wait(0.05)
                running = list(results)
                release.set()
                for worker in workers:
                    worker.join(5)
            self.assertEqual(running, [org_claim.RUNNING_REFUSED] * 7)
            self.assertEqual(len(entered), 1)
            self.assertEqual(sorted(results), sorted(["org-claim: CLAIMED org 3"] + [org_claim.RUNNING_REFUSED] * 7))

    def test_a_claim_is_refused_while_the_run_lock_is_held_by_another_fd(self):
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            lock_path = Path(folder) / "team" / ".claim-run.lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                code, out, browser = self._claim(folder)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            self.assertEqual((code, out.strip()), (org_claim.REFUSED, "org-claim: REFUSED another claim is running"))
            browser.assert_not_called()
            self.assertFalse((Path(folder) / "team" / "claim-attempt").exists())
            code, _, browser = self._claim(folder)  # the lock is free again
            self.assertEqual(code, 0)
            browser.assert_called_once()

    def test_one_event_code_is_tried_at_most_three_times(self):
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            codes = []
            for _ in range(3):
                code, _, browser = self._claim(folder)
                codes.append(code)
                browser.assert_called_once()
            code, out, browser = self._claim(folder)
            self.assertEqual(codes, [0, 0, 0])
            self.assertEqual((code, out.strip()),
                             (org_claim.REFUSED, "org-claim: REFUSED this event code was already tried 3 times"))
            browser.assert_not_called()
            marker = (Path(folder) / "team" / "claim-attempt").read_text()
            self.assertNotIn("SYNTH", marker.replace(hashlib.sha256(b"SYNTH").hexdigest(), ""))
            code, _, browser = self._claim(folder, event_code="SYNTH-TWO")  # another code has its own count
            self.assertEqual(code, 0)
            browser.assert_called_once()

    def test_claim_is_allowed_after_a_new_run_or_non_claimed_marker(self):
        """W-097: allowed for different run, or same run with non-CLAIMED status."""
        # Case 1: Different run with any status - allowed
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            self._marker(folder, "run-0", "started")
            code, _, claim = self._claim(folder)
            self.assertEqual(code, 0)
            claim.assert_called_once()
        # Case 2: Same run with CLAIMED status - blocked
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            self._marker(folder, "run-1", "CLAIMED")
            code, _, claim = self._claim(folder)
            self.assertEqual(code, org_claim.REFUSED)
            claim.assert_not_called()

    def test_w097_started_marker_does_not_block_next_claim(self):
        """W-097: a 'started' marker for the same run no longer blocks the next claim."""
        with tempfile.TemporaryDirectory() as folder:
            self._with_run(folder, "run-1")
            self._marker(folder, "run-1", "started")
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


class HttpsOriginTests(unittest.TestCase):
    """_https_origin rejects URLs that browsers and urlparse parse differently."""

    def test_rejects_url_with_backslash(self):
        self.assertIsNone(org_claim._https_origin("https://example.test\\x/path"))
        self.assertIsNone(org_claim._https_origin("https://example.test/path\\x"))

    def test_rejects_url_with_userinfo(self):
        self.assertIsNone(org_claim._https_origin("https://user@example.test/path"))
        self.assertIsNone(org_claim._https_origin("https://user:pass@example.test/path"))

    def test_rejects_explicit_port_zero(self):
        self.assertIsNone(org_claim._https_origin("https://example.test:0/path"))

    def test_rejects_explicit_non_443_port(self):
        self.assertIsNone(org_claim._https_origin("https://example.test:8443/path"))
        self.assertIsNone(org_claim._https_origin("https://example.test:80/path"))
        self.assertIsNone(org_claim._https_origin("https://example.test:8080/path"))

    def test_accepts_plain_https_url(self):
        self.assertEqual(org_claim._https_origin("https://example.test/path"), ("example.test", 443))
        self.assertEqual(org_claim._https_origin("https://sub.example.test/path"), ("sub.example.test", 443))

    def test_accepts_explicit_port_443(self):
        self.assertEqual(org_claim._https_origin("https://example.test:443/path"), ("example.test", 443))


class PortalBoundaryTests(unittest.TestCase):
    """The browser never leaves the https portal host, redirects included."""

    def test_default_port_443_matches_in_both_directions(self):
        self.assertTrue(org_claim.on_portal("https://portal.example.test:443/x", "https://portal.example.test/home"))
        self.assertTrue(org_claim.on_portal("https://portal.example.test/x", "https://portal.example.test:443/home"))
        self.assertTrue(org_claim.on_portal("https://PORTAL.example.test/x", "https://portal.example.test/home"))

    def test_other_ports_hosts_and_http_are_refused(self):
        portal = "https://portal.example.test/home"
        for url in ("https://portal.example.test:8443/x", "http://portal.example.test/x",
                    "http://portal.example.test:443/x", "https://portal.example.test.other.test/x",
                    "https://sub.portal.example.test/x", "https://portal.example.test:bad/x", ""):
            self.assertFalse(org_claim.on_portal(url, portal), url)
        self.assertFalse(org_claim.on_portal("https://portal.example.test:443/x",
                                             "https://portal.example.test:8443/home"))
        self.assertFalse(org_claim.on_portal("http://portal.example.test/x", "http://portal.example.test/home"))

    def test_a_redirect_that_adds_port_443_does_not_stop_the_form(self):
        page = FakePage(_routes("https://portal.example.test:443/events/new"))
        self.assertEqual(org_claim._open_form(page, PORTAL_URL)["button"], "Submit")

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
            marker_data = json.loads(marker.read_text())
            self.assertEqual((marker_data["last_run"], marker_data["result"]), ("run-7", "CLAIMED"))
            self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

    def test_w098_gate_points_at_new_org_after_successful_claim(self):
        """W-098: after successful claim, the org gate points at the new org."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 0, "last_outcome": FAILED, "last_run": "run-9"})
            page = FakePage(_routes(), on_click=_to_details("https://portal.example.test/done"))
            self.assertEqual(self._claim(root, page), "org-claim: CLAIMED org 1")
            gate = json.loads((root / "gate.json").read_text())
            self.assertEqual(gate["org"], 0)  # automate's 0-based index of org 1

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
            self.assertEqual([(m["last_run"], m["result"]) for m in seen], [("run-8", "started")])
            self.assertEqual(json.loads(marker.read_text())["result"], "started")

    def _five_orgs(self, root, *, frozen=False):
        (root / "verification-orgs.json").write_text(json.dumps(
            {"orgs": [{"username": f"u{i}@example.test", "password": "Synth1!xx"} for i in range(5)]}))
        save_gate(root, {"org": 5, "frozen": frozen, "reason": "", "last_outcome": SUCCESS, "last_run": "run-5"})

    def test_w098_automate_selects_the_claimed_org_after_five_used_orgs(self):
        """The gate holds automate's 0-based index: the next run picks the new username."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._five_orgs(root)
            page = FakePage(_routes(), on_click=_to_details("https://portal.example.test/done"))
            self.assertEqual(self._claim(root, page), "org-claim: CLAIMED org 6")
            index = automate.next_org_index(load_gate(root))
            orgs = automate.load_orgs(root)
            self.assertIsNotNone(index)
            self.assertLess(index, len(orgs))
            self.assertEqual(orgs[index]["username"], "learner.synthetic@example.test")

    def test_w098_a_frozen_gate_stays_frozen_after_a_claim(self):
        """Producer decision: the claim never clears a freeze; --reset-gate does."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self._five_orgs(root, frozen=True)
            page = FakePage(_routes(), on_click=_to_details("https://portal.example.test/done"))
            self.assertEqual(self._claim(root, page), "org-claim: CLAIMED org 6")
            gate = load_gate(root)
            self.assertTrue(gate["frozen"])
            self.assertEqual(gate["org"], 5)

    def test_a_gate_failure_after_the_org_is_saved_never_redeems_the_code_again(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 0, "last_outcome": FAILED, "last_run": "run-4"})
            page = FakePage(_routes(), on_click=_to_details("https://portal.example.test/done"))
            with mock.patch.object(org_claim, "update_gate", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    self._claim(root, page)
            self.assertEqual([o["username"] for o in load_orgs(root)], ["learner.synthetic@example.test"])
            marker = json.loads((root / "team" / "claim-attempt").read_text())
            self.assertEqual(marker["result"], "CLAIMED")
            retry = FakePage(_routes(), on_click=_to_details("https://portal.example.test/done"))
            with self.assertRaises(org_claim.ClaimRefused):
                self._claim(root, retry)
            self.assertEqual((retry.filled, retry.clicked), ([], []))

    def _form_with_old_block(self):
        return {PORTAL_URL: (PORTAL_URL, PORTAL),
                "https://portal.example.test/events/new": ("https://portal.example.test/events/new",
                                                           OLD_BLOCK + FORM)}

    def test_a_claim_saves_the_new_block_not_the_earlier_event(self):
        def submit(page):
            page.url, page.html = "https://portal.example.test/events", OLD_BLOCK + NEW_BLOCK
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 0, "last_outcome": FAILED, "last_run": "run-3"})
            self.assertEqual(self._claim(root, FakePage(self._form_with_old_block(), on_click=submit)),
                             "org-claim: CLAIMED org 1")
            self.assertEqual(load_orgs(root), [{"username": "new.learner@example.test", "password": "NewSynth2!pass"}])

    def test_a_username_already_in_the_org_file_is_never_new(self):
        def submit(page):
            page.url, page.html = "https://portal.example.test/events", \
                OLD_BLOCK.replace("old.learner", "u0") + NEW_BLOCK
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "verification-orgs.json").write_text(json.dumps(
                {"orgs": [{"username": "U0@example.test", "password": "Synth1!xx"}]}))
            save_gate(root, {"org": 1, "last_outcome": SUCCESS, "last_run": "run-3"})
            self.assertEqual(self._claim(root, FakePage(_routes(), on_click=submit)), "org-claim: CLAIMED org 2")
            self.assertEqual(load_orgs(root)[1]["username"], "new.learner@example.test")

    def test_two_new_blocks_after_submit_need_review(self):
        def submit(page):
            page.url, page.html = "https://portal.example.test/events", OLD_BLOCK + NEW_BLOCK + OTHER_NEW_BLOCK
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch("workshop_validator.org_claim.time.monotonic", side_effect=itertools.count(0, 10)):
            root = Path(folder)
            save_gate(root, {"org": 0, "last_outcome": FAILED, "last_run": "run-3"})
            line = self._claim(root, FakePage(self._form_with_old_block(), on_click=submit))
            self.assertEqual(line, "org-claim: NEEDS_REVIEW could not read credentials")
            self.assertFalse((root / "verification-orgs.json").exists())

    def test_only_the_old_block_after_submit_keeps_polling_then_needs_review(self):
        reads = []

        def submit(page):
            page.url, page.html = "https://portal.example.test/events", OLD_BLOCK
            page.on_eval = lambda p: reads.append(1)
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch("workshop_validator.org_claim.time.monotonic", side_effect=itertools.count(0, 10)):
            root = Path(folder)
            save_gate(root, {"org": 0, "last_outcome": FAILED, "last_run": "run-3"})
            line = self._claim(root, FakePage(self._form_with_old_block(), on_click=submit))
            self.assertEqual(line, "org-claim: NEEDS_REVIEW could not read credentials")
            self.assertGreater(len(reads), 1, "the page was read once, not polled")
            self.assertFalse((root / "verification-orgs.json").exists())

    def test_guides_only_from_https_links_on_the_portal_host(self):
        html = PORTAL + '<a href="http://portal.example.test/files/plain.pdf">Plain</a>' \
                        '<a href="/files/moved.pdf">Moved</a>'
        page = FakePage({PORTAL_URL: (PORTAL_URL, html)})
        fetched = []

        def get(url, **kwargs):
            fetched.append(url)
            final = "https://elsewhere.example.test/m.pdf" if url.endswith("moved.pdf") else url
            return mock.Mock(ok=True, status=200, url=final, body=lambda: b"%PDF-1.4 synthetic")
        context = mock.Mock(request=mock.Mock(get=get))
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(org_claim, "_browser", side_effect=lambda run: run(context, page)):
            line = org_claim.guides(Path(folder), {"portal_url": PORTAL_URL})
        self.assertEqual(line, "org-claim: GUIDES 1 (skipped 3 off-portal links)")
        self.assertEqual(fetched, ["https://portal.example.test/files/synthetic-guide.pdf",
                                   "https://portal.example.test/files/moved.pdf"])

    def test_guides_fetches_with_max_redirects_zero(self):
        html = PORTAL + '<a href="/files/second.pdf">Second</a>'
        page = FakePage({PORTAL_URL: (PORTAL_URL, html)})
        kwargs_list = []

        def get(url, **kwargs):
            kwargs_list.append(kwargs)
            return mock.Mock(ok=True, status=200, url=url, body=lambda: b"%PDF-1.4 synthetic")
        context = mock.Mock(request=mock.Mock(get=get))
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(org_claim, "_browser", side_effect=lambda run: run(context, page)):
            org_claim.guides(Path(folder), {"portal_url": PORTAL_URL})
        self.assertEqual(len(kwargs_list), 2)
        for kwargs in kwargs_list:
            self.assertEqual(kwargs.get("max_redirects"), 0)


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

    def test_gate_writes_wait_for_the_shared_gate_lock(self):
        """automate.save_gate and the claim's gate step take the same lock."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 0, "frozen": False, "reason": ""})
            lock = os.open(root / ".gate.json.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                writers = [threading.Thread(target=save_gate, args=(root, {"org": 2, "frozen": False})),
                           threading.Thread(target=org_claim.point_gate_at, args=(root, 4))]
                for writer in writers:
                    writer.start()
                    writer.join(0.5)
                    self.assertTrue(writer.is_alive(), "a gate write did not wait for the lock")
                self.assertEqual(load_gate(root)["org"], 0)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)
            for writer in writers:
                writer.join(5)
            self.assertIn(load_gate(root)["org"], {2, 3})

    def test_gate_json_is_replaced_atomically(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            save_gate(root, {"org": 1, "frozen": False, "reason": ""})
            before = (root / "gate.json").read_text()
            with mock.patch("workshop_validator.automate.os.replace", side_effect=OSError("disk")):
                with self.assertRaises(OSError):
                    save_gate(root, {"org": 2, "frozen": False, "reason": ""})
            self.assertEqual((root / "gate.json").read_text(), before)
            self.assertEqual(sorted(p.name for p in root.iterdir() if not p.name.endswith(".lock")), ["gate.json"])
            self.assertEqual(stat.S_IMODE((root / "gate.json").stat().st_mode), 0o600)

    def test_inbox_folder_is_private_even_when_it_already_existed(self):
        with tempfile.TemporaryDirectory() as folder:
            inbox = Path(folder) / "team" / "guide-inbox"
            inbox.mkdir(parents=True)
            os.chmod(inbox, 0o755)
            org_claim.save_guides(Path(folder), [("https://x.test/a.pdf", b"%PDF-1.4 x")])
            self.assertEqual(stat.S_IMODE(inbox.stat().st_mode), 0o700)


class GuidesResponseTests(unittest.TestCase):
    """Test that --guides counts all non-OK responses (including refused redirects) as skipped."""

    def test_redirect_and_error_responses_counted_as_skipped(self):
        """3xx and 4xx responses with max_redirects=0 are counted in skipped total."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {"portal_url": "https://portal.example.test"}

            class _Response:
                def __init__(self, ok, status, body_bytes=b"", url_str=""):
                    self.ok, self.status, self._body, self.url = ok, status, body_bytes, url_str

                def body(self):
                    return self._body

            class _Request:
                def __init__(self):
                    self.calls = []

                def get(self, url, max_redirects=None):
                    self.calls.append((url, max_redirects))
                    # First link: 404 not found
                    if "missing" in url:
                        return _Response(False, 404, url_str=url)
                    # Second link: 302 redirect (refused because max_redirects=0)
                    if "redirect" in url:
                        return _Response(True, 302, url_str=url)
                    # Third link: OK
                    return _Response(True, 200, b"%PDF-1.4\nfake", url_str=url)

            class _Context:
                def __init__(self):
                    self.request = _Request()

            class _Page:
                def __init__(self, ctx):
                    self.context, self.url = ctx, "https://portal.example.test/home"

            html = """<html><body>
            <a href="https://portal.example.test/missing.pdf">Missing</a>
            <a href="https://portal.example.test/redirect.pdf">Redirect</a>
            <a href="https://portal.example.test/good.pdf">Good</a>
            </body></html>"""

            context = _Context()
            page = _Page(context)

            # Mock the browser interactions
            with mock.patch.object(org_claim, "_browser") as mock_browser:
                def run_browser(func):
                    return func(context, page)
                mock_browser.side_effect = run_browser

                with mock.patch.object(org_claim, "_goto"), \
                     mock.patch.object(org_claim, "_poll", return_value=(
                         ["https://portal.example.test/missing.pdf",
                          "https://portal.example.test/redirect.pdf",
                          "https://portal.example.test/good.pdf"], html)), \
                     mock.patch.object(org_claim, "_require_portal"):
                    result = org_claim.guides(root, config)

            # Verify max_redirects=0 was used
            for _, max_redir in context.request.calls:
                self.assertEqual(max_redir, 0)

            # Verify result: 1 saved, 2 skipped (404 and 302)
            self.assertIn("GUIDES 1", result)
            self.assertIn("skipped 2", result)


if __name__ == "__main__":
    unittest.main()
