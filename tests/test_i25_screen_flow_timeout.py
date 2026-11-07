"""Test Screen Flow selection with delayed dialog appearance."""
import unittest

from workshop_validator import guide_walk
from workshop_validator.guide_walk import walk_guide
from workshop_validator.guide_inventory import GuideInventory, GuideStep


def step(step_id, exercise, task, number, text):
    return GuideStep(
        step_id, 1, exercise, task, number, text,
        (90.0, 10.0, 400.0, 24.0), None, (),
    )


def inventory_of(*items):
    return GuideInventory(1, tuple(items), (), ())


def as_builder(page):
    """Mark page as Flow Builder with visible canvas and Toolbox."""
    page.roles.add(("css", getattr(guide_walk, "_BUILDER_CANVAS_SELECTOR", "")))
    page.roles.add(("button", "Toggle Toolbox"))


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class Page:
    def __init__(self, url, body=""):
        self.url = url
        self.body = body
        self.roles = set()
        self.events = []
        self.page_title = ""
        self.html = ""
        self.spinning = False
        self.context = None

    def title(self):
        return self.page_title

    def locator(self, selector):
        if selector == "body":
            return Loc(self, "body")
        if "spinner" in selector or "progressbar" in selector:
            return Loc(self, "spinner")
        return Loc(self, "css", selector)

    def get_by_role(self, role, name=None, exact=True):
        return Loc(self, role, name or "")

    def get_by_text(self, text, exact=True):
        return Loc(self, "text", text)

    def content(self):
        return self.html or ""

    def goto(self, url, wait_until=None, timeout=None):
        self.events.append(("goto", url))
        self.url = url
        opened = getattr(self, "on_goto", None)
        if opened is not None:
            opened(url)


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
        if self.kind == "text":
            return 1 if self.name in self.page.body else 0
        return 1 if (self.kind, self.name) in self.page.roles else 0

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.count() > 0

    def click(self, timeout=None):
        self.page.events.append(("click", self.kind, self.name))
        reveal = getattr(self.page, "on_click", None)
        if reveal is not None:
            reveal(self.kind, self.name)

    def inner_text(self, timeout=None):
        if self.kind == "body":
            return self.page.body
        return self.name if self.name else ""

    def nth(self, index):
        return self


