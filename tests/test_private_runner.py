from contextlib import contextmanager
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop_validator.browser_worker import UIAssertion
from workshop_validator.evidence import AssertionSpec, Observation
from workshop_validator.guide_inventory import GuideInventory, GuideStep
from workshop_validator.pipeline import PlannedStep
from workshop_validator.guide_walk import CheckResult, GuideCheck
from workshop_validator.private_runner import (
    PageStillLoading, PrivateRunnerError, executable_plans, load_plan,
    load_source_review, login_to_salesforce, playwright_session,
    refuse_unsettled, run_uploaded_guide)
import inspect


class PrivatePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=os.path.join(self.tmp.name,'plan.json')

    def write(self, guide_sha256='a'*64, steps=None):
        data={"schema_version":1,"guide_sha256":guide_sha256,
              "steps":steps if steps is not None else [{
                "step_id":"S001","page":1,"title":"Synthetic save",
                "actions":[{"kind":"click","role":"button","accessible_name":"Save"}],
                "assertions":[{"assertion_id":"visible","kind":"DOM_VISIBLE",
                               "expected":True,"role":"status",
                               "accessible_name":"Save status"}]
              }]}
        with open(self.path,'w') as f:json.dump(data,f)

    def test_parse_pinned_guide_and_source_anchors(self):
        self.write()
        steps=load_plan(self.path,pdf_digest='a'*64,page_count=4)
        self.assertEqual(steps[0].step_id,'S001')
        self.assertEqual(steps[0].actions[0].kind,'click')
        self.assertTrue(steps[0].assertions[0].spec.expected)

    def test_wrong_guide_hash_is_rejected(self):
        self.write()
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='b'*64,page_count=4)

    def test_invalid_page_and_unsafe_action_rejected(self):
        self.write(steps=[{"step_id":"S01","page":99,"title":"Example",
             "actions":[{"kind":"javascript","role":"button","accessible_name":"Save"}],
             "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='a'*64,page_count=4)
        self.write(steps=[{"step_id":"S01","page":1,"title":"Example",
             "actions":[{"kind":"javascript","role":"button","accessible_name":"Save"}],
             "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest='a'*64,page_count=4)



    def test_inline_note_placement_uses_private_explicit_anchors(self):
        from workshop_validator.private_runner import build_inline_note_placements
        from workshop_validator.pdf_annotations import ReviewNote
        self.write(steps=[{"step_id":"S001","page":1,
            "title":"Synthetic instruction", "inline_after_y":140,
            "actions":[{"kind":"click","role":"button","accessible_name":"Save"}],
            "assertions":[{"assertion_id":"ok","kind":"DOM_VISIBLE","expected":True,
                           "role":"status","accessible_name":"Status"}]}])
        note=ReviewNote(1,"S001","NEEDS_REVIEW","Synthetic step","Needs testing")
        placements=build_inline_note_placements(self.path,[note])
        self.assertEqual(len(placements),1)
        self.assertEqual(placements[0].insert_after_y,140)
        self.assertIn("behavioral verification",placements[0].summary)

    def test_missing_inline_anchor_is_rejected_not_placed_elsewhere(self):
        from workshop_validator.private_runner import build_inline_note_placements
        from workshop_validator.pdf_annotations import ReviewNote
        self.write()
        note=ReviewNote(1,"S001","NEEDS_REVIEW","Step","Missing evidence")
        with self.assertRaises(PrivateRunnerError):
            build_inline_note_placements(self.path,[note])

    def test_malformed_numeric_source_anchor_is_rejected(self):
        self.write(steps=[{"step_id":"S001","page":1,"title":"Step",
            "inline_after_y":"not-a-coordinate", "actions":[], "assertions":[]}])
        with self.assertRaises(PrivateRunnerError):
            load_plan(self.path,pdf_digest="a"*64,page_count=4)

class _Loc:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def fill(self, value, timeout=None):
        self.page.events.append(("fill", self.selector))
        if self.selector == "#password" and not self.is_visible():
            raise AssertionError("filled a hidden password field")

    def click(self, timeout=None):
        self.page.events.append(("click", self.selector))
        if self.selector == "#Login" and not self.page.password_visible:
            self.page.password_visible = True

    def count(self):
        if self.selector == "#password":
            return 1 if self.page.password_visible or self.page.combined_form else 0
        if self.selector == "#username":
            return 0
        return 1

    def is_visible(self):
        if self.selector == "#password":
            return self.page.password_visible or self.page.combined_form
        return True

    def wait_for(self, state=None, timeout=None):
        if self.selector == "#password" and not self.is_visible():
            raise TimeoutError("password field did not appear")


class _Page:
    def __init__(self, combined_form=False, promo_blocked=False):
        self.events = []
        self.password_visible = False
        self.combined_form = combined_form
        self.promo_blocked = promo_blocked
        self.promo_ignored = False
        self.url = "https://example.lightning.force.com/lightning/page/home"

    def route(self, pattern, handler=None, **kwargs):
        text = pattern if isinstance(pattern, str) else getattr(pattern, "pattern", "")
        if "promos.html" in str(text).replace("\\", "").lower():
            self.promo_ignored = True

    def goto(self, url, wait_until=None, timeout=None):
        self.events.append(("goto", url))
        if self.promo_blocked and not self.promo_ignored:
            raise RuntimeError(
                "net::ERR_ABORTED https://login.salesforce.com/promos.html"
            )

    def locator(self, selector):
        return _Loc(self, selector)

    def wait_for_url(self, predicate, timeout=None):
        if not predicate(self.url):
            raise AssertionError("still on the login host")


class LoginAdapterTests(unittest.TestCase):
    def test_username_first_form_clicks_before_password(self):
        page = _Page(combined_form=False)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])

    def test_combined_form_does_not_submit_username_alone(self):
        page = _Page(combined_form=True)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])

    def test_missing_secret_fails_before_navigation(self):
        page = _Page()
        with self.assertRaises(PrivateRunnerError):
            login_to_salesforce(page, "", "secret-value")
        self.assertEqual(page.events, [])

    def test_blocked_promo_iframe_does_not_fail_login(self):
        page = _Page(promo_blocked=True)
        login_to_salesforce(page, "learner@example.com", "secret-value")
        self.assertTrue(page.promo_ignored)
        self.assertEqual(page.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("click", "#Login"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertEqual(self._password_submits(page), [("click", "#Login")])
        self.assertEqual(
            [event for event in page.events if event[0] == "goto"],
            [("goto", "https://login.salesforce.com/")],
        )
        self.assertFalse(any(event[0] == "reload" for event in page.events))

    def test_password_login_is_submitted_once(self):
        username_first = _Page(combined_form=False)
        login_to_salesforce(username_first, "learner@example.com", "secret-value")
        self.assertEqual(self._password_submits(username_first), [("click", "#Login")])
        combined = _Page(combined_form=True)
        login_to_salesforce(combined, "learner@example.com", "secret-value")
        self.assertEqual(combined.events, [
            ("goto", "https://login.salesforce.com/"),
            ("fill", "#username"),
            ("fill", "#password"),
            ("click", "#Login"),
        ])
        self.assertEqual(self._password_submits(combined), [("click", "#Login")])

    @staticmethod
    def _password_submits(page):
        filled = page.events.index(("fill", "#password"))
        return [event for event in page.events[filled:] if event == ("click", "#Login")]


def guide_step(step_id, number="1", text="Open the page"):
    return GuideStep(
        step_id, 1, "Exercise 1", "Task 1: Start", number, text,
        (90.0, 10.0, 200.0, 24.0), None, (),
    )


def one_plan(step_id="P001N001"):
    return PlannedStep(
        step_id, 1, "Open the page", (),
        (UIAssertion(AssertionSpec("visible", "DOM_VISIBLE", True), "button", "Save"),),
    )


class UploadedGuideTests(unittest.TestCase):
    def test_no_authored_check_stays_not_run_after_sign_in(self):
        calls = []

        def login(username, password):
            calls.append((username, password))

        inventory = GuideInventory(1, (guide_step("P001N001"), guide_step("P001N002", "2", "Save")), (), ())
        folder = Path(self.tmp.name) / "report"
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=login, output_dir=folder, login_budget=5,
        )
        self.assertEqual(calls, [("learner@example.com", "secret-value")])
        self.assertTrue(result.signed_in)
        self.assertEqual(result.checks_executed, 0)
        self.assertEqual(executable_plans(inventory), ())
        self.assertEqual([step.status for step in result.report_steps], ["NOT_RUN", "NOT_RUN"])
        status = json.loads((folder / "status.json").read_text())
        self.assertEqual(status["checks_executed"], 0)
        self.assertNotIn("secret-value", json.dumps(status))
        self.assertNotIn("learner@example.com", json.dumps(status))
        report = (folder / "validation-summary.html").read_text()
        self.assertIn("INCOMPLETE", report)
        self.assertNotIn("secret-value", report)
        self.assertNotIn(">VERIFIED<", report)

    def test_sign_in_failure_does_not_run_checks_or_fail_steps(self):
        def boom(username, password):
            raise PrivateRunnerError("Salesforce authentication failed; no steps verified")

        def execute_step(page, step, context):
            raise AssertionError("checks must not run after a failed sign-in")

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=boom, plans=(one_plan(),), execute_step=execute_step, login_budget=5,
        )
        self.assertFalse(result.signed_in)
        self.assertEqual(result.checks_executed, 0)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")
        self.assertNotIn("secret-value", result.message)

    def test_login_timeout_is_bounded_and_not_a_guide_failure(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hang(username, password):
            release.wait(10)

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=hang, login_budget=0,
        )
        self.assertFalse(result.signed_in)
        self.assertIn("timed out", result.message)
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")

    def test_pipeline_pass_keeps_unplanned_steps_not_run(self):
        def login(username, password):
            return None

        def execute_step(page, step, context):
            spec = step.assertions[0].spec
            return [Observation(
                evidence_id=f"{context.run_id}:{context.step_id}:dom:0",
                assertion_id=spec.assertion_id,
                kind=spec.kind,
                actual=True,
                run_id=context.run_id,
                org_id=context.org_id,
                step_id=context.step_id,
                collector_id=context.executor_id,
            )]

        inventory = GuideInventory(1, (
            guide_step("P001N001"),
            guide_step("P001N002", "2", "Save the page"),
        ), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=login, plans=(one_plan(),), execute_step=execute_step, login_budget=5,
        )
        by_id = {step.step_id: step for step in result.report_steps}
        self.assertEqual(by_id["P001N001"].status, "PASS")
        self.assertTrue(by_id["P001N001"].evidence_ids)
        self.assertEqual(by_id["P001N002"].status, "NOT_RUN")
        self.assertEqual(result.checks_executed, 1)
        self.assertNotIn("secret-value", by_id["P001N001"].note)

    def test_still_loading_is_not_a_failure(self):
        def execute_step(page, step, context):
            raise PageStillLoading()

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=lambda username, password: None,
            plans=(one_plan(),), execute_step=execute_step, login_budget=5,
        )
        step = result.report_steps[0]
        self.assertEqual(step.status, "NOT_RUN")
        self.assertIn("still loading", step.note)
        self.assertEqual(result.checks_executed, 0)
        self.assertEqual(refuse_unsettled("FUNCTIONAL_FAIL", still_loading=True), "NOT_RUN")

    def test_default_path_opens_a_playwright_session(self):
        opened = []

        @contextmanager
        def session(username, password, **kwargs):
            opened.append((username, password))
            page = type("Page", (), {"url": "https://example.lightning.force.com/lightning/page/home"})()
            yield page

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        with patch("workshop_validator.private_runner.playwright_session", session):
            result = run_uploaded_guide(
                "learner@example.com", "secret-value", inventory, login_budget=5,
            )
        self.assertEqual(opened, [("learner@example.com", "secret-value")])
        self.assertTrue(result.signed_in)
        self.assertEqual(result.report_steps[0].status, "NEEDS_REVIEW")
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.checks_executed, 1)

    def test_session_sign_in_failure_does_not_fail_guide_steps(self):
        @contextmanager
        def session(username, password):
            raise PrivateRunnerError("Salesforce authentication failed; no steps verified")
            yield None

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory, session=session, login_budget=5,
        )
        self.assertFalse(result.signed_in)
        self.assertIn("authentication failed", result.message)
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")

    def test_session_error_hides_the_password(self):
        @contextmanager
        def session(username, password):
            raise RuntimeError(password)
            yield None

        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory, session=session, login_budget=5,
        )
        self.assertFalse(result.signed_in)
        self.assertEqual(result.message, "Sign-in could not finish.")
        self.assertNotIn("secret-value", result.message)
        self.assertEqual(result.report_steps[0].status, "NOT_RUN")

    def test_session_runs_checks_in_order_and_scrubs_secrets(self):
        page = type("Page", (), {"url": "https://example.lightning.force.com/lightning/page/home"})()

        @contextmanager
        def session(username, password):
            yield page

        def fail(state, step):
            return CheckResult(
                "FUNCTIONAL_FAIL",
                "rejected secret-value",
                (),
            )

        def ok(state, step):
            return CheckResult("PASS", "opened", (f"{step.step_id}:opened",))

        checks = (
            GuideCheck("Fail the first", lambda step: step.number == "1", fail),
            GuideCheck("Pass the second", lambda step: step.number == "2", ok),
        )
        inventory = GuideInventory(1, (
            guide_step("P001N001", "1", "Select Preview"),
            guide_step("P001N002", "2", "Welcome message is shown"),
            guide_step("P001N003", "3", "Do something else"),
        ), (), ())
        folder = Path(self.tmp.name) / "ordered"
        result = run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            session=session, checks=checks, output_dir=folder, login_budget=5,
        )
        by_id = {step.step_id: step for step in result.report_steps}
        self.assertEqual(by_id["P001N001"].status, "FUNCTIONAL_FAIL")
        self.assertEqual(by_id["P001N001"].note, "The step note was omitted.")
        self.assertNotIn("secret-value", by_id["P001N001"].note)
        self.assertEqual(by_id["P001N002"].status, "PASS")
        self.assertTrue(by_id["P001N002"].evidence_ids)
        self.assertEqual(by_id["P001N003"].status, "NEEDS_REVIEW")
        self.assertEqual(result.checks_executed, 3)
        report = (folder / "validation-summary.html").read_text()
        self.assertNotIn("secret-value", report)
        self.assertNotIn("learner@example.com", report)

    def test_report_is_not_written_inside_the_repository(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        inventory = GuideInventory(1, (guide_step("P001N001"),), (), ())
        run_uploaded_guide(
            "learner@example.com", "secret-value", inventory,
            login=lambda username, password: None,
            output_dir=root / "inside", repo_root=root, login_budget=5,
        )
        self.assertEqual(list(root.rglob("status.json")), [])
        self.assertEqual(list(root.rglob("*.html")), [])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)


