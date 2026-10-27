import re
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

from workshop_validator.guide_inventory import GuideInventory, GuideStep, extract_inventory
from workshop_validator.guide_walk import (
    LOADING_NOTE,
    PAGE_POLL_S,
    PAGE_WAIT_S,
    CheckResult,
    GuideCheck,
    ResearchResult,
    builtin_checks,
    find_check,
    click_role,
    present,
    walk_guide,
)
from workshop_validator.live_test import wait_for_live_test
from workshop_validator.step_board import build_board


SETUP = "Set Up Sample Environment"
EX11 = "Exercise 1-1: Build a Sample Widget"
EX12 = "Exercise 1-2: Configure Widget Display"


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


FIXTURES = (
    (SETUP, "Task 1: Get your org credentials.", "1",
     "Open Chrome and navigate to: sforce.co/OrgClaim", "Open OrgClaim"),
    (SETUP, "Task 1: Get your org credentials.", "2",
     "Enter the event code provided by your instructor and select Redeem Code.",
     "Redeem the event code"),
    (SETUP, "Task 2: Log in to your org.", "1",
     "Select Launch from OrgClaim and a new tab will open logged in to your org.",
     "Launch the org"),
    (SETUP, "Task 2: Log in to your org.", "2",
     "Alternatively, navigate to login.salesforce.com and log in with your username and password.",
     "Sign in at login.salesforce.com"),
    (SETUP, "Task 3: Update your user information.", "1",
     "Select the profile picture in the top right corner and select Settings.",
     "Open Personal Information"),
    (SETUP, "Task 3: Update your user information.", "2",
     "Update the first and last name fields with your name.", "Check first and last name"),
    (SETUP, "Task 3: Update your user information.", "3",
     "Update the email field with your email address. Verify the company name is Acme Widgets.",
     "Set the company name"),
    (SETUP, "Task 3: Update your user information.", "4",
     "Select Save at the bottom of the screen.", "Save personal information"),
    (SETUP, "Task 4: Enable Einstein and Agentforce.", "1",
     "Go to Setup using the gear icon in the top right.", "Open Setup"),
    (SETUP, "Task 4: Enable Einstein and Agentforce.", "2",
     "Use Setup Quick Find to search for and select Agentforce Agents.",
     "Open Agentforce Agents"),
    (SETUP, "Task 4: Enable Einstein and Agentforce.", "3",
     "Toggle Agentforce to On.", "Turn Agentforce on"),
    (SETUP, "Task 5: Assign the Agentforce Agent Actions permission set to the EinsteinServiceAgent", "1",
     "Use the Setup Quick Find to search for and select Users.", "Open Users"),
    (SETUP, "Task 5: Assign the Agentforce Agent Actions permission set to the EinsteinServiceAgent", "2",
     "Select EinsteinServiceAgent User.", "Open the EinsteinServiceAgent user"),
    (SETUP, "Task 5: Assign the Agentforce Agent Actions permission set to the EinsteinServiceAgent", "3",
     "Hover over Permission Set Assignments and select Edit Assignments.",
     "Edit permission set assignments"),
    (SETUP, "Task 5: Assign the Agentforce Agent Actions permission set to the EinsteinServiceAgent", "4",
     "Add Agentforce Action Access from Available Permission Sets to Enabled Permission Sets.",
     "Add Agentforce Action Access"),
    (SETUP, "Task 5: Assign the Agentforce Agent Actions permission set to the EinsteinServiceAgent", "5",
     "Select Save.", "Save permission sets"),
    (EX11, "Task 1: Create a new agent.", "1",
     "In the App Launcher, select the Agentforce Studio app.", "Open Agentforce Studio"),
    (EX11, "Task 1: Create a new agent.", "2",
     "Select the arrow next to the New Agent button and select New from Script.",
     "Start a new agent from script"),
    (EX11, "Task 1: Create a new agent.", "3",
     "Open the following URL and copy the script provided: sforce.co/agentcode.",
     "Open the agent script"),
    (EX11, "Task 1: Create a new agent.", "4",
     "Paste the script to create the new agent and select Create Agent.", "Create the agent"),
    (EX11, "Task 2: Explore the new builder interface.", "1",
     "Select the name of the agent (HelloWorld Agent) to see an overview of the general agent definition.",
     "Open HelloWorld Agent"),
    (EX11, "Task 2: Explore the new builder interface.", "2",
     "Click on Canvas, then select Script.", "Open the agent script view"),
    (EX11, "Task 2: Explore the new builder interface.", "3",
     "Select Save.", "Save the agent"),
    (EX11, "Task 2: Explore the new builder interface.", "4",
     "Select Select User.", "Open Select User"),
    (EX11, "Task 2: Explore the new builder interface.", "5",
     "From the Agent’s User Record list, select the EinsteinServiceAgent User.",
     "Choose the EinsteinServiceAgent user"),
    (EX11, "Task 2: Explore the new builder interface.", "6",
     "Click Save.", "Save the agent user"),
    (EX11, "Task 3: Test your agent.", "1",
     "Select Preview to test your agent in Live Test Mode.", "Open Live Test"),
    (EX11, "Task 3: Test your agent.", "2",
     "The agent's welcome message is the following:", "Check the welcome message"),
    (EX11, "Task 3: Test your agent.", "3",
     "Reply with Hello there!", "Reply Hello there!"),
    (EX11, "Task 4: Activate your agent.", "1",
     "Select Commit Version.", "Commit the agent version"),
    (EX11, "Task 4: Activate your agent.", "2",
     "Select Activate.", "Activate the agent"),
)


UNMATCHED = (
    (EX12, "Task 3: Test your agent.", "1",
     "Select Preview to test your agent in Live Test Mode."),
    (EX12, "Task 2: Update the System Messages.", "2",
     "Click on Canvas, then select Script. This opens the agent configuration."),
    ("Exercise 2-1: Explore Sample Data", "Task 1: Review the Data Model.", "1",
     "Select Save."),
    (SETUP, "Task 1: Get your org credentials.", "9", "Open the setup page"),
)


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Loc:
    def __init__(self, page, kind, name=""):
        self.page = page
        self.kind = kind
        self.name = name

    def count(self):
        if self.kind == "body":
            return 1
        if self.kind == "spinner":
            return 1 if self.page.spinning else 0
        if self.kind == "label":
            return 1 if self.name in self.page.values else 0
        if self.kind == "html":
            return 1 if self.name.lower() in (self.page.html or "").lower() else 0
        if self.kind == "text":
            blob = f"{self.page.body}\n{self.page.html}"
            return 1 if self.name.lower() in blob.lower() else 0
        return 1 if (self.kind, self.name) in self.page.roles else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        self.page.reads += 1
        return self.count() > 0

    def is_enabled(self):
        return self.page.enabled.get((self.kind, self.name), True)

    def get_attribute(self, name, timeout=None):
        if name == "aria-checked":
            self.page.state_reads += 1
            if self.page.state_reads < self.page.reveal_state_after:
                return None
            return self.page.checked.get((self.kind, self.name))
        elif name in ("aria-selected", "aria-pressed", "aria-expanded"):
            return self.page.aria_states.get((self.kind, self.name, name))
        return None

    def is_checked(self):
        return str(self.get_attribute("aria-checked")).lower() == "true"

    def click(self, timeout=None):
        self.page.events.append(("click", self.kind, self.name))
        reveal = getattr(self.page, "on_click", None)
        if reveal is not None:
            reveal(self.kind, self.name)

    def fill(self, value, timeout=None):
        self.page.values[self.name] = value
        self.page.events.append(("fill", self.name, value))
        reveal = getattr(self.page, "on_fill", None)
        if reveal is not None:
            reveal(self.name, value)

    def input_value(self, timeout=None):
        return self.page.values.get(self.name, "")

    def inner_text(self, timeout=None):
        if self.kind == "body":
            text = self.page.body
        elif self.name:
            text = self.name
        else:
            text = ""
        self.page.reads += 1
        self.page.snapshots.append(text)
        return text

    def nth(self, index):
        return self

    def locator(self, selector):
        """Controls inside this element, from page.children."""
        inside = self.page.children.get((self.kind, self.name), ())
        inside = [item for item in inside if item in self.page.roles]
        if "aria-haspopup" in selector:
            inside = [item for item in inside if item in self.page.has_popup]
        return _ListLoc(self.page, inside)


