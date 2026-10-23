import json
import io
import os
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path

from workshop_validator.guide_inventory import extract_inventory
from workshop_validator.local_site import (
    LocalSiteError,
    LoginResult,
    _progress_log,
    _read_progress,
    _write_progress,
    compose_notice,
    create_app,
    main,
    perform_run,
    salesforce_login_attempt,
)
from contextlib import contextmanager

from workshop_validator.private_runner import PrivateRunnerError, UploadedRun, run_uploaded_guide
from workshop_validator.step_board import render_board
from workshop_validator.report import ReportStep

try:
    from reportlab.pdfgen import canvas
except ImportError:
    canvas = None


def pdf_bytes(*pages):
    fd, path = tempfile.mkstemp(suffix=".pdf")
    os.close(fd)
    try:
        document = canvas.Canvas(path, pagesize=(612, 792), pageCompression=0)
        for lines in pages:
            for index, (x, text) in enumerate(lines):
                document.drawString(x, 690 - index * 25, text)
            document.showPage()
        document.save()
        return Path(path).read_bytes()
    finally:
        os.remove(path)


class LocalSiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.private = self.root / "private"
        self.password = "correct-horse-battery"
        self.username = "reviewer@example.com"

    def test_form_lists_the_fields_and_does_not_store_a_password(self):
        client = create_app(private_root=self.private, repo_root=self.repo).test_client()
        response = client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("Exercise guide PDF", html)
        self.assertIn("Salesforce username", html)
        self.assertIn("type='password'", html)
        self.assertIn("Run validation", html)
        self.assertIn("password is not saved", html)
        self.assertNotIn("value=''", html.split("type='password'", 1)[0][-40:])

    def test_sign_in_failure_leaves_every_guide_step_not_run(self):
        if canvas is None:
            self.skipTest("reportlab required")
        calls = []

        def login(username, password):
            calls.append((username, password))
            return LoginResult(
                True, False, "Salesforce authentication failed; no steps verified"
            )

        response, html = self.post(self.sample_pdf(), login)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [(self.username, self.password)])
        self.assertNotIn(self.password, html)
        self.assertNotIn(self.username, html)
        self.assertIn("The PDF was read", html)
        self.assertIn("Salesforce authentication failed; no steps verified.", html)
        self.assertIn("were not executed", html)
        self.assertIn("Every guide step is NOT RUN", html)
        self.assertIn("not a failure of the guide", html)
        self.assertIn("Steps that were not executed are NOT RUN.", html)
        self.assertIn("<h2>Setup</h2>", html)
        self.assertLess(html.index("<h2>Setup</h2>"), html.index("<h2>Exercise 2</h2>"))
        self.assertNotIn("class='badge pass'", html)
        self.assertNotIn("class='badge fail'", html)
        self.assertIn("class='badge not-run'", html)
        self.assertEqual(list(self.private.rglob("*.pdf")), [])
        self.assertNotIn("VERIFIED", html)

    def test_finished_report_uses_the_same_board(self):
        if canvas is None:
            self.skipTest("reportlab required")
        source = self.agent_pdf()
        preview = self.preview_inventory(source)
        ids = [item.step_id for item in preview.steps]
        self.assertGreaterEqual(len(ids), 3)
        seen = {}

        def login(username, password):
            seen["password"] = password
            return LoginResult(True, True, "Signed in to the trial org.")

        def extract(path):
            seen["mode"] = os.stat(path).st_mode & 0o777
            seen["dir_mode"] = os.stat(Path(path).parent).st_mode & 0o777
            seen["name"] = Path(path).name
            self.assertNotIn(self.repo.resolve(), Path(path).resolve().parents)
            return extract_inventory(path)

        board = perform_run(
            source, self.username, self.password,
            repo_root=self.repo, private_root=self.private, login=login, extract=extract,
            report_steps=(
                ReportStep(ids[0], 1, "Preview", "PASS", ("evidence-1",)),
                ReportStep(
                    ids[1], 1, "Welcome", "FUNCTIONAL_FAIL", (),
                    "Live Test shows Let's chat, not the saved welcome.",
                ),
            ),
        )
        app = create_app(private_root=self.private, repo_root=self.repo, login=login)
        app.config["runs"].put(board)
        response = app.test_client().get(f"/runs/{board.run_id}")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen["password"], self.password)
        self.assertEqual(seen["mode"], 0o600)
        self.assertEqual(seen["dir_mode"], 0o700)
        self.assertEqual(seen["name"], "guide.pdf")
        self.assertNotIn(self.password, html)
        self.assertNotIn("Every guide step is NOT RUN", html)
        self.assertIn("Exercise 1-2, Task 3: Test your agent:", html)
        welcome = html.index("Welcome message is shown")
        reason = html.index("Live Test shows Let&#x27;s chat, not the saved welcome.")
        reply = html.index("Reply with Hello there!")
        self.assertLess(html.index("Select Preview"), welcome)
        self.assertLess(welcome, reason)
        self.assertLess(reason, reply)
        self.assertIn("data-status='NOT RUN'", html)
        self.assertIn("<article class='task fail' data-status='FAIL'>", html)
        self.assertEqual(list(self.private.rglob("*.pdf")), [])

    def test_login_message_cannot_carry_the_password_onto_the_page(self):
        if canvas is None:
            self.skipTest("reportlab required")

        def login(username, password):
            return LoginResult(True, False, f"rejected {password}")

        _response, html = self.post(self.sample_pdf(), login)
        self.assertNotIn(self.password, html)
        self.assertIn("Sign-in could not finish.", html)

    def test_password_inside_the_guide_is_not_rendered_or_sent_to_login(self):
        if canvas is None:
            self.skipTest("reportlab required")
        calls = []

        def login(username, password):
            calls.append(password)
            return LoginResult(True, True, "Signed in")

        pdf = pdf_bytes([
            (72, "Exercise 1: Sample"),
            (72, "Task 1: Start"),
            (90, f"1. Type {self.password} into the field"),
        ])
        response, html = self.post(pdf, login)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])
        self.assertNotIn(self.password, html)
        self.assertIn("could not be completed", html)
        self.assertEqual(list(self.private.rglob("*.pdf")), [])

    def test_non_pdf_and_bad_csrf_do_not_start_a_run(self):
        calls = []

        def login(username, password):
            calls.append(password)
            return LoginResult(True, True, "Signed in")

        client = self.client(login)
        token = self.token(client)
        rejected = client.post("/run", data={
            "csrf_token": token,
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(b"not a pdf"), "notes.pdf"),
            "no_ai_team": "1",
        })
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("Choose a PDF file.", rejected.get_data(as_text=True))
        expired = client.post("/run", data={
            "csrf_token": "not-the-token",
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(b"%PDF-1.4"), "guide.pdf"),
            "no_ai_team": "1",
        })
        self.assertEqual(expired.status_code, 400)
        self.assertIn("form expired", expired.get_data(as_text=True))
        self.assertEqual(calls, [])
        self.assertNotIn(self.password, rejected.get_data(as_text=True))
        self.assertNotIn(self.password, expired.get_data(as_text=True))

    def test_storage_inside_the_repo_is_refused(self):
        if canvas is None:
            self.skipTest("reportlab required")

        def login(username, password):
            raise AssertionError("login should not run")

        with self.assertRaises(LocalSiteError):
            perform_run(
                self.sample_pdf(), self.username, self.password,
                repo_root=self.repo, private_root=self.repo / "inside", login=login,
            )
        self.assertEqual(list(self.repo.rglob("*.pdf")), [])

    def test_sign_in_helper_hides_unexpected_errors(self):
        def explode(username, password):
            raise RuntimeError(password)

        result = salesforce_login_attempt(
            self.username, self.password, connect=explode
        )
        self.assertNotIn(self.password, result.message)
        self.assertEqual(result.message, "Sign-in could not finish.")
        self.assertFalse(result.signed_in)

        def blocked(username, password):
            raise PrivateRunnerError(
                "Salesforce login timed out or requires interactive authentication"
            )

        blocked_result = salesforce_login_attempt(
            self.username, self.password, connect=blocked
        )
        self.assertIn("timed out", blocked_result.message)
        self.assertTrue(blocked_result.attempted)

        def missing(username, password):
            raise ImportError("playwright")

        skipped = salesforce_login_attempt("user", "secret", connect=missing)
        self.assertFalse(skipped.attempted)
        self.assertIn("Playwright is not installed", skipped.message)

    def test_unknown_run_page_does_not_echo_the_address(self):
        client = create_app(private_root=self.private, repo_root=self.repo).test_client()
        missing = client.get("/runs/" + ("ab" * 16))
        reflected = client.get("/runs/%3Cscript%3Ealert(1)%3C/script%3E")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(reflected.status_code, 404)
        body = reflected.get_data(as_text=True)
        self.assertIn("That page is not available.", body)
        self.assertNotIn("alert(1)", body)
        self.assertEqual(body.lower().count("<script"), 1)

    def test_site_refuses_to_listen_beyond_localhost(self):
        def boom(app, port):
            raise AssertionError("should not bind")

        self.assertEqual(main(["--host", "0.0.0.0"], serve=boom), 2)
        self.assertEqual(main(["--port", "0"], serve=boom), 2)
        seen = {}

        def spy(app, port):
            seen["port"] = port

        self.assertEqual(main(["--port", "8766"], serve=spy), 0)
        self.assertEqual(seen["port"], 8766)

    def test_notice_for_an_unexecuted_run_does_not_claim_passes(self):
        from workshop_validator.guide_inventory import GuideInventory

        inventory = GuideInventory(1, (), (), ("check the table",))
        text = compose_notice(
            LoginResult(True, True, "Signed in to the trial org."),
            inventory,
            executed=False,
        )
        self.assertIn("The PDF was read", text)
        self.assertIn("were not executed", text)
        self.assertIn("Every guide step is NOT RUN", text)
        self.assertIn("1 extraction warning", text)
        executed = compose_notice(
            LoginResult(True, True, "Signed in to the trial org."),
            GuideInventory(1, (), (), ()),
            executed=True,
        )
        self.assertIn("The PDF was read", executed)
        self.assertIn("These statuses come from the validator.", executed)
        self.assertNotIn("Every guide step is NOT RUN", executed)

    def test_uploaded_run_reports_not_run_without_a_fake_pass(self):
        if canvas is None:
            self.skipTest("reportlab required")
        seen = {}

        def validate(username, password, inventory, **kwargs):
            seen["password"] = password
            seen["count"] = len(inventory.steps)
            return UploadedRun(True, True, "Signed in to the trial org.", (), 0)

        def login(username, password):
            raise AssertionError("direct login should not run")

        board = perform_run(
            self.sample_pdf(), self.username, self.password,
            repo_root=self.repo, private_root=self.private, login=login, validate=validate,
        )
        page = render_board(board)
        self.assertEqual(seen["password"], self.password)
        self.assertGreater(seen["count"], 0)
        self.assertIn("The PDF was read", page)
        self.assertIn("The validator ran after sign-in.", page)
        self.assertIn("were not executed", page)
        self.assertIn("Every guide step is NOT RUN", page)
        self.assertNotIn("class='badge pass'", page)
        self.assertNotIn("class='badge fail'", page)
        self.assertNotIn(self.password, page)
        self.assertNotIn(self.username, page)

    def test_sign_in_failure_from_the_validator_is_not_a_guide_fail(self):
        if canvas is None:
            self.skipTest("reportlab required")

        def validate(username, password, inventory, **kwargs):
            return UploadedRun(
                True, False, "Salesforce login timed out or requires interactive authentication",
                tuple(
                    ReportStep(step.step_id, step.page, step.number, "FUNCTIONAL_FAIL", (), "bad")
                    for step in inventory.steps
                ),
                len(inventory.steps),
            )

        _response, html = self.post_validate(self.sample_pdf(), validate)
        self.assertIn("timed out", html)
        self.assertIn("not a failure of the guide", html)
        self.assertIn("Every guide step is NOT RUN", html)
        self.assertNotIn("class='badge fail'", html)
        self.assertNotIn("class='badge pass'", html)
        self.assertNotIn(self.password, html)

    def test_executed_check_stays_on_its_step(self):
        if canvas is None:
            self.skipTest("reportlab required")

        def validate(username, password, inventory, **kwargs):
            first = inventory.steps[0]
            return UploadedRun(
                True, True, "Signed in to the trial org.",
                (ReportStep(first.step_id, first.page, first.text, "PASS", ("evidence-1",)),),
                1,
            )

        _response, html = self.post_validate(self.sample_pdf(), validate)
        self.assertIn("These statuses come from the validator.", html)
        self.assertIn("class='badge pass'", html)
        self.assertIn("data-status='NOT RUN'", html)
        self.assertNotIn("Every guide step is NOT RUN", html)
        self.assertNotIn(self.password, html)
        self.assertNotIn(self.username, html)

    def test_background_upload_signs_in_and_records_the_login_step(self):
        if canvas is None:
            self.skipTest("reportlab required")
        seen = {}

        @contextmanager
        def session(username, password):
            seen["username"] = username
            seen["password"] = password
            page = type("Page", (), {
                "url": "https://example.lightning.force.com/lightning/page/home",
            })()
            yield page

        def validate(username, password, inventory, **kwargs):
            kwargs.pop("walk_timeout", None)
            return run_uploaded_guide(
                username, password, inventory, session=session,
                page_wait_s=0, **kwargs,
            )

        app = create_app(
            validate=validate, background=True,
            private_root=self.private, repo_root=self.repo,
        )
        client = app.test_client()
        pdf = pdf_bytes([
            (72, "Set Up Your Instance"),
            (72, "Task 2: Log in to your org."),
            (90, "1. Alternatively, navigate to login.salesforce.com and log in."),
            (90, "2. Open the setup page"),
            (90, "3. Click Save."),
        ])
        response = client.post("/run", data={
            "csrf_token": self.token(client),
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(pdf), "guide.pdf"),
            "no_ai_team": "1",
        })
        self.assertEqual(response.status_code, 303)
        finished = ""
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            finished = client.get(response.headers["Location"]).get_data(as_text=True)
            if "class='badge pass'" in finished:
                break
            time.sleep(0.02)
        self.assertNotIn("could not be completed", finished)
        self.assertNotIn(self.password, finished)
        self.assertNotIn(self.username, finished)
        self.assertEqual(seen["password"], self.password)
        self.assertIn("These statuses come from the validator.", finished)
        self.assertIn("class='badge pass'", finished)
        self.assertIn("data-status='NOT RUN'", finished)
        self.assertIn("no action for this step yet: Open the setup page", finished)
        titles = re.findall(r"<span class='text'>(.*?)</span>", finished)
        self.assertEqual(
            titles,
            [
                "Alternatively, navigate to login.salesforce.com and log in.",
                "Open the setup page",
                "Click Save.",
            ],
        )

    def test_background_run_shows_progress_before_the_board(self):
        if canvas is None:
            self.skipTest("reportlab required")
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def validate(username, password, inventory, **kwargs):
            progress = kwargs.get("progress")
            if progress:
                progress(
                    "signing_in",
                    "Signing in at login.salesforce.com. Sign-in is limited to about two minutes.",
                )
            started.set()
            release.wait(5)
            return UploadedRun(True, True, "Signed in to the trial org.", (), 0)

        app = create_app(
            validate=validate, background=True,
            private_root=self.private, repo_root=self.repo,
        )
        client = app.test_client()
        token = self.token(client)
        response = client.post("/run", data={
            "csrf_token": token,
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(self.sample_pdf()), "guide.pdf"),
            "no_ai_team": "1",
        })
        self.assertEqual(response.status_code, 303)
        self.assertTrue(started.wait(5))
        progress = client.get(response.headers["Location"])
        body = progress.get_data(as_text=True)
        self.assertEqual(progress.status_code, 200)
        self.assertIn("Signing in at login.salesforce.com", body)
        self.assertIn("ol class='progress'", body)
        self.assertIn("not in the browser", body)
        self.assertIn("updates every few seconds", body)
        self.assertIn("http-equiv='refresh'", body)
        self.assertNotIn("class='badge pass'", body)
        self.assertNotIn("Do the later step", body)
        self.assertNotIn(self.password, body)
        self.assertNotIn(self.username, body)
        release.set()
        finished = ""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            finished = client.get(response.headers["Location"]).get_data(as_text=True)
            if "The validator ran after sign-in." in finished:
                break
            time.sleep(0.02)
        self.assertIn("The PDF was read", finished)
        self.assertIn("Every guide step is NOT RUN", finished)
        self.assertNotIn("class='badge pass'", finished)
        self.assertNotIn("class='badge fail'", finished)
        self.assertNotIn(self.password, finished)

    def test_progress_log_and_file_keep_every_line(self):
        log = _progress_log((), "Reading the exercise guide PDF.")
        log = _progress_log(log, "Reading the exercise guide PDF.")
        for number in range(1, 101):
            log = _progress_log(log, f"Step {number} of 200: Action {number}")
        check = "Checked at 30 seconds. The page is still loading."
        log = _progress_log(log, check)
        log = _progress_log(log, check)
        self.assertEqual(log[0], "Reading the exercise guide PDF.")
        self.assertEqual(log[1], "Step 1 of 200: Action 1")
        self.assertEqual(log[-1], check)
        self.assertEqual(log.count(check), 2)
        self.assertGreater(len(log), 80)
        self.private.mkdir()
        folder = self.private / "kept-run"
        _write_progress(folder, log)
        self.assertEqual(_read_progress(folder), log)
        saved = (folder / "progress.txt").read_text(encoding="utf-8")
        self.assertGreater(len(saved.splitlines()), 80)
        self.assertTrue(
            saved.startswith("Reading the exercise guide PDF.\nStep 1 of 200: Action 1\n")
        )
        self.assertEqual((folder / "progress.txt").stat().st_mode & 0o777, 0o600)

    def test_results_page_keeps_more_than_eighty_progress_lines(self):
        if canvas is None:
            self.skipTest("reportlab required")
        started = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        freeze = "The run froze after 5 minutes. Action 90 was not on the page."

        def validate(username, password, inventory, **kwargs):
            progress = kwargs["progress"]
            for number in range(1, 91):
                progress("running", f"Step {number} of 120: Action {number}")
            progress("running", "Checked at 30 seconds. The page is still loading.")
            progress("running", "Checked at 60 seconds. Still looking for Action 90.")
            progress("running", freeze)
            started.set()
            release.wait(5)
            return UploadedRun(True, True, "Signed in to the trial org.", (), 0)

        app = create_app(
            validate=validate, background=True,
            private_root=self.private, repo_root=self.repo,
        )
        client = app.test_client()
        response = client.post("/run", data={
            "csrf_token": self.token(client),
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(self.sample_pdf()), "guide.pdf"),
            "no_ai_team": "1",
        })
        self.assertEqual(response.status_code, 303)
        self.assertTrue(started.wait(5))
        location = response.headers["Location"]
        run_id = location.rstrip("/").rsplit("/", 1)[-1]
        saved_lines = self._progress_lines(run_id)
        self.assertEqual(saved_lines[0], "Reading the exercise guide PDF.")
        self.assertEqual(saved_lines[1], "Step 1 of 120: Action 1")
        self.assertEqual(saved_lines[-1], freeze)
        self.assertEqual(len(saved_lines), 94)
        self.assertIn("Checked at 30 seconds. The page is still loading.", saved_lines)
        self.assertIn("Checked at 60 seconds. Still looking for Action 90.", saved_lines)
        self.assertLess(
            saved_lines.index("Step 1 of 120: Action 1"),
            saved_lines.index("Step 90 of 120: Action 90"),
        )

        progress = client.get(location)
        body = progress.get_data(as_text=True)
        self.assertEqual(progress.status_code, 200)
        self.assertIn("http-equiv='refresh'", body)
        self.assertIn("The run is still going.", body)
        self.assertIn("not in the browser", body)
        self.assertNotIn("Do the later step", body)
        self._assert_progress_lead(body)

        release.set()
        finished = ""
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            finished = client.get(location).get_data(as_text=True)
            if "The PDF was read" in finished:
                break
            time.sleep(0.02)
        self.assertIn("The PDF was read", finished)
        self.assertNotIn("http-equiv='refresh'", finished)
        self._assert_progress_lead(finished)
        self.assertLess(finished.index(freeze), finished.index("The PDF was read"))
        self.assertLess(finished.index(freeze), finished.index(">Passed<"))
        self.assertNotIn(self.password, finished)
        self.assertNotIn(self.username, finished)
        self.assertEqual(len(self._progress_lines(run_id)), 94)

    def _progress_lines(self, run_id: str) -> list[str]:
        saved = (self.private / run_id / "progress.txt").read_text(encoding="utf-8")
        return [line for line in saved.splitlines() if line.strip()]

    def _assert_progress_lead(self, body: str) -> None:
        freeze = "The run froze after 5 minutes. Action 90 was not on the page."
        self.assertEqual(body.count(" of 120: "), 90)
        self.assertEqual(body.count("Step 1 of 120: Action 1"), 1)
        self.assertEqual(body.count("Step 13 of 120: Action 13"), 1)
        self.assertEqual(body.count("Step 90 of 120: Action 90"), 1)
        self.assertEqual(body.count("The run froze after 5 minutes."), 1)
        self.assertLess(body.index("class='freeze'"), body.index(freeze))
        self.assertLess(body.index(freeze), body.index("class='current-step'"))
        self.assertLess(body.index("class='current-step'"), body.index(">Checks<"))
        self.assertLess(body.index(">Checks<"), body.index("class='check'"))
        self.assertLess(body.index("class='check'"), body.index("Checked at 30 seconds"))
        self.assertLess(
            body.index("Checked at 30 seconds"),
            body.index("Checked at 60 seconds"),
        )
        self.assertLess(body.index("Checked at 60 seconds"), body.index(">From the start<"))
        self.assertLess(body.index(">From the start<"), body.index("Step 1 of 120: Action 1"))
        self.assertLess(
            body.index("Step 1 of 120: Action 1"),
            body.index("Step 13 of 120: Action 13"),
        )
        self.assertIn("background: #e7f5ef", body)
        self.assertNotIn(self.password, body)
        self.assertNotIn(self.username, body)

    def client(self, login):
        return create_app(
            login=login, private_root=self.private, repo_root=self.repo
        ).test_client()

    def token(self, client):
        html = client.get("/").get_data(as_text=True)
        match = re.search(r"name='csrf_token' value='([^']+)'", html)
        self.assertIsNotNone(match)
        return match.group(1)

    def post_validate(self, pdf, validate):
        client = create_app(
            validate=validate, private_root=self.private, repo_root=self.repo,
        ).test_client()
        response = client.post("/run", data={
            "csrf_token": self.token(client),
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(pdf), "guide.pdf"),
            "no_ai_team": "1",
        }, follow_redirects=True)
        return response, response.get_data(as_text=True)

    def post(self, pdf, login):
        client = self.client(login)
        response = client.post("/run", data={
            "csrf_token": self.token(client),
            "username": self.username,
            "password": self.password,
            "guide": (io.BytesIO(pdf), "../../secret-org.pdf"),
            "no_ai_team": "1",
        }, follow_redirects=True)
        return response, response.get_data(as_text=True)

    def sample_pdf(self):
        return pdf_bytes(
            [
                (72, "Exercise 2: Later work"),
                (72, "Task 1: Second"),
                (90, "1. Do the later step"),
            ],
            [
                (72, "Set Up Your Instance"),
                (72, "Task 1: Prepare the org"),
                (90, "1. Open the setup page"),
            ],
        )

    def agent_pdf(self):
        return pdf_bytes([
            (72, "Exercise 1-2: Check the agent"),
            (72, "Task 3: Test your agent"),
            (90, "1. Select Preview"),
            (90, "2. Welcome message is shown"),
            (90, "3. Reply with Hello there!"),
        ])

    def preview_inventory(self, pdf):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        destination = Path(path)
        try:
            destination.write_bytes(pdf)
            return extract_inventory(destination)
        finally:
            destination.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()