class TestScreenFlowTimeout(unittest.TestCase):
    def walk(self, page, items, timeout_s=90, interval_s=2):
        clock = Clock()

        def sleep(seconds):
            clock.now += seconds

        return walk_guide(
            page, inventory_of(*items),
            timeout_s=timeout_s, interval_s=interval_s, clock=clock,
            sleeper=sleep,
        )

    def test_screen_flow_appears_immediately(self):
        """Screen Flow option visible immediately after New Flow click passes."""
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
                as_builder(page)

        page.on_goto = opened
        page.on_click = reveal
        item = step(
            "sf1", "Exercise 1", "Task: Create Flow", "1",
            "Create a new Screen Flow.",
        )
        outcome = self.walk(page, (item,))
        self.assertEqual(outcome.statuses["sf1"], "PASS")
        self.assertIn(("click", "button", "New Flow"), page.events)
        self.assertIn(("click", "text", "Screen Flow"), page.events)

    def test_screen_flow_appears_after_delay(self):
        """Screen Flow option appearing after delay (dialog load) passes."""
        page = Page("https://example.lightning.force.com/lightning/page/home")

        dialog_opened = {"done": False}

        def opened(url):
            if url.endswith("/lightning/setup/Flows/home"):
                page.roles.add(("button", "New Flow"))

        def reveal(kind, name):
            if name == "New Flow" and not dialog_opened["done"]:
                dialog_opened["done"] = True
                # Dialog appears but Screen Flow takes time to render
            if name == "Screen Flow":
                page.roles.add(("button", "Create"))
            if name == "Create":
                page.url = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
                page.body = "Free-Form"
                as_builder(page)

        page.on_goto = opened
        page.on_click = reveal

        clock = Clock()

        def sleep(seconds):
            clock.now += seconds
            # Screen Flow appears after 4 seconds
            if clock.now >= 4 and dialog_opened["done"]:
                page.body = "New Automation Screen Flow"

        item = step(
            "sf2", "Exercise 1", "Task: Create Flow", "1",
            "Create a new Screen Flow.",
        )
        outcome = walk_guide(
            page, inventory_of(item),
            timeout_s=90, interval_s=2, clock=clock,
            sleeper=sleep,
        )
        self.assertEqual(outcome.statuses["sf2"], "PASS")
        self.assertIn(("click", "button", "New Flow"), page.events)
        self.assertIn(("click", "text", "Screen Flow"), page.events)
        # Verify it waited (at least 4 seconds for option to appear)
        self.assertGreaterEqual(clock.now, 4)

    def test_screen_flow_in_separate_page(self):
        """Screen Flow in a separate browser page context is found."""
        flows = Page("https://example.lightning.force.com/lightning/setup/Flows/home")
        flows.roles.add(("button", "New Flow"))

        dialog = Page(
            "https://example.lightning.force.com/lightning/setup/Flows/dialog",
            body="New Automation Screen Flow",
        )

        class Ctx:
            def __init__(self):
                self.pages = [flows, dialog]

        ctx = Ctx()
        flows.context = ctx
        dialog.context = ctx

        def reveal_flows(kind, name):
            if name == "New Flow":
                # Dialog opens in separate page
                pass

        def reveal_dialog(kind, name):
            if name == "Screen Flow":
                dialog.roles.add(("button", "Create"))
            if name == "Create":
                dialog.url = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
                dialog.body = "Free-Form"
                as_builder(dialog)

        flows.on_click = reveal_flows
        dialog.on_click = reveal_dialog

        item = step(
            "sf3", "Exercise 1", "Task: Create Flow", "1",
            "Create a new Screen Flow.",
        )
        outcome = self.walk(flows, (item,))
        self.assertEqual(outcome.statuses["sf3"], "PASS")

    def test_screen_flow_never_appears(self):
        """Screen Flow never appearing causes UI_MISMATCH."""
        page = Page("https://example.lightning.force.com/lightning/page/home")

        def opened(url):
            if url.endswith("/lightning/setup/Flows/home"):
                page.roles.add(("button", "New Flow"))

        def reveal(kind, name):
            if name == "New Flow":
                # Dialog opens but Screen Flow is not offered
                page.body = "New Automation"

        page.on_goto = opened
        page.on_click = reveal
        item = step(
            "sf4", "Exercise 1", "Task: Create Flow", "1",
            "Create a new Screen Flow.",
        )
        outcome = self.walk(page, (item,), timeout_s=10)
        self.assertEqual(outcome.statuses["sf4"], "UI_MISMATCH")
        self.assertIn("Screen Flow was not offered", outcome.notes["sf4"])

    def test_new_flow_button_not_clicked_multiple_times(self):
        """New Flow button is not repeatedly clicked during polling."""
        page = Page("https://example.lightning.force.com/lightning/page/home")

        click_count = {"count": 0}

        def opened(url):
            if url.endswith("/lightning/setup/Flows/home"):
                page.roles.add(("button", "New Flow"))

        def reveal(kind, name):
            if name == "New Flow":
                click_count["count"] += 1
                # Dialog opens immediately but Screen Flow takes time
            if name == "Screen Flow":
                page.roles.add(("button", "Create"))
            if name == "Create":
                page.url = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
                page.body = "Free-Form"
                as_builder(page)

        page.on_goto = opened
        page.on_click = reveal

        clock = Clock()

        def sleep(seconds):
            clock.now += seconds
            # Screen Flow appears after 6 seconds
            if clock.now >= 6:
                page.body = "New Automation Screen Flow"

        item = step(
            "sf5", "Exercise 1", "Task: Create Flow", "1",
            "Create a new Screen Flow.",
        )
        outcome = walk_guide(
            page, inventory_of(item),
            timeout_s=90, interval_s=2, clock=clock,
            sleeper=sleep,
        )
        self.assertEqual(outcome.statuses["sf5"], "PASS")
        # New Flow should only be clicked once
        self.assertEqual(click_count["count"], 1)

    def test_dialog_no_screen_flow_needs_review(self):
        """Dialog opens with different content shows UI_MISMATCH."""
        page = Page("https://example.lightning.force.com/lightning/page/home")

        def opened(url):
            if url.endswith("/lightning/setup/Flows/home"):
                page.roles.add(("button", "New Flow"))

        def reveal(kind, name):
            if name == "New Flow":
                # Dialog shows different options
                page.body = "New Automation\nAutomated Process\nBefore-Save Flow"

        page.on_goto = opened
        page.on_click = reveal
        item = step(
            "sf6", "Exercise 1", "Task: Create Flow", "1",
            "Create a new Screen Flow.",
        )
        outcome = self.walk(page, (item,), timeout_s=10)
        self.assertEqual(outcome.statuses["sf6"], "UI_MISMATCH")

    def test_screen_flow_delayed_across_multiple_polls(self):
        """Screen Flow appearing after multiple poll iterations passes."""
        page = Page("https://example.lightning.force.com/lightning/page/home")

        dialog_opened = {"done": False}

        def opened(url):
            if url.endswith("/lightning/setup/Flows/home"):
                page.roles.add(("button", "New Flow"))

        def reveal(kind, name):
            if name == "New Flow" and not dialog_opened["done"]:
                dialog_opened["done"] = True
                page.body = "New Automation"  # Initially no Screen Flow
            if name == "Screen Flow":
                page.roles.add(("button", "Create"))
            if name == "Create":
                page.url = "https://example.lightning.force.com/builder_platform_interaction/flowBuilder.app"
                page.body = "Free-Form"
                as_builder(page)

        page.on_goto = opened
        page.on_click = reveal

        clock = Clock()
        poll_count = {"count": 0}

        def sleep(seconds):
            clock.now += seconds
            poll_count["count"] += 1
            # Screen Flow appears after 10 seconds (5 polls at 2s interval)
            if clock.now >= 10 and dialog_opened["done"]:
                page.body = "New Automation Screen Flow"

        item = step(
            "sf7", "Exercise 1", "Task: Create Flow", "1",
            "Create a new Screen Flow.",
        )
        outcome = walk_guide(
            page, inventory_of(item),
            timeout_s=90, interval_s=2, clock=clock,
            sleeper=sleep,
        )
        self.assertEqual(outcome.statuses["sf7"], "PASS")
        # Should have waited through multiple polls
        self.assertGreaterEqual(poll_count["count"], 5)


if __name__ == "__main__":
    unittest.main()