class _ListLoc:
    def __init__(self, page, items):
        self.page = page
        self.items = list(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        kind, name = self.items[index]
        return Loc(self.page, kind, name)

    @property
    def first(self):
        return self.nth(0)


class _MultiLoc:
    """Locator that can return multiple items for a role."""
    def __init__(self, page, role, selector=""):
        self.page = page
        self.role = role
        self.items = [(kind, name) for kind, name in page.roles if kind == role]
        # A :not([role='listbox'] *) selector leaves out controls inside a popup.
        if ":not(" in selector:
            self.items = [item for item in self.items if item not in page.in_popup]

    def count(self):
        return len(self.items)

    def nth(self, index):
        if index < len(self.items):
            kind, name = self.items[index]
            return Loc(self.page, kind, name)
        return Loc(self.page, self.role, "")


class Page:
    def __init__(self, url, body="", roles=(), values=None):
        self.url = url
        self.body = body
        self.roles = set(roles)
        self.values = dict(values or {})
        self.enabled = {}
        self.events = []
        self.page_title = ""
        self.spinning = False
        self.html = ""
        self.checked = {}
        self.aria_states = {}  # For aria-selected, aria-pressed, aria-expanded
        self.in_popup = set()  # (kind, name) shown inside a menu or listbox popup
        self.children = {}  # (kind, name) -> controls inside that element
        self.has_popup = set()  # (kind, name) with aria-haspopup set
        self.state_reads = 0
        self.reads = 0
        self.snapshots = []
        self.reveal_state_after = 1
        self._frames = None

    @property
    def frames(self):
        if self._frames is not None:
            return self._frames
        return [self]

    @frames.setter
    def frames(self, value):
        self._frames = value

    def title(self):
        return self.page_title

    def locator(self, selector):
        if selector == "body":
            return Loc(self, "body")
        if "spinner" in selector or "progressbar" in selector:
            return Loc(self, "spinner")
        # Handle [role='...'] selectors
        role_match = re.search(r"""\[role=['"](\w+)['"]\]""", selector)
        if role_match:
            role = role_match.group(1)
            return _MultiLoc(self, role, selector)
        match = re.search(r"""(?:title|aria-label)\s*=\s*['\"]([^'\"]+)['\"]""", selector)
        if match and match.group(1).lower() in (self.html or "").lower():
            return Loc(self, "html", match.group(1))
        if selector.startswith("text="):
            return Loc(self, "text", selector[5:])
        return Loc(self, "css", selector)

    def get_by_role(self, role, name=None, exact=True):
        return Loc(self, role, name or "")

    def get_by_label(self, name):
        return Loc(self, "label", name)

    def get_by_text(self, name, exact=False):
        return Loc(self, "text", name or "")

    def get_by_title(self, name, exact=False):
        return Loc(self, "html", name or "")

    def content(self):
        self.reads += 1
        if self.html:
            return self.html
        return self.body or ""

    def goto(self, url, wait_until=None, timeout=None):
        self.events.append(("goto", url))
        self.url = url
        if "OrgClaim" in url:
            self.body = "Claim Your Event Materials. Event code. Redeem Code"
        opened = getattr(self, "on_goto", None)
        if opened is not None:
            opened(url)


def inventory_of(*items):
    return GuideInventory(1, tuple(items), (), ())


class MatcherTests(unittest.TestCase):
    def test_each_known_instruction_matches_one_check(self):
        catalog = builtin_checks()
        seen = []
        for index, (exercise, task, number, text, expected) in enumerate(FIXTURES):
            item = step(f"S{index}", exercise, task, number, text)
            names = [check.name for check in catalog if check.matches(item)]
            self.assertEqual(names, [expected], text)
            seen.append(expected)
        self.assertEqual(len(seen), len(set(seen)))

    def test_later_exercises_do_not_reuse_exercise_1_checks(self):
        for index, (exercise, task, number, text) in enumerate(UNMATCHED):
            item = step(f"U{index}", exercise, task, number, text)
            self.assertIsNone(find_check(item), text)


class WalkTests(unittest.TestCase):
    def walk(self, page, items, checks=None):
        clock = Clock()
        return walk_guide(
            page, inventory_of(*items), checks=checks,
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
        )

    def test_failure_stays_visible_and_later_checks_still_run(self):
        def fail(state, step):
            return CheckResult(
                "FUNCTIONAL_FAIL",
                "Live Test shows Let's chat, not the saved welcome.",
                (),
            )

        def ok(state, step):
            return CheckResult("PASS", "opened", (f"{step.step_id}:opened",))

        checks = (
            GuideCheck("Fail", lambda item: item.number == "1", fail),
            GuideCheck("Pass", lambda item: item.number == "2", ok),
        )
        items = (
            step("a", EX11, "Task 3: Test your agent.", "1", "Select Preview"),
            step("b", EX11, "Task 3: Test your agent.", "2", "Welcome message is shown"),
            step("c", EX11, "Task 3: Test your agent.", "3", "Leave this one"),
        )
        outcome = self.walk(Page("https://example.lightning.force.com/home"), items, checks)
        self.assertEqual(outcome.statuses["a"], "FUNCTIONAL_FAIL")
        self.assertIn("Let's chat", outcome.notes["a"])
        self.assertEqual(outcome.statuses["b"], "PASS")
        self.assertEqual(outcome.evidence["b"], ("b:opened",))
        self.assertEqual(outcome.statuses["c"], "NEEDS_REVIEW")
        self.assertIn("no action for this step yet", outcome.notes["c"])
        self.assertIn("c", outcome.executed)
        self.assertEqual(outcome.executed[:2], ("a", "b"))

    def test_still_loading_is_not_a_failure(self):
        def run(state, step):
            from workshop_validator.guide_walk import require
            return require(state, lambda: False, "missing")

        page = Page("https://example.lightning.force.com/home", body="Loading assistant")
        item = step("a", SETUP, "Task 1: Get your org credentials.", "1", "Open the page")
        outcome = self.walk(
            page, (item,),
            (GuideCheck("Load", lambda current: True, run),),
        )
        self.assertEqual(outcome.statuses["a"], "NOT_RUN")
        self.assertIn("still loading", outcome.notes["a"])
        self.assertEqual(outcome.notes["a"], LOADING_NOTE)

    def test_pass_without_evidence_is_not_a_pass(self):
        def run(state, step):
            return CheckResult("PASS", "claimed", ())

        item = step("a", SETUP, "Task 1: Get your org credentials.", "1", "Open the page")
        outcome = self.walk(
            Page("https://example.lightning.force.com/home"), (item,),
            (GuideCheck("Empty", lambda current: True, run),),
        )
        self.assertEqual(outcome.statuses["a"], "NEEDS_REVIEW")
        self.assertNotIn("a", outcome.evidence)

    def test_unexpected_error_does_not_leak_and_does_not_stop_the_walk(self):
        def boom(state, step):
            raise RuntimeError("secret-value")

        def ok(state, step):
            return CheckResult("PASS", "opened", (f"{step.step_id}:opened",))

        items = (
            step("a", SETUP, "Task 1: Get your org credentials.", "1", "First"),
            step("b", SETUP, "Task 1: Get your org credentials.", "2", "Second"),
        )
        outcome = self.walk(
            Page("https://example.lightning.force.com/home"), items,
            (
                GuideCheck("Boom", lambda current: current.number == "1", boom),
                GuideCheck("Ok", lambda current: current.number == "2", ok),
            ),
        )
        self.assertEqual(outcome.statuses["a"], "UI_MISMATCH")
        self.assertNotIn("secret-value", outcome.notes["a"])
        self.assertNotEqual(
            outcome.notes["a"],
            "The guide target did not match the observed page.",
        )
        self.assertEqual(outcome.statuses["b"], "PASS")

    def test_blocked_promo_iframe_is_not_a_guide_failure(self):
        class BrokenPromo:
            @property
            def url(self):
                raise RuntimeError("promos.html blocked")

            def get_by_role(self, role, name=None, exact=True):
                raise AssertionError("promo iframe was used")

            def get_by_label(self, name):
                raise AssertionError("promo iframe was used")

            def locator(self, selector):
                raise AssertionError("promo iframe was used")

        promo = Page("https://login.salesforce.com/promos.html", body="Access Denied")
        promo.roles.add(("button", "Launch"))
        page = Page("https://example.lightning.force.com/lightning/page/home")
        page.frames = [promo, BrokenPromo()]
        self.assertFalse(present(page, "button", "Launch"))
        item = step(
            "login", SETUP, "Task 2: Log in to your org.", "2",
            "Alternatively, navigate to login.salesforce.com and log in with your username and password.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["login"], "PASS")
        self.assertNotIn("promos.html", outcome.notes.get("login", ""))

    def test_login_form_on_the_org_host_is_not_already_signed_in(self):
        page = Page(
            "https://example.my.salesforce.com/",
            body="To access this page, you have to log in to Salesforce.",
        )
        item = step(
            "login", SETUP, "Task 2: Log in to your org.", "2",
            "Alternatively, navigate to login.salesforce.com and log in with your username and password.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["login"], "UI_MISMATCH")
        self.assertIn("did not open the org", outcome.notes["login"])

    def test_login_step_passes_from_the_signed_in_page(self):
        item = step(
            "login", SETUP, "Task 2: Log in to your org.", "2",
            "Alternatively, navigate to login.salesforce.com and log in with your username and password.",
        )
        page = Page("https://example.lightning.force.com/lightning/page/home")
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["login"], "PASS")
        self.assertEqual(outcome.evidence["login"], ("login:lightning-open",))
        self.assertIn("Lightning is already open", outcome.notes["login"])
        self.assertEqual(page.events, [])

    def test_authenticated_session_does_not_open_login(self):
        page = Page("https://example.lightning.force.com/lightning/page/home")
        items = (
            step("P003N001", SETUP, "Task 1: Get your org credentials.", "1",
                 "Open Chrome and navigate to: sforce.co/OrgClaim"),
            step("P003N002", SETUP, "Task 1: Get your org credentials.", "2",
                 "Enter the event code provided by your instructor and select Redeem Code."),
            step("P003N003", SETUP, "Task 2: Log in to your org.", "1",
                 "Select Launch from OrgClaim and a new tab will open logged in to your org."),
            step("P003N004", SETUP, "Task 2: Log in to your org.", "2",
                 "Alternatively, navigate to login.salesforce.com and log in with your username and password."),
        )
        outcome = self.walk(page, items)
        self.assertEqual(outcome.statuses["P003N001"], "NOT_RUN")
        self.assertEqual(outcome.statuses["P003N002"], "NOT_RUN")
        self.assertEqual(outcome.statuses["P003N003"], "NOT_RUN")
        self.assertEqual(outcome.statuses["P003N004"], "PASS")
        self.assertEqual(outcome.evidence["P003N004"], ("P003N004:lightning-open",))
        self.assertIn("Lightning is already open", outcome.notes["P003N004"])
        self.assertNotIn("login.salesforce.com", " ".join(str(event) for event in page.events))
        self.assertFalse(any(event[0] == "goto" for event in page.events))
        self.assertFalse(any(event[0] == "click" for event in page.events))

    def test_orgclaim_opens_and_redeem_without_a_code_is_blocked(self):
        page = Page("about:blank")
        page.roles.add(("button", "Redeem Code"))
        page.values["Event code"] = ""
        items = (
            step("open", SETUP, "Task 1: Get your org credentials.", "1",
                 "Open Chrome and navigate to: sforce.co/OrgClaim"),
            step("code", SETUP, "Task 1: Get your org credentials.", "2",
                 "Enter the event code provided by your instructor and select Redeem Code."),
        )
        outcome = self.walk(page, items)
        self.assertEqual(outcome.statuses["open"], "PASS")
        self.assertEqual(page.events[0], ("goto", "https://sforce.co/OrgClaim"))
        self.assertEqual(outcome.statuses["code"], "ENV_BLOCKED")
        self.assertIn("No event code", outcome.notes["code"])

    def test_company_name_is_set_without_touching_email(self):
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            values={
                "First Name": "Ada",
                "Last Name": "Lovelace",
                "Company Name": "Old Company",
                "Email": "person@example.com",
            },
        )
        item = step(
            "company", SETUP, "Task 3: Update your user information.", "3",
            "Update the email field with your email address. Verify the company name is Acme Widgets.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["company"], "PASS")
        self.assertEqual(page.values["Company Name"], "Acme Widgets")
        self.assertEqual(page.values["Email"], "person@example.com")
        self.assertNotIn("person@example.com", outcome.notes["company"])

    def test_launch_missing_is_a_mismatch_and_sign_in_still_passes(self):
        page = Page("https://sforce.co/OrgClaim", body="Claim page")
        items = (
            step("launch", SETUP, "Task 2: Log in to your org.", "1",
                 "Select Launch from OrgClaim and a new tab will open logged in to your org."),
            step("login", SETUP, "Task 2: Log in to your org.", "2",
                 "Alternatively, navigate to login.salesforce.com and log in with your username and password."),
        )
        outcome = self.walk(page, items)
        self.assertEqual(outcome.statuses["launch"], "UI_MISMATCH")
        self.assertIn("Launch was not", outcome.notes["launch"])
        self.assertEqual(outcome.statuses["login"], "UI_MISMATCH")
        self.assertNotIn("login.salesforce.com", " ".join(str(event) for event in page.events))
        self.assertEqual(outcome.executed, ("launch", "login"))

    def test_agentforce_agents_opens_after_the_setup_page_settles(self):
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            body="My Personal Information\nPersonal Information",
        )
        page.page_title = "Personal Information | Salesforce"
        page.roles.add(("link", "Agentforce Studio"))

        def goto(url, wait_until=None, timeout=None):
            page.events.append(("goto", url))
            page.url = url
            page.page_title = "Agentforce Agents | Salesforce"
            page.body = "Loading"
            page.spinning = True

        def sleeper(seconds):
            clock.now += seconds
            page.spinning = False
            page.body = (
                "Agentforce Agents\nAgentforce\nOn\nNew Agent\n"
                "Merchant Support Agent\nHelloWorld Agent"
            )
            page.roles.add(("switch", "Agentforce"))
            page.roles.add(("button", "New Agent"))

        page.goto = goto
        clock = Clock()
        item = step(
            "agents", SETUP, "Task 4: Enable Einstein and Agentforce.", "2",
            "Use Setup Quick Find to search for and select Agentforce Agents.",
        )
        outcome = walk_guide(
            page, inventory_of(item), timeout_s=90, interval_s=2,
            clock=clock, sleeper=sleeper,
        )
        self.assertEqual(outcome.statuses["agents"], "PASS")
        self.assertEqual(
            page.events[0],
            ("goto", "https://example.lightning.force.com/lightning/setup/EinsteinCopilot/home"),
        )
        self.assertNotIn(("fill", "Quick Find", "Agentforce Agents"), page.events)
        self.assertIn("setup page", outcome.notes["agents"])
        self.assertEqual(outcome.evidence["agents"], ("agents:agentforce-agents",))

    def test_empty_agentforce_list_is_not_a_failure(self):
        page = Page(
            "https://example.lightning.force.com/lightning/page/home",
            body="Data Cloud Setup",
        )

        def goto(url, wait_until=None, timeout=None):
            page.events.append(("goto", url))
            page.url = url
            page.page_title = "Agentforce Agents | Salesforce"
            page.body = "Agentforce Agents\nAgent Name"
            page.spinning = False

        page.goto = goto
        item = step(
            "agents", SETUP, "Task 4: Enable Einstein and Agentforce.", "2",
            "Use Setup Quick Find to search for and select Agentforce Agents.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["agents"], "NOT_RUN")
        self.assertEqual(outcome.notes["agents"], LOADING_NOTE)
        self.assertNotIn("Quick Find", outcome.notes["agents"])

    def test_settled_page_without_agentforce_agents_is_a_mismatch(self):
        page = Page(
            "https://example.lightning.force.com/lightning/settings/personal/PersonalInformation/home",
            body="My Personal Information",
        )

        def goto(url, wait_until=None, timeout=None):
            page.events.append(("goto", url))
            page.url = url
            page.page_title = "Page not found | Salesforce"
            page.body = "Page not found"
            page.spinning = False

        page.goto = goto
        item = step(
            "agents", SETUP, "Task 4: Enable Einstein and Agentforce.", "2",
            "Use Setup Quick Find to search for and select Agentforce Agents.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["agents"], "UI_MISMATCH")
        self.assertIn("did not open", outcome.notes["agents"])
        self.assertNotIn("Quick Find", outcome.notes["agents"])

    def attempt(self, page, items, research=None, sleeper=None):
        clock = Clock()

        def default_sleep(seconds):
            clock.now += seconds

        outcome = walk_guide(
            page, inventory_of(*items), checks=(),
            timeout_s=0, interval_s=1, clock=clock,
            sleeper=sleeper or default_sleep, research=research,
        )
        return clock, outcome

    def test_toggle_already_on_passes_without_a_click(self):
        page = Page("https://example.lightning.force.com/lightning/setup/home")
        page.roles.add(("switch", "Einstein"))
        page.checked[("switch", "Einstein")] = "true"
        item = step(
            "toggle", "Exercise 9: Widgets", "Task 1: Enable it.", "1",
            "Toggle Einstein to On.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["toggle"], "PASS")
        self.assertGreaterEqual(page.state_reads, 1)
        self.assertEqual(page.events, [])
        self.assertEqual(clock.now, 0)
        self.assertIn("already on in the provisioned org", outcome.notes["toggle"])
        self.assertIn("not clicked", outcome.notes["toggle"])
        self.assertEqual(outcome.evidence["toggle"], ("toggle:already-set:Einstein",))

    def test_profile_sentence_opens_view_profile_then_settings(self):
        page = Page("https://example.lightning.force.com/lightning/page/home")
        page.roles.add(("button", "View profile"))

        def reveal(kind, name):
            if name == "View profile":
                page.roles.add(("link", "Settings"))
            if name == "Settings":
                page.values["First Name"] = "EPIC"

        page.on_click = reveal
        item = step(
            "profile", "Exercise 1: Get Started", "Task 1: Update your user.", "1",
            'In the upper right corner, click the icon to the right of the notifications bell. '
            '(If you hover over it, you will see "View profile".) Then click Settings.',
        )
        later = step(
            "later", "Exercise 1: Get Started", "Task 1: Update your user.", "2",
            "Under Personal Information, enter the following:",
        )
        outcome = self.walk(page, (item, later))
        self.assertEqual(outcome.statuses["profile"], "PASS")
        self.assertIn("Personal Information", outcome.notes["profile"])
        self.assertEqual(
            [event for event in page.events if event[0] == "click"],
            [("click", "button", "View profile"), ("click", "link", "Settings")],
        )
        self.assertEqual(outcome.statuses["later"], "NEEDS_REVIEW")
        self.assertIn("no action for this step yet", outcome.notes["later"])

    def test_quoted_control_is_clicked_before_the_following_name(self):
        page = Page("https://example.lightning.force.com/lightning/page/home")
        page.roles.add(("button", "App Launcher"))

        def reveal(kind, name):
            if name == "App Launcher":
                page.roles.add(("link", "Sales"))
                page.url += "?appLauncher=open"
            elif name == "Sales":
                page.url = "https://example.lightning.force.com/lightning/o/Account/home"
                page.page_title = "Accounts | Sales | Salesforce"

        page.on_click = reveal
        item = step(
            "apps", "Exercise 2: Explore", "Task 1: Open Sales.", "1",
            'Click the waffle. (If you hover over it, you will see "App Launcher".) Then click Sales.',
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["apps"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertEqual(
            [event for event in page.events if event[0] == "click"],
            [("click", "button", "App Launcher"), ("click", "link", "Sales")],
        )
        self.assertIn("App Launcher", outcome.notes["apps"])
        self.assertIn("Sales", outcome.notes["apps"])

    def test_then_click_uses_save_and_then_ok(self):
        page = Page("https://example.lightning.force.com/lightning/page/home")
        page.roles.add(("button", "Save"))

        def reveal(kind, name):
            if name == "Save":
                # Save opens a dialog that holds the OK button.
                page.roles.add(("dialog", "Confirm"))
                page.roles.add(("button", "OK"))
            elif name == "OK":
                # OK closes the dialog; the runner records the accepted dialog text.
                page.roles.discard(("dialog", "Confirm"))
                page.roles.discard(("button", "OK"))
                page._accepted_dialog = "Confirm your changes"

        page.on_click = reveal
        item = step(
            "save", "Exercise 1: Get Started", "Task 1: Update your user.", "3",
            "Click Save at the bottom, then read the popup and click OK.",
        )
        clock, outcome = self.attempt(page, (item,))
        # Save passes on the newly opened dialog, OK on the accepted dialog text.
        self.assertEqual(outcome.statuses["save"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertEqual(
            [event for event in page.events if event[0] == "click"],
            [("click", "button", "Save"), ("click", "button", "OK")],
        )

    def test_name_shown_only_in_a_listbox_is_not_evidence(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.body = "New Screen"
        page.roles.add(("button", "Name"))

        def on_click(kind, name):
            if kind == "button" and name == "Name":
                # The click only opens a listbox that repeats the name.
                page.roles.add(("listbox", "Suggestions"))
                page.roles.add(("option", "Name"))
                page.roles.add(("textbox", "Name"))
                page.in_popup.update({("option", "Name"), ("textbox", "Name")})

        page.on_click = on_click
        item = step(
            "name", "Exercise 2: Flows", "Task 1: Screen.", "f",
            "Scroll to find the Name field. Click it once to place it in the middle panel.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertIn(("click", "button", "Name"), page.events)
        self.assertEqual(outcome.statuses["name"], "NEEDS_REVIEW")
        self.assertIn("no change could be read", outcome.notes["name"])

    def test_save_without_a_popup_passes_on_the_success_banner(self):
        page = Page("https://example.lightning.force.com/lightning/setup/personalInformation")
        page.roles.add(("button", "Save"))

        def reveal(kind, name):
            if name == "Save":
                page.body = "Your settings have been successfully saved."

        page.on_click = reveal
        item = step(
            "save", "Exercise 1: Get Started", "Task 1: Update your user.", "3",
            "Click Save at the bottom, then read the popup and click OK.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["save"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn("successfully saved", outcome.notes["save"])
        self.assertEqual(
            [event for event in page.events if event[0] == "click"],
            [("click", "button", "Save")],
        )

    def test_save_then_ok_clicks_ok_when_visible_despite_success_banner(self):
        """Prove _finish_clicks doesn't skip OK when OK is visible."""
        page = Page("https://example.lightning.force.com/lightning/page/home")
        page.roles.add(("button", "Save"))

        def reveal(kind, name):
            if name == "Save":
                page.body = "Your settings have been successfully saved."
                page.roles.add(("button", "OK"))
            elif name == "OK":
                page._accepted_dialog = "Your settings have been successfully saved."

        page.on_click = reveal
        item = step(
            "save", "Exercise 1: Get Started", "Task 1: Update.", "3",
            "Click Save, then click OK.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["save"], "PASS")
        # OK should be clicked because it's visible
        self.assertEqual(
            [event for event in page.events if event[0] == "click"],
            [("click", "button", "Save"), ("click", "button", "OK")],
        )
        # Note should NOT say "Clicked OK" if OK wasn't clicked
        self.assertIn("Save", outcome.notes["save"])
        self.assertIn("OK", outcome.notes["save"])

    def test_save_banner_inside_the_setup_frame_counts(self):
        page = Page(
            "https://example.lightning.force.com/lightning/setup/personalInformation",
            body="Skip to Navigation Skip to Main Content",
        )
        frame = Page("https://example.lightning.force.com/setup/personalInformation")
        page.frames = [frame]
        page.roles.add(("button", "Save"))

        def reveal(kind, name):
            if name == "Save":
                frame.body = "Your settings have been successfully saved."

        page.on_click = reveal
        item = step(
            "save", "Exercise 1: Get Started", "Task 1: Update your user.", "3",
            "Click Save at the bottom, then read the popup and click OK.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["save"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn("successfully saved", outcome.notes["save"])

    def test_sales_app_check_looks_for_sales(self):
        page = Page(
            "https://example.lightning.force.com/lightning/page/home",
            body="Sales Home Opportunities",
        )
        item = step(
            "sales", "Exercise 1: Get Started", "Task 2: Files.", "1",
            "In your Org (not within Setup), check to see if you are in the Sales app.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["sales"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn("Sales", outcome.notes["sales"])

    def test_dropdown_beside_a_named_file_then_downloads_it(self):
        page = Page("https://example.lightning.force.com/lightning/o/ContentDocument/home")
        page.roles.add(("row", "Flow Builder Quick Reference"))
        page.roles.add(("link", "Flow Builder Quick Reference"))
        page.roles.add(("button", "Show Actions"))
        page.children[("row", "Flow Builder Quick Reference")] = [
            ("link", "Flow Builder Quick Reference"), ("button", "Show Actions"),
        ]
        page.has_popup.add(("button", "Show Actions"))

        def reveal(kind, name):
            if name == "Show Actions":
                page.roles.add(("menuitem", "Download"))

        page.on_click = reveal
        item = step(
            "file", "Exercise 1: Get Started", "Task 2: Files.", "4",
            "Click the dropdown arrow to the far right of Flow Builder Quick Reference and click Download.",
        )
        clock, outcome = self.attempt(page, (item,))
        clicks = [event for event in page.events if event[0] == "click"]
        # Only the controls the step names: the row's dropdown, then Download in its menu.
        self.assertEqual(
            clicks, [("click", "button", "Show Actions"), ("click", "menuitem", "Download")],
        )
        self.assertNotIn(("click", "link", "Flow Builder Quick Reference"), clicks)
        # The walk has no path that reads a browser download (the runner sets
        # accept_downloads=False), so the download cannot be proven from the page.
        self.assertEqual(outcome.statuses["file"], "NEEDS_REVIEW")

    def test_dropdown_missing_from_the_named_row_is_a_mismatch(self):
        page = Page("https://example.lightning.force.com/lightning/o/ContentDocument/home")
        page.roles.add(("row", "Sample File"))
        page.roles.add(("link", "Sample File"))
        page.children[("row", "Sample File")] = [("link", "Sample File")]
        item = step(
            "file", "Exercise 1: Get Started", "Task 2: Files.", "4",
            "Click the dropdown arrow to the far right of Sample File and click Download.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["file"], "UI_MISMATCH")
        self.assertIn("dropdown in the Sample File row was not found", outcome.notes["file"])
        # Never fall back to the file link.
        self.assertEqual([event for event in page.events if event[0] == "click"], [])

    def test_files_tab_leaves_personal_information(self):
        page = Page("https://example.lightning.force.com/lightning/settings/personalInformation")
        page.roles.add(("link", "Files tab"))
        def on_goto(url):
            if "ContentDocument" in url:
                page.page_title = "Files | Salesforce"
        page.on_goto = on_goto
        item = step(
            "files", "Exercise 1: Get Started", "Task 2: Files.", "3",
            "Click the Files tab.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["files"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn("/lightning/o/ContentDocument/home", page.url)
        self.assertTrue(any(
            event[0] == "goto" and "ContentDocument" in event[1] for event in page.events
        ))

    def test_toolbox_note_is_not_the_click_target(self):
        page = Page("https://example.lightning.force.com/lightning/o/ContentDocument/home", body="Files")
        item = step(
            "var", "Exercise 2: Flows", "Task 1: Screen.", "3",
            "Create a record Variable to capture Opportunity information. "
            "NOTE: The Toolbox is hidden by default. To open it, click the icon in the upper-left corner, "
            "just below the back arrow.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["var"], "NEEDS_REVIEW")
        self.assertEqual(clock.now, 0)
        self.assertEqual(page.events, [])
        self.assertIn("no action for this step yet", outcome.notes["var"])

    def test_new_screen_flow_opens_the_builder(self):
        page = Page("https://example.lightning.force.com/lightning/page/home")

        def opened(url):
            if url.endswith("/lightning/setup/Flows/home"):
                page.roles.add(("button", "New Flow"))

        def reveal(kind, name):
            if name == "New Flow":
                page.body = "New Automation Screen Flow"
            if name == "Screen Flow":
                page.roles.add(("button", "Create"))
            if name == "Create":
                page.url = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
                page.body = "Free-Form"
                page.roles.add(("button", "Toggle Toolbox"))

        page.on_goto = opened
        page.on_click = reveal
        item = step(
            "flow", "Exercise 2: Flows", "Task 1: Screen.", "2",
            "Create a new Screen Flow.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["flow"], "PASS")
        self.assertIn("Flow Builder", outcome.notes["flow"])
        self.assertIn("flowBuilder", page.url)

    def test_screen_flow_choices_follow_the_builder_tab(self):
        flows = Page("https://example.lightning.force.com/lightning/setup/Flows/home")
        flows.roles.add(("button", "New Flow"))
        builder = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="New Automation Screen Flow",
        )

        class Ctx:
            def __init__(self):
                self.pages = [flows, builder]

        ctx = Ctx()
        flows.context = ctx
        builder.context = ctx

        def reveal(kind, name):
            if name == "Screen Flow":
                builder.body = "Free-Form"
                builder.roles.add(("button", "Toggle Toolbox"))

        flows.on_click = reveal
        builder.on_click = reveal
        item = step(
            "flowtab", "Exercise 2: Flows", "Task 1: Screen.", "2",
            "Create a new Screen Flow.",
        )
        outcome = self.walk(flows, (item,))
        self.assertEqual(outcome.statuses["flowtab"], "PASS")
        self.assertIn("flowBuilder", builder.url)
        self.assertNotIn("New Automation", builder.body)

    def test_record_variable_opens_new_resource(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("button", "Toggle Toolbox"))

        def reveal(kind, name):
            if name == "Toggle Toolbox":
                page.roles.add(("button", "New Resource"))
            if name == "New Resource":
                page.roles.add(("combobox", "Resource Type"))
                page.roles.add(("option", "Variable"))
            if name == "Variable":
                page.roles.add(("textbox", "API Name"))

        page.on_click = reveal
        item = step(
            "var", "Exercise 2: Flows", "Task 1: Screen.", "3",
            "Create a record Variable to capture Opportunity information. "
            "NOTE: The Toolbox is hidden by default. To open it, click the icon in the upper-left corner.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["var"], "PASS")
        self.assertIn("New Resource", outcome.notes["var"])
        self.assertIn(("click", "button", "New Resource"), page.events)

    def test_data_type_record_is_chosen_from_the_dropdown(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("combobox", "Data Type"))

        def reveal(kind, name):
            if name == "Data Type":
                page.roles.add(("option", "Record"))

        page.on_click = reveal
        item = step(
            "type", "Exercise 2: Flows", "Task 1: Screen.", "b",
            "Data Type: Record",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["type"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn("Data Type is Record", outcome.notes["type"])
        self.assertIn(("click", "combobox", "Data Type"), page.events)
        self.assertIn(("click", "option", "Record"), page.events)

    def test_fields_at_the_top_clicks_fields(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("tab", "Fields"))
        def on_click(kind, name):
            if kind == "tab" and name == "Fields":
                page.aria_states[("tab", "Fields", "aria-selected")] = "true"
        page.on_click = on_click
        item = step(
            "fields", "Exercise 2: Flows", "Task 1: Screen.", "d",
            "In the left panel, click Fields at the top.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["fields"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn(("click", "tab", "Fields"), page.events)

    def test_fields_tab_is_chosen_over_a_longer_fields_name(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("button", "Display Fields"))
        page.roles.add(("tab", "Fields"))
        original = page.get_by_role

        def get_by_role(role, name=None, exact=True):
            if role == "button" and name == "Fields" and not exact:
                return Loc(page, "button", "Display Fields")
            return original(role, name, exact)

        page.get_by_role = get_by_role
        def on_click(kind, name):
            if kind == "tab" and name == "Fields":
                page.aria_states[("tab", "Fields", "aria-selected")] = "true"
        page.on_click = on_click
        item = step(
            "fields", "Exercise 2: Flows", "Task 1: Screen.", "d",
            "In the left panel, click Fields at the top.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["fields"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn(("click", "tab", "Fields"), page.events)
        self.assertNotIn(("click", "button", "Display Fields"), page.events)

    def test_record_variable_uses_the_fields_tab(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.body = "New Screen"
        page.roles.add(("tab", "Fields"))

        def reveal(kind, name):
            if name == "Fields":
                page.roles.add(("combobox", "Record Variable"))
                page.roles.add(("option", "varWidgetRec"))

        page.on_click = reveal
        item = step(
            "variable", "Exercise 2: Flows", "Task 1: Screen.", "e",
            "Record Variable: varWidgetRec",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["variable"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn(("click", "tab", "Fields"), page.events)
        self.assertIn(("click", "option", "varWidgetRec"), page.events)

    def test_click_it_places_the_named_field(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.body = "New Screen"
        page.roles.add(("button", "Name"))
        def on_click(kind, name):
            if kind == "button" and name == "Name":
                # Field appears in the canvas (second occurrence of "Name")
                page.roles.add(("textbox", "Name"))
        page.on_click = on_click
        item = step(
            "name", "Exercise 2: Flows", "Task 1: Screen.", "f",
            "Scroll to find the Name field. Click it once to place it in the middle panel.",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["name"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn(("click", "button", "Name"), page.events)

    def test_screen_element_sets_its_label(self):
        page = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="New Resource",
        )
        page.roles.add(("button", "Done"))
        page.roles.add(("button", "Screen"))

        def reveal(kind, name):
            if name == "Done":
                page.body = "Screen Flow"
            if name == "Screen":
                page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertEqual(page.values["Label"], "scr001 Widget Input")
        clicks = [event for event in page.events if event[0] == "click"]
        self.assertEqual(
            clicks[:2],
            [("click", "button", "Done"), ("click", "button", "Screen")],
        )

    def test_object_lookup_chooses_the_matching_record(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("combobox", "Object"))
        page.values["Object"] = ""

        def filled(name, value):
            if name == "Object" and value == "Opportunity":
                page.roles.add(("option", "Opportunity"))

        page.on_fill = filled
        item = step(
            "object", "Exercise 2: Flows", "Task 1: Screen.", "c",
            "Object: Opportunity",
        )
        clock, outcome = self.attempt(page, (item,))
        self.assertEqual(outcome.statuses["object"], "PASS")
        self.assertEqual(clock.now, 0)
        self.assertIn(("click", "option", "Opportunity"), page.events)

    def test_auto_layout_plus_adds_the_screen(self):
        page = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="Click the elements you want to copy, and then click the copy button.",
        )
        page.roles.add(("button", "Close"))
        page.roles.add(("button", "Add Element"))

        def reveal(kind, name):
            if name == "Close":
                page.body = "Screen Flow"
            if name == "Add Element":
                page.roles.add(("button", "Screen"))
            if name == "Screen":
                page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertEqual(page.values["Label"], "scr001 Widget Input")
        clicks = [event for event in page.events if event[0] == "click"]
        self.assertEqual(
            clicks[:2],
            [("click", "button", "Close"), ("click", "button", "Add Element")],
        )
        self.assertIn(("click", "button", "Screen"), clicks)

    def test_unnamed_plus_under_start_is_clicked(self):
        page = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="Screen Flow",
        )
        page.roles.add(("button", "Screen Flow"))

        class Mouse:
            def click(self, x, y):
                page.events.append(("mouse", round(x), round(y)))
                page.roles.add(("button", "Screen"))

        page.mouse = Mouse()
        original = page.get_by_role

        def get_by_role(role, name=None, exact=True):
            loc = original(role, name, exact)
            if name == "Screen Flow":
                loc.bounding_box = lambda: {"x": 400, "y": 80, "width": 180, "height": 48}
            return loc

        page.get_by_role = get_by_role

        def reveal(kind, name):
            if name == "Screen":
                page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertIn(("mouse", 490, 156), page.events)
        self.assertIn(("click", "button", "Screen"), page.events)
        self.assertEqual(page.values["Label"], "scr001 Widget Input")

    def test_add_to_flow_opens_the_screen_list(self):
        dialog = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="New Automation\nScreen Flow\nScreen Automations",
        )
        canvas = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="Screen Flow",
        )
        canvas.roles.add(("button", "Add To Flow"))

        class Context:
            def __init__(self):
                self.pages = [dialog, canvas]

        context = Context()
        dialog.context = context
        canvas.context = context

        def close():
            dialog.events.append(("close",))
            context.pages = [canvas]

        dialog.close = close

        def reveal(kind, name):
            if name == "Add To Flow":
                canvas.roles.add(("menuitem", "Screen"))
            if name == "Screen":
                canvas.values["Label"] = ""

        canvas.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(dialog, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertIn(("close",), dialog.events)
        self.assertIn(("click", "button", "Add To Flow"), canvas.events)
        self.assertNotIn(("click", "text", "Screen Flow"), dialog.events)
        self.assertEqual(canvas.values["Label"], "scr001 Widget Input")

    def test_covering_dialog_is_closed_before_the_plus(self):
        page = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="New Automation\nScreen Flow",
        )
        page.roles.add(("button", "Close"))
        page.roles.add(("button", "Add To Flow"))

        def reveal(kind, name):
            if name == "Close":
                page.body = "Screen Flow"
            if name == "Add To Flow":
                page.roles.add(("menuitem", "Screen"))
            if name == "Screen":
                page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        names = [event[2] for event in page.events if event[0] == "click"]
        self.assertEqual(names[0], "Close")
        self.assertIn("Add To Flow", names)
        self.assertEqual(page.values["Label"], "scr001 Widget Input")

    def test_flow_label_column_is_not_the_screen_label(self):
        page = Page(
            "https://example.lightning.force.com/lightning/setup/Flows/home",
            body="Flow Label",
        )
        page.roles.add(("textbox", "Flow Label"))
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "UI_MISMATCH")
        self.assertNotIn(("fill", "Label", "scr001 Widget Input"), page.events)
        self.assertNotIn(("fill", "Flow Label", "scr001 Widget Input"), page.events)

    def test_open_canvas_is_not_sent_back_to_new_automation(self):
        dialog = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="New Automation\nScreen Flow\nScreen Automations",
        )
        canvas = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="Screen Flow",
        )
        canvas.roles.add(("button", "Screen Flow"))

        class Context:
            def __init__(self):
                self.pages = [dialog, canvas]

        context = Context()
        dialog.context = context
        canvas.context = context

        class Mouse:
            def click(self, x, y):
                canvas.events.append(("mouse", round(x), round(y)))
                canvas.roles.add(("button", "Screen"))

        canvas.mouse = Mouse()
        original = canvas.get_by_role

        def get_by_role(role, name=None, exact=True):
            loc = original(role, name, exact)
            if name == "Screen Flow":
                loc.bounding_box = lambda: {"x": 400, "y": 80, "width": 180, "height": 48}
            return loc

        canvas.get_by_role = get_by_role

        def reveal(kind, name):
            if name == "Screen":
                canvas.values["Label"] = ""

        canvas.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(dialog, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertIn(("mouse", 490, 156), canvas.events)
        self.assertEqual(dialog.events, [])
        self.assertEqual(canvas.values["Label"], "scr001 Widget Input")

    def test_adding_a_screen_does_not_change_canvas_mode(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("button", "Auto-Layout"))
        page.roles.add(("button", "Add Element"))

        def reveal(kind, name):
            if name == "Add Element":
                page.roles.add(("button", "Screen"))
            if name == "Screen":
                page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        names = [event[2] for event in page.events if event[0] == "click"]
        self.assertNotIn("Auto-Layout", names)
        self.assertNotIn("Free-Form", names)
        self.assertIn("Add Element", names)
        self.assertIn("Screen", names)

    def test_second_click_places_screen_on_the_canvas(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("button", "Screen"))
        page.screen_clicks = 0

        def reveal(kind, name):
            if name != "Screen":
                return
            page.screen_clicks += 1
            page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertGreaterEqual(page.screen_clicks, 1)
        self.assertEqual(page.values["Label"], "scr001 Widget Input")

    def test_screen_is_dragged_onto_the_canvas(self):
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("button", "Screen"))

        class Mouse:
            def __init__(self):
                self.events = []

            def move(self, x, y, steps=1):
                self.events.append(("move", x, y))

            def down(self):
                self.events.append(("down",))

            def up(self):
                self.events.append(("up",))
                page.values["Label"] = ""

        page.mouse = Mouse()
        page.on_click = lambda kind, name: page.values.__setitem__("Label", "") if name == "Screen" else None

        def box():
            return {"x": 40, "y": 180, "width": 120, "height": 28}

        original = page.get_by_role

        def get_by_role(role, name=None, exact=True):
            loc = original(role, name, exact)
            loc.bounding_box = box
            return loc

        page.get_by_role = get_by_role
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertEqual(page.mouse.events, [])
        self.assertNotIn(("click", "button", "Auto-Layout"), page.events)
        self.assertEqual(page.values["Label"], "scr001 Widget Input")

    def test_canvas_mode_is_not_switched_when_step_does_not_name_it(self):
        """Guide-only: if the step does not name canvas mode, never switch Auto-Layout/Free-Form."""
        page = Page("https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app")
        page.roles.add(("button", "Free-Form"))
        page.roles.add(("button", "Add Element"))

        def reveal(kind, name):
            if name == "Add Element":
                page.roles.add(("menuitem", "Screen"))
            if name == "Screen":
                page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        names = [event[2] for event in page.events if event[0] == "click"]
        # Verify Free-Form was NOT clicked (mode was not switched)
        self.assertNotIn("Free-Form", names)
        self.assertNotIn("Auto-Layout", names)
        self.assertIn("Screen", names)
        self.assertEqual(page.values["Label"], "scr001 Widget Input")

    def test_visible_screen_flow_words_are_clicked(self):
        page = Page(
            "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app",
            body="New Automation\nScreen Flow\nScreen Automations",
        )

        def reveal(kind, name):
            if name == "Screen Flow":
                page.body = "Start"
                page.roles.add(("menuitem", "Screen"))
            if name == "Screen":
                page.values["Label"] = ""

        page.on_click = reveal
        item = step(
            "screen", "Exercise 2: Flows", "Task 1: Screen.", "a",
            "Add a Screen Element with Label scr001 Widget Input",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["screen"], "PASS")
        self.assertIn(("click", "text", "Screen Flow"), page.events)
        self.assertEqual(page.values["Label"], "scr001 Widget Input")

    def test_click_whose_words_are_on_the_page_is_performed(self):
        page = Page("https://example.lightning.force.com/lightning/page/home")
        page.html = '<button title="App Launcher"></button>'
        def on_click(kind, name):
            # App Launcher opens, showing new controls (but no step-mentioned names appear)
            if name == "App Launcher":
                page.body = "Sales\nService\nMarketing"
                page.roles.add(("link", "Sales"))
        page.on_click = on_click
        item = step(
            "open", "Exercise 9: Widgets", "Task 1: Open it.", "1",
            "Click the App Launcher.",
        )
        clock, outcome = self.attempt(page, (item,))
        # No readable after-state evidence (new names not in step text, no URL change)
        self.assertEqual(outcome.statuses["open"], "NEEDS_REVIEW")
        self.assertGreater(page.reads, 0)
        self.assertTrue(any(
            event[0] == "click" and "App Launcher" in event for event in page.events
        ))
        self.assertEqual(clock.now, 0)

    def test_missing_control_fails_only_after_research_and_five_minutes(self):
        calls = []

        def research(state, step, name):
            calls.append((state.clock(), name))
            if state.clock() < PAGE_WAIT_S:
                return ResearchResult(pending=True)
            return ResearchResult(pending=False, candidate="")

        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        items = (
            step(
                "a", "Exercise 9: Widgets", "Task 1: Archive.", "1",
                "Click the Archive Ledger.",
            ),
            step(
                "b", "Exercise 9: Widgets", "Task 1: Archive.", "2",
                "Click the Next Ledger.",
            ),
        )
        clock, outcome = self.attempt(page, items, research=research)
        self.assertGreaterEqual(clock.now, PAGE_WAIT_S)
        self.assertTrue(any(stamp < PAGE_WAIT_S for stamp, _name in calls))
        self.assertTrue(any(stamp >= PAGE_WAIT_S for stamp, _name in calls))
        self.assertGreaterEqual(calls[1][0] - calls[0][0], PAGE_POLL_S)
        self.assertEqual(outcome.statuses["a"], "UI_MISMATCH")
        self.assertIn("Archive Ledger", outcome.notes["a"])
        self.assertEqual(outcome.statuses["b"], "NOT_RUN")
        self.assertNotIn("b", outcome.executed)
        self.assertFalse(any(event[0] == "click" for event in page.events))

    def test_research_still_running_at_five_minutes_is_not_a_fail(self):
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Home")
        page.held = False
        pending_at_limit = []

        def research(state, step, name):
            if state.clock() < PAGE_WAIT_S:
                return ResearchResult(pending=True)
            if not page.held:
                page.held = True
                pending_at_limit.append(state.clock())
                return ResearchResult(pending=True)
            page.roles.add(("button", "Ledger Archive"))
            return ResearchResult(pending=False, candidate="Ledger Archive")

        item = step(
            "a", "Exercise 9: Widgets", "Task 1: Archive.", "1",
            "Click the Archive Ledger.",
        )
        clock, outcome = self.attempt(page, (item,), research=research)
        self.assertTrue(page.held)
        self.assertTrue(pending_at_limit)
        self.assertGreaterEqual(pending_at_limit[0], PAGE_WAIT_S)
        self.assertGreater(clock.now, PAGE_WAIT_S)
        self.assertEqual(outcome.statuses["a"], "UI_MISMATCH")
        self.assertIn("A similar control is on the page: Ledger Archive", outcome.notes["a"])
        self.assertIn("It was not clicked", outcome.notes["a"])
        self.assertFalse(any(event[0] == "click" for event in page.events))

    def test_pass_waits_until_the_expected_text_or_control_state_is_read(self):
        clock = Clock()
        page = Page("https://example.lightning.force.com/lightning/page/home", body="Loading")

        def sleeper(seconds):
            clock.now += seconds
            if clock.now >= PAGE_POLL_S:
                page.body = "The quota is 50."

        item = step(
            "see", "Exercise 9: Widgets", "Task 1: Look.", "1",
            "You should see the quota is 50.",
        )
        outcome = walk_guide(
            page, inventory_of(item), checks=(),
            timeout_s=0, interval_s=1, clock=clock, sleeper=sleeper,
        )
        self.assertEqual(outcome.statuses["see"], "PASS")
        self.assertGreaterEqual(clock.now, PAGE_POLL_S)
        self.assertNotIn("page was open", outcome.notes["see"].lower())
        self.assertIn("quota is 50", outcome.notes["see"])
        loading_at = next(i for i, text in enumerate(page.snapshots) if "Loading" in text)
        quota_at = next(i for i, text in enumerate(page.snapshots) if "quota is 50" in text)
        self.assertLess(loading_at, quota_at)

        late = Page("https://example.lightning.force.com/lightning/setup/home")
        late.roles.add(("switch", "Widget"))
        late.checked[("switch", "Widget")] = "true"
        late.reveal_state_after = 2
        toggle = step(
            "state", "Exercise 9: Widgets", "Task 2: Enable it.", "1",
            "Toggle Widget to On.",
        )
        state_clock, state_outcome = self.attempt(late, (toggle,))
        self.assertEqual(state_outcome.statuses["state"], "PASS")
        self.assertGreaterEqual(late.state_reads, 2)
        self.assertEqual(late.events, [])
        self.assertEqual(state_clock.now, 0)

    def test_password_change_is_skipped_and_the_next_step_still_runs(self):
        page = Page("https://example.lightning.force.com/lightning/settings/personal/home")
        page.roles.add(("button", "Save"))
        def on_click(kind, name):
            if name == "Save":
                page.body = "Your settings have been successfully saved."
        page.on_click = on_click
        items = (
            step(
                "a", "Exercise 9: Widgets", "Task 1: Security.", "1",
                "Enter your current password and a new password.",
            ),
            step(
                "b", "Exercise 9: Widgets", "Task 1: Security.", "2",
                "Click Save.",
            ),
            step(
                "c", "Exercise 9: Widgets", "Task 1: Security.", "3",
                "Click Save.",
            ),
        )
        _clock, outcome = self.attempt(page, items)
        self.assertEqual(outcome.statuses["a"], "NOT_RUN")
        self.assertIn("lock the trial org", outcome.notes["a"])
        # The Save right after the skipped password form would submit it.
        self.assertEqual(outcome.statuses["b"], "NOT_RUN")
        self.assertIn("password form", outcome.notes["b"])
        self.assertEqual(outcome.statuses["c"], "PASS")
        self.assertIn(("click", "button", "Save"), page.events)


class LiveWaitTests(unittest.TestCase):
    def test_first_loading_poll_is_not_a_failure(self):
        clock = Clock()
        calls = {"n": 0}

        def reader(page):
            calls["n"] += 1
            if calls["n"] == 1:
                return {
                    "panel_found": False, "text": "", "messages": [], "spinner": True,
                    "loading_word": True, "loading_assistant": True, "agent_loading": False,
                    "empty_reply": False, "nothing_here_yet": False, "lets_chat": False,
                    "details_waiting": False, "error_text": None,
                }
            return {
                "panel_found": True,
                "text": "Hello! I'm a simple agent here to say hi.",
                "messages": [], "spinner": False, "loading_word": False,
                "loading_assistant": False, "agent_loading": False, "empty_reply": False,
                "nothing_here_yet": False, "lets_chat": False, "details_waiting": False,
                "error_text": None,
            }

        result = wait_for_live_test(
            None, expect="Hello! I'm a simple agent here to say hi.",
            timeout_s=90, interval_s=2, reader=reader, clock=clock, sleeper=clock.sleep,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["settled"])
        self.assertGreaterEqual(calls["n"], 2)

    def test_settled_mismatch_fails_only_after_the_wait(self):
        clock = Clock()
        calls = {"n": 0}

        def reader(page):
            calls["n"] += 1
            return {
                "panel_found": True, "text": "Live Test", "messages": [],
                "spinner": False, "loading_word": False, "loading_assistant": False,
                "agent_loading": False, "empty_reply": False, "nothing_here_yet": False,
                "lets_chat": False, "details_waiting": False, "error_text": None,
            }

        result = wait_for_live_test(
            None, expect="Hello! I'm a simple agent here to say hi.",
            timeout_s=0, interval_s=1, reader=reader, clock=clock, sleeper=clock.sleep,
        )
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(result["settled"])
        self.assertIn("settled without", result["reason"])

    def test_still_loading_at_the_time_limit_is_not_a_failure(self):
        clock = Clock()

        def reader(page):
            return {
                "panel_found": False, "text": "", "messages": [], "spinner": True,
                "loading_word": True, "loading_assistant": False, "agent_loading": False,
                "empty_reply": True, "nothing_here_yet": True, "lets_chat": False,
                "details_waiting": True, "error_text": None,
            }

        result = wait_for_live_test(
            None, expect="Hello! I'm a simple agent here to say hi.",
            timeout_s=0, interval_s=1, reader=reader, clock=clock, sleeper=clock.sleep,
        )
        self.assertIsNone(result["status"])
        self.assertFalse(result["settled"])
        self.assertIn("still loading", result["reason"])


class GuideFileTests(unittest.TestCase):
    def test_real_guide_pdf_parses_and_matches_the_builtin_checks(self):
        import os
        env_path = os.environ.get("WV_PRIVATE_GUIDE")
        if not env_path:
            self.skipTest("WV_PRIVATE_GUIDE not set")
        path = Path(env_path)
        if not path.exists():
            self.skipTest("guide pdf is not on this machine")
        inventory = extract_inventory(path)
        self.assertGreaterEqual(len(inventory.steps), 150)
        matched = []
        for item in inventory.steps:
            names = [check.name for check in builtin_checks() if check.matches(item)]
            self.assertLessEqual(len(names), 1)
            if names:
                matched.append(names[0])
        self.assertGreaterEqual(len(matched), 25)
        self.assertEqual(matched[0], "Open OrgClaim")
        board = build_board(inventory, notice="The PDF was read.", run_id="agt503")
        self.assertGreaterEqual(len(board.sections), 2)
        self.assertEqual(board.not_run, len(inventory.steps))


class GuideOnlyTests(unittest.TestCase):
    """Suggestions are never clicked. Reviewed aliases keep going but never PASS."""

    def walk(self, page, items, aliases=None):
        clock = Clock()
        outcome = walk_guide(
            page, inventory_of(*items), checks=(),
            timeout_s=0, interval_s=1, clock=clock, sleeper=clock.sleep,
            research=lambda state, step, name: ResearchResult(pending=False, candidate=""),
            aliases=aliases,
        )
        return outcome

    def items(self):
        return (
            step("a", "Exercise 9: Widgets", "Task 1: Save it.", "1", "Click Save."),
            step("b", "Exercise 9: Widgets", "Task 1: Save it.", "2", "Click Done."),
        )

    def test_reviewed_alias_continues_but_the_step_stays_a_mismatch(self):
        page = Page("https://example.lightning.force.com/lightning/page/home",
                    roles={("button", "Save & Close"), ("button", "Done")})
        def on_click(kind, name):
            # Use success banner as realistic after-state
            page.body = "Your settings have been successfully saved."
        page.on_click = on_click
        outcome = self.walk(page, self.items(), aliases={"Save": "Save & Close"})
        self.assertEqual(outcome.statuses["a"], "UI_MISMATCH")
        self.assertIn('Guide says "Save"', outcome.notes["a"])
        self.assertIn('"Save & Close"', outcome.notes["a"])
        self.assertTrue(outcome.evidence["a"])
        self.assertEqual(outcome.statuses["b"], "PASS")
        self.assertIn("b", outcome.executed)

    def test_alias_is_not_used_when_the_guide_name_is_on_the_page(self):
        page = Page("https://example.lightning.force.com/lightning/page/home",
                    roles={("button", "Save"), ("button", "Save & Close"), ("button", "Done")})
        def on_click(kind, name):
            # Use success banner as realistic after-state
            page.body = "Your settings have been successfully saved."
        page.on_click = on_click
        outcome = self.walk(page, self.items(), aliases={"Save": "Save & Close"})
        self.assertEqual(outcome.statuses["a"], "PASS")
        clicks = [event for event in page.events if event[0] == "click"]
        self.assertIn(("click", "button", "Save"), clicks)
        self.assertNotIn(("click", "button", "Save & Close"), clicks)

    def test_without_an_alias_a_renamed_control_freezes_the_run(self):
        page = Page("https://example.lightning.force.com/lightning/page/home",
                    roles={("button", "Save & Close"), ("button", "Done")})
        outcome = self.walk(page, self.items())
        self.assertEqual(outcome.statuses["a"], "UI_MISMATCH")
        self.assertEqual(outcome.statuses["b"], "NOT_RUN")
        self.assertFalse([event for event in page.events if event[0] == "click"])

    def test_unreadable_toggle_is_never_clicked(self):
        page = Page("https://example.lightning.force.com/lightning/setup/home",
                    roles={("switch", "Einstein")})
        item = step("t", "Exercise 9: Widgets", "Task 1: Enable it.", "1", "Toggle Einstein to On.")
        with patch("workshop_validator.guide_walk._read_toggle", return_value=(True, "unread")):
            outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["t"], "NEEDS_REVIEW")
        self.assertIn("not clicked", outcome.notes["t"])
        self.assertEqual(page.events, [])

    def test_off_toggle_is_clicked_then_read_again(self):
        page = Page("https://example.lightning.force.com/lightning/setup/home",
                    roles={("switch", "Einstein")})
        page.checked[("switch", "Einstein")] = "false"

        def flip(kind, name):
            page.checked[(kind, name)] = "true"

        page.on_click = flip
        item = step("t", "Exercise 9: Widgets", "Task 1: Enable it.", "1", "Toggle Einstein to On.")
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["t"], "PASS")
        self.assertNotIn("already on", outcome.notes["t"])
        self.assertEqual(page.events, [("click", "switch", "Einstein")])

    def test_parent_sentence_is_carried_out_by_its_substeps(self):
        page = Page("https://example.lightning.force.com/lightning/page/home",
                    roles={("button", "Done")})
        def on_click(kind, name):
            # Wizard closes - no readable after-state
            pass
        page.on_click = on_click
        parent = step("p", "Exercise 9: Widgets", "Task 1: Finish.", "1", "Finish the widget wizard.")
        child = GuideStep("c", 1, "Exercise 9: Widgets", "Task 1: Finish.", "a", "Click Done.",
                          (90.0, 30.0, 400.0, 44.0), "p", ())
        outcome = self.walk(page, (parent, child))
        self.assertEqual(outcome.statuses["p"], "NOT_RUN")
        self.assertIn("following steps carry it out", outcome.notes["p"])
        # Done button closes wizard - no readable after-state evidence
        self.assertEqual(outcome.statuses["c"], "NEEDS_REVIEW")


class _Match:
    def __init__(self, shown, clicks):
        self.shown, self.clicks = shown, clicks

    def is_visible(self):
        return self.shown

    def click(self, timeout=None):
        self.clicks.append(self.shown)


class _Matches:
    def __init__(self, shown, clicks):
        self.items = [_Match(item, clicks) for item in shown]
        self.first = self.items[0] if self.items else None

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _HiddenFirstPage:
    frames = ()

    def __init__(self):
        self.clicks = []

    def get_by_role(self, role, name=None, exact=False):
        return _Matches([False, True], self.clicks)


class VisibleMatchTests(unittest.TestCase):
    def test_a_hidden_copy_ahead_of_the_open_menu_is_skipped(self):
        from workshop_validator.guide_walk import click_role, present

        page = _HiddenFirstPage()
        self.assertTrue(present(page, "link", "Settings"))
        self.assertTrue(click_role(page, "link", "Settings"))
        self.assertEqual(page.clicks, [True])


class PasswordSkipTests(unittest.TestCase):
    def test_password_fields_and_their_save_are_never_run(self):
        from workshop_validator.guide_inventory import GuideStep
        from workshop_validator.guide_walk import PASSWORD_SAVE_NOTE, _password_skip

        def step(step_id, text, parent=None):
            return GuideStep(step_id, 1, None, None, "a", text, (0, 0, 0, 0), parent, ())

        rows = [
            step("d", "Click Change My Password. Complete the following fields:", "p"),
            step("d1", "Current Password: (your current password)", "d"),
            step("d2", "Verify New Password: (same)", "d"),
            step("d3", "Security Question: (pick one)", "d"),
            step("e", "Click Save.", "p"),
            step("f", "Click the Files tab.", None),
        ]
        form, seen = None, []
        for row in rows:
            skipped = _password_skip(row, form)
            if skipped is None:
                form = None
                seen.append((row.step_id, "RUN"))
                continue
            form, result = skipped
            seen.append((row.step_id, result.status))
        self.assertEqual(seen, [("d", "NOT_RUN"), ("d1", "NOT_RUN"), ("d2", "NOT_RUN"),
                                ("d3", "NOT_RUN"), ("e", "NOT_RUN"), ("f", "RUN")])
        self.assertEqual(_password_skip(rows[4], "d")[1].note, PASSWORD_SAVE_NOTE)
        self.assertIsNone(_password_skip(rows[4], None))


class OwnValueTests(unittest.TestCase):
    def test_a_described_value_is_never_typed(self):
        from workshop_validator.guide_walk import _intent_for

        self.assertEqual(_intent_for("First Name: (your first name)").kind, "own")
        own = _intent_for("Email: (an email address that you can access from the classroom)")
        self.assertEqual((own.kind, own.target), ("own", "Email"))
        self.assertEqual(_intent_for("API Name: varWidget").kind, "fill")
        self.assertNotEqual(_intent_for("Description: (leave blank)").kind, "own")

    def test_a_filled_field_is_kept_and_an_empty_one_needs_review(self):
        from workshop_validator import guide_walk as g

        class Field:
            def __init__(self, value):
                self.value = value

            def count(self):
                return 1

            def nth(self, index):
                return self

            def is_visible(self):
                return True

            def input_value(self, timeout=None):
                return self.value

            def fill(self, *args, **kwargs):
                raise AssertionError("an own value must not be typed")

        # Prefilled field -> PASS
        step = g.GuideStep("s1", 1, None, None, "i", "Email: (your email)", (0, 0, 0, 0), "p", ())
        state = SimpleNamespace(page=object())
        with patch.object(g, "labeled", return_value=Field("ada@example.test")):
            result = g._act_own(state, step, "Email")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.evidence_ids, ("s1:already-set:Email",))

        # Empty optional field -> NOT_RUN
        with patch.object(g, "labeled", return_value=Field("")):
            result = g._act_own(state, step, "Email")
            self.assertEqual(result.status, "NOT_RUN")
            self.assertFalse(result.freeze)

        # Empty mandatory field -> NEEDS_REVIEW with freeze
        mandatory_step = g.GuideStep("s2", 1, None, None, "i", "Email is required: (your email)", (0, 0, 0, 0), "p", ())
        with patch.object(g, "labeled", return_value=Field("")):
            result = g._act_own(state, mandatory_step, "Email")
            self.assertEqual(result.status, "NEEDS_REVIEW")
            self.assertTrue(result.freeze)

        # Field not found -> None
        with patch.object(g, "labeled", return_value=None):
            self.assertIsNone(g._act_own(state, step, "Email"))

    def test_field_mandatory_detection(self):
        from workshop_validator.guide_walk import _is_field_mandatory

        # Positive cases: "required"
        self.assertTrue(_is_field_mandatory("Email is required: (your email)", "Email"))
        self.assertTrue(_is_field_mandatory("The Name field is required.", "Name"))

        # Positive cases: "mandatory"
        self.assertTrue(_is_field_mandatory("This field is mandatory.", "field"))
        self.assertTrue(_is_field_mandatory("Enter a mandatory value.", "value"))

        # Positive cases: "must" in same clause as field label
        self.assertTrue(_is_field_mandatory("You must enter your Email.", "Email"))
        self.assertTrue(_is_field_mandatory("The Name must be filled.", "Name"))

        # Negative cases: "must" about a different field
        self.assertFalse(_is_field_mandatory("You must click Save. Email: (your email)", "Email"))
        self.assertFalse(_is_field_mandatory("Name must be filled. Address: (your address)", "Address"))

        # Negative cases: no mandatory markers
        self.assertFalse(_is_field_mandatory("Email: (your email)", "Email"))
        self.assertFalse(_is_field_mandatory("Enter your Name.", "Name"))
        self.assertFalse(_is_field_mandatory("Fill in the Address field.", "Address"))

    def test_optional_empty_field_does_not_freeze(self):
        """Optional empty field returns NOT_RUN without freeze."""
        from workshop_validator import guide_walk as g

        class Field:
            def __init__(self, value):
                self.value = value
            def count(self):
                return 1
            def nth(self, index):
                return self
            def is_visible(self):
                return True
            def input_value(self, timeout=None):
                return self.value
            def fill(self, *args, **kwargs):
                raise AssertionError("an own value must not be typed")

        optional_step = g.GuideStep("s1", 1, None, None, "i", "Email: (your email)", (0, 0, 0, 0), "p", ())
        state = SimpleNamespace(page=object())
        with patch.object(g, "labeled", return_value=Field("")):
            result = g._act_own(state, optional_step, "Email")
        self.assertEqual(result.status, "NOT_RUN")
        self.assertFalse(result.freeze)

    def test_mandatory_empty_field_freezes_walk(self):
        """Mandatory empty field returns NEEDS_REVIEW with freeze=True."""
        from workshop_validator import guide_walk as g

        class Field:
            def __init__(self, value):
                self.value = value
            def count(self):
                return 1
            def nth(self, index):
                return self
            def is_visible(self):
                return True
            def input_value(self, timeout=None):
                return self.value
            def fill(self, *args, **kwargs):
                raise AssertionError("an own value must not be typed")

        mandatory_step = g.GuideStep("s2", 1, None, None, "i", "Email is required: (your email)", (0, 0, 0, 0), "p", ())
        state = SimpleNamespace(page=object())
        with patch.object(g, "labeled", return_value=Field("")):
            result = g._act_own(state, mandatory_step, "Email")
        self.assertEqual(result.status, "NEEDS_REVIEW")
        self.assertTrue(result.freeze)


class StepReadingTests(unittest.TestCase):
    def _step(self, text):
        return GuideStep("s1", 1, None, None, "a", text, (0, 0, 0, 0), None, ())

    def test_chained_clicks_and_trailing_words_are_trimmed(self):
        from workshop_validator.guide_walk import _intent_for, _named_clicks

        self.assertEqual(_named_clicks("If not, then click the Widget Menu icon and click Gadgets."),
                         ["Widget Menu", "Gadgets"])
        self.assertEqual(_intent_for("Likewise, click the Color field to add it.").target, "Color")

    def test_outside_and_if_not_steps_are_recorded_without_acting(self):
        from workshop_validator.guide_walk import _side_skip

        self.assertEqual(_side_skip(self._step("Move the downloaded file to your desktop."), None).status,
                         "NOT_RUN")
        if_not = self._step("If not, then click Gadgets.")
        self.assertEqual(_side_skip(if_not, "PASS").status, "NOT_RUN")
        self.assertIsNone(_side_skip(if_not, "UI_MISMATCH"))
        self.assertIsNone(_side_skip(self._step("Click Save."), "PASS"))

    def test_an_unreadable_target_is_not_called_a_guide_mismatch(self):
        from workshop_validator import guide_walk as g

        state = SimpleNamespace(page=object())
        with patch.object(g, "_read_page_text", return_value="Home"):
            clause = g._freeze_result(state, self._step("x"), "dropdown arrow to the far right and click")
            name = g._freeze_result(state, self._step("x"), "Save")
        self.assertEqual(clause.status, "NEEDS_REVIEW")
        self.assertTrue(clause.freeze)
        self.assertEqual(name.status, "UI_MISMATCH")

    def test_a_trailing_role_word_is_dropped_only_when_the_full_name_is_missing(self):
        from workshop_validator import guide_walk as g

        state = SimpleNamespace(page=SimpleNamespace(url="https://example.com/page"))
        clicked = []
        # Mock _visible_match to return a locator that simulates aria-selected=true
        mock_loc = SimpleNamespace()
        mock_loc.get_attribute = lambda attr: "true" if attr == "aria-selected" else None
        with patch.object(g, "_control_visible", side_effect=lambda page, name: name == "Widgets"), \
                patch.object(g, "_locator_for_name", side_effect=lambda page, name: name), \
                patch.object(g, "_click_locator", side_effect=lambda loc: clicked.append(loc) or True), \
                patch.object(g, "_in_setup", return_value=False), \
                patch.object(g, "_read_page_text", return_value=""), \
                patch.object(g, "_visible_names_snapshot", return_value=frozenset()), \
                patch.object(g, "_visible_match", return_value=mock_loc):
            result = g._act_click(state, self._step("Click the Widgets tab."), "Widgets tab")
            missing = g._act_click(state, self._step("Click the Gadgets tab."), "Gadgets tab")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(clicked, ["Widgets"])
        self.assertIsNone(missing)


class DefaultsTests(unittest.TestCase):
    def test_page_wait_default_is_90_seconds(self):
        """CLAUDE.md: poll ~2 s, up to ~90 s"""
        self.assertEqual(PAGE_WAIT_S, 90.0)


if __name__ == "__main__":
    unittest.main()