class TeamIntegrationTests(unittest.TestCase):
    """Tests for AI team integration in local site."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.private = self.root / "private"
        self.password = "team-horse-battery"

    def test_form_has_usernames_textarea_and_mode_radio(self):
        client = create_app(private_root=self.private, repo_root=self.repo).test_client()
        response = client.get("/")
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Salesforce usernames (1-5, one per line)", html)
        self.assertIn("<textarea", html)
        self.assertIn('name="usernames"', html)
        self.assertIn('name="mode"', html)
        self.assertIn('value="quick"', html)
        self.assertIn('value="full"', html)
        self.assertIn("Quick — verify failures", html)
        self.assertIn("Full AI review", html)
        self.assertIn('name="no_ai_team"', html)
        self.assertIn("Use saved orgs", html)

    def test_mode_default_is_quick(self):
        client = create_app(private_root=self.private, repo_root=self.repo).test_client()
        response = client.get("/")
        html = response.get_data(as_text=True)
        self.assertIn('value="quick" checked', html)

    def test_org_file_written_with_0600_permissions(self):
        if canvas is None:
            self.skipTest("reportlab required")
        
        launched = []
        def mock_launcher(repo_root, pdf_path, mode):
            launched.append((repo_root, pdf_path, mode))
        
        client = create_app(
            private_root=self.private,
            repo_root=self.repo,
            team_launcher=mock_launcher,
        ).test_client()
        
        with client.session_transaction() as sess:
            sess["csrf"] = "test-token-16chars"
        
        pdf = pdf_bytes([(50, "1. Setup step one")])
        response = client.post(
            "/run",
            data={
                "csrf_token": "test-token-16chars",
                "guide": (io.BytesIO(pdf), "guide.pdf"),
                "usernames": "user1@example.com\nuser2@example.com",
                "password": self.password,
                "mode": "quick",
            },
            content_type="multipart/form-data",
            follow_redirects=False,
        )
        
        self.assertEqual(response.status_code, 303)
        orgs_file = self.private / "verification-orgs.json"
        self.assertTrue(orgs_file.is_file())
        self.assertEqual(oct(orgs_file.stat().st_mode)[-3:], "600")
        
        data = json.loads(orgs_file.read_text(encoding="utf-8"))
        self.assertEqual(len(data["orgs"]), 2)
        self.assertEqual(data["orgs"][0]["username"], "user1@example.com")
        self.assertEqual(data["orgs"][0]["password"], self.password)
        self.assertEqual(data["orgs"][1]["username"], "user2@example.com")
        self.assertEqual(data["orgs"][1]["password"], self.password)

    def test_gate_reset_only_when_list_changes(self):
        if canvas is None:
            self.skipTest("reportlab required")
        
        launched = []
        def mock_launcher(repo_root, pdf_path, mode):
            launched.append((repo_root, pdf_path, mode))
        
        client = create_app(
            private_root=self.private,
            repo_root=self.repo,
            team_launcher=mock_launcher,
        ).test_client()
        
        with client.session_transaction() as sess:
            sess["csrf"] = "test-token-16chars"
        
        pdf = pdf_bytes([(50, "1. Setup step one")])
        
        # First submission
        client.post(
            "/run",
            data={
                "csrf_token": "test-token-16chars",
                "guide": (io.BytesIO(pdf), "guide.pdf"),
                "usernames": "user1@example.com",
                "password": self.password,
                "mode": "quick",
            },
            content_type="multipart/form-data",
        )
        
        gate_file = self.private / "gate.json"
        self.assertTrue(gate_file.is_file())
        gate = json.loads(gate_file.read_text(encoding="utf-8"))
        self.assertEqual(gate["org"], 0)
        
        # Modify gate
        gate["org"] = 2
        gate_file.write_text(json.dumps(gate, indent=2), encoding="utf-8")
        
        # Second submission with SAME usernames - gate should NOT reset
        client.post(
            "/run",
            data={
                "csrf_token": "test-token-16chars",
                "guide": (io.BytesIO(pdf), "guide.pdf"),
                "usernames": "user1@example.com",
                "password": self.password,
                "mode": "full",
            },
            content_type="multipart/form-data",
        )
        
        gate = json.loads(gate_file.read_text(encoding="utf-8"))
        self.assertEqual(gate["org"], 2, "Gate should not reset when org list unchanged")
        
        # Third submission with DIFFERENT usernames - gate SHOULD reset
        client.post(
            "/run",
            data={
                "csrf_token": "test-token-16chars",
                "guide": (io.BytesIO(pdf), "guide.pdf"),
                "usernames": "user2@example.com",
                "password": self.password,
                "mode": "quick",
            },
            content_type="multipart/form-data",
        )
        
        gate = json.loads(gate_file.read_text(encoding="utf-8"))
        self.assertEqual(gate["org"], 0, "Gate should reset when org list changes")

    def test_launcher_argv_has_no_password_and_includes_mode(self):
        if canvas is None:
            self.skipTest("reportlab required")
        
        launched = []
        def mock_launcher(repo_root, pdf_path, mode):
            launched.append((str(repo_root), str(pdf_path), mode))
        
        client = create_app(
            private_root=self.private,
            repo_root=self.repo,
            team_launcher=mock_launcher,
        ).test_client()
        
        with client.session_transaction() as sess:
            sess["csrf"] = "test-token-16chars"
        
        pdf = pdf_bytes([(50, "1. Setup step one")])
        response = client.post(
            "/run",
            data={
                "csrf_token": "test-token-16chars",
                "guide": (io.BytesIO(pdf), "guide.pdf"),
                "usernames": "user@example.com",
                "password": "secret-password-123",
                "mode": "full",
            },
            content_type="multipart/form-data",
        )
        
        self.assertEqual(len(launched), 1)
        repo_root_arg, pdf_path_arg, mode_arg = launched[0]
        
        self.assertEqual(str(self.repo), repo_root_arg)
        self.assertIn("uploads/", pdf_path_arg)
        self.assertIn(".pdf", pdf_path_arg)
        self.assertEqual("full", mode_arg)
        
        # Verify password not in any part
        combined = f"{repo_root_arg} {pdf_path_arg} {mode_arg}"
        self.assertNotIn("secret-password-123", combined)

    def test_password_absent_from_response(self):
        if canvas is None:
            self.skipTest("reportlab required")
        
        launched = []
        def mock_launcher(repo_root, pdf_path, mode):
            launched.append((repo_root, pdf_path, mode))
        
        client = create_app(
            private_root=self.private,
            repo_root=self.repo,
            team_launcher=mock_launcher,
        ).test_client()
        
        with client.session_transaction() as sess:
            sess["csrf"] = "test-token-16chars"
        
        pdf = pdf_bytes([(50, "1. Setup step one")])
        response = client.post(
            "/run",
            data={
                "csrf_token": "test-token-16chars",
                "guide": (io.BytesIO(pdf), "guide.pdf"),
                "usernames": "user@example.com",
                "password": "my-secret-password",
                "mode": "quick",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        
        html = response.get_data(as_text=True)
        self.assertNotIn("my-secret-password", html)

    def test_team_status_page_shows_stalled_banner(self):
        from workshop_validator.team_state import start_job
        start_job(self.private, "/tmp/guide.pdf", "quick")
        
        # Write stalled marker
        stalled_path = self.private / "team" / "stalled.json"
        stalled_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        stalled_path.write_text(json.dumps({"reason": "Too many restarts"}), encoding="utf-8")
        stalled_path.chmod(0o600)
        
        client = create_app(private_root=self.private, repo_root=self.repo).test_client()
        response = client.get("/team")
        html = response.get_data(as_text=True)
        
        self.assertIn("Team stalled", html)
        self.assertIn("Too many restarts", html)
