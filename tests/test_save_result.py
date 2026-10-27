"""Test reading save result messages on Personal Information page."""
import unittest

from workshop_validator.guide_walk import _read_save_message


class MockPage:
    """Mock page for testing save result reading."""
    def __init__(self, body="", status_elements=None, alert_elements=None):
        self.body = body
        self.frames = [self]
        self._status_elements = status_elements or []
        self._alert_elements = alert_elements or []

    def content(self):
        return self.body

    def locator(self, selector):
        return MockLocator(self, selector)


class MockLocator:
    """Mock locator for testing."""
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    def all(self):
        if "[role='status']" in self.selector:
            return self.page._status_elements
        elif "[role='alert']" in self.selector:
            return self.page._alert_elements
        return []

    def count(self):
        if self.selector == "body":
            return 1 if self.page.body else 0
        return len(self.all())

    @property
    def first(self):
        if self.selector == "body":
            return MockBodyElement(self.page.body)
        elements = self.all()
        return elements[0] if elements else None


class MockElement:
    """Mock element with role and text."""
    def __init__(self, text, visible=True):
        self._text = text
        self._visible = visible

    def is_visible(self):
        return self._visible

    def inner_text(self):
        return self._text


class MockBodyElement:
    """Mock body element for text content."""
    def __init__(self, text):
        self._text = text

    def inner_text(self, timeout=None):
        return self._text


class TestReadSaveMessage(unittest.TestCase):
    def test_success_banner_with_status_role(self):
        """Success banner with role=status and 'saved' text returns success type."""
        elem = MockElement("Your settings have been successfully saved.")
        page = MockPage(status_elements=[elem])
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "success")
        self.assertIn("saved", msg_text.lower())

    def test_success_banner_with_alert_role(self):
        """Success banner with role=alert and 'success' text returns success."""
        elem = MockElement("Settings saved successfully")
        page = MockPage(alert_elements=[elem])
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "success")
        self.assertIn("saved", msg_text.lower())

    def test_error_message_with_status_role(self):
        """Error message with role=status returns error type."""
        elem = MockElement("Error: Unable to save settings")
        page = MockPage(status_elements=[elem])
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "error")
        self.assertIn("error", msg_text.lower())

    def test_error_message_with_alert_role(self):
        """Error message with role=alert returns error type."""
        elem = MockElement("Failed to save your settings")
        page = MockPage(alert_elements=[elem])
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "error")
        self.assertIn("failed", msg_text.lower())

    def test_no_message_returns_none(self):
        """No message found returns none type."""
        page = MockPage()
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "none")
        self.assertEqual(msg_text, "")

    def test_successfully_saved_in_body_returns_success(self):
        """Body text with 'successfully saved' returns success."""
        page = MockPage(body="Your changes were successfully saved. Thank you.")
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "success")

    def test_generic_saved_in_body_returns_success(self):
        """Body text with just 'saved' returns success."""
        page = MockPage(body="Settings saved")
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "success")

    def test_error_in_body_returns_error(self):
        """Body text with 'error' returns error type."""
        page = MockPage(body="An error occurred while saving")
        msg_type, msg_text = _read_save_message(page)
        self.assertEqual(msg_type, "error")

    def test_invisible_element_not_counted(self):
        """Invisible message is not counted, falls back to body."""
        elem = MockElement("Settings saved successfully", visible=False)
        page = MockPage(status_elements=[elem])
        msg_type, msg_text = _read_save_message(page)
        # Should not find the invisible message, no body text either
        self.assertEqual(msg_type, "none")


if __name__ == "__main__":
    unittest.main()