class _StateHarness:
    def __init__(self):
        self.launch_kwargs = None
        self.contexts = []
        self.navigations = []


def _state_playwright(harness):
    class FakePage:
        def __init__(self):
            self.url = "about:blank"

        def set_default_timeout(self, timeout):
            return None

        def goto(self, url, wait_until=None, timeout=None):
            harness.navigations.append(url)
            if getattr(harness, "login_wall", False) and "lightning/page/home" in url:
                self.url = "https://example.my.salesforce.com/"
                self.login_wall = True
            else:
                self.url = url
                self.login_wall = False

        def wait_for_timeout(self, timeout):
            return None

        def locator(self, selector):
            page = self

            class Box:
                def count(self):
                    return 1 if selector == "#username" and getattr(page, "login_wall", False) else 0

                def is_visible(self):
                    return self.count() > 0

            return Box()

        @property
        def video(self):
            return None

    class FakeContext:
        def new_page(self):
            return FakePage()

        def storage_state(self, path=None):
            return {
                "cookies": [{
                    "name": "sid",
                    "value": "fake-session",
                    "domain": "example.my.salesforce.com",
                    "path": "/",
                }],
                "origins": [{
                    "origin": "https://example.lightning.force.com",
                    "localStorage": [],
                }],
            }

        def close(self):
            return None

    class FakeBrowser:
        def new_context(self, **kwargs):
            harness.contexts.append(kwargs)
            return FakeContext()

        def close(self):
            return None

    class FakeChromium:
        def launch(self, **kwargs):
            harness.launch_kwargs = kwargs
            return FakeBrowser()

    class FakePlaywright:
        def __init__(self):
            self.chromium = FakeChromium()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def sync_playwright():
        return FakePlaywright()

    return sync_playwright


class StoredSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.private = self.root / "private"
        self.password = "secret-value"
        self.username = "learner@example.com"

    def test_storage_state_is_reused_when_already_logged_in(self):
        harness = _StateHarness()
        login_calls = []

        def fake_login(page, username, password):
            login_calls.append(username)
            page.goto("https://login.salesforce.com/")
            page.url = "https://example.lightning.force.com/lightning/page/home"

        first = self.private / "run-one"
        second = self.private / "run-two"
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=first, repo_root=self.repo,
            ):
                pass
        self.assertEqual(login_calls, [self.username])
        self.assertEqual(
            harness.navigations,
            ["https://login.salesforce.com/"],
        )
        self._assert_headed_with_video(harness, first)
        self.assertNotIn("storage_state", harness.contexts[0])
        storage = self.private / "browser-state.json"
        self.assertTrue(storage.is_file())
        self.assertFalse(storage.is_symlink())
        self.assertEqual(os.stat(storage).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.private).st_mode & 0o777, 0o700)
        saved = storage.read_text(encoding="utf-8")
        self.assertNotIn(self.password, saved)
        self.assertNotIn(self.username, saved)
        self.assertEqual(list(self.repo.rglob("browser-state.json")), [])
        self.assertEqual(list(self.repo.rglob("*.webm")), [])

        harness.navigations.clear()
        login_calls.clear()
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=second, repo_root=self.repo,
            ) as page:
                self.assertIn("lightning.force.com", page.url)
        self.assertEqual(login_calls, [])
        self.assertEqual(harness.navigations, [
            "https://example.lightning.force.com/lightning/page/home",
        ])
        self.assertNotIn("login.salesforce.com", " ".join(harness.navigations))
        self._assert_headed_with_video(harness, second)
        self.assertEqual(harness.contexts[-1]["storage_state"], str(storage.resolve()))

    def test_login_wall_on_the_org_host_signs_in_again(self):
        harness = _StateHarness()
        login_calls = []

        def fake_login(page, username, password):
            login_calls.append(username)
            page.goto("https://login.salesforce.com/")
            page.url = "https://example.lightning.force.com/lightning/page/home"
            page.login_wall = False

        first = self.private / "run-one"
        second = self.private / "run-two"
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=first, repo_root=self.repo,
            ):
                pass
        harness.navigations.clear()
        login_calls.clear()
        harness.login_wall = True
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=second, repo_root=self.repo,
            ) as page:
                self.assertIn("lightning.force.com", page.url)
        self.assertEqual(login_calls, [self.username])
        self.assertIn("https://login.salesforce.com/", harness.navigations)

    def test_storage_inside_the_repo_is_not_written(self):
        harness = _StateHarness()

        def fake_login(page, username, password):
            page.url = "https://example.lightning.force.com/lightning/page/home"

        inside = self.repo / "captured"
        with self._patched(harness, fake_login):
            with playwright_session(
                self.username, self.password, video_dir=inside, repo_root=self.repo,
            ):
                pass
        self.assertEqual(list(self.repo.rglob("browser-state.json")), [])
        self.assertNotIn("storage_state", harness.contexts[0])
        self.assertNotIn("record_video_dir", harness.contexts[0])
        self.assertEqual(harness.launch_kwargs["headless"], False)

    def _assert_headed_with_video(self, harness, folder):
        self.assertEqual(harness.launch_kwargs["headless"], False)
        self.assertNotIn("channel", harness.launch_kwargs)
        context = harness.contexts[-1]
        self.assertEqual(Path(context["record_video_dir"]).resolve(), folder.resolve())
        self.assertEqual(context["viewport"], {"width": 1500, "height": 1000})
        self.assertEqual(context["record_video_size"], {"width": 1500, "height": 1000})
        self.assertIn("Chrome/", context["user_agent"])
        self.assertNotIn("Headless", context["user_agent"])

    def _patched(self, harness, fake_login):
        return _stored_patches(harness, fake_login)


class _stored_patches:
    def __init__(self, harness, fake_login):
        self.harness = harness
        self.fake_login = fake_login
        self._stack = None

    def __enter__(self):
        from contextlib import ExitStack
        self._stack = ExitStack()
        self._stack.__enter__()
        self._stack.enter_context(patch(
            "playwright.sync_api.sync_playwright", _state_playwright(self.harness),
        ))
        self._stack.enter_context(patch(
            "workshop_validator.private_runner.login_to_salesforce", self.fake_login,
        ))
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._stack.__exit__(exc_type, exc, tb)


class DefaultsTests(unittest.TestCase):
    def test_page_wait_default_is_90_seconds(self):
        """CLAUDE.md: poll ~2 s, up to ~90 s"""
        sig = inspect.signature(run_uploaded_guide)
        page_wait_s_default = sig.parameters['page_wait_s'].default
        self.assertEqual(page_wait_s_default, 90)


if __name__ == "__main__":
    unittest.main()
