import os
import unittest
from workshop_validator.discovery import name_similarity, propose_locators

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright=None


class SimilarityTests(unittest.TestCase):
    def test_changed_wording_is_only_textual_score(self):
        self.assertGreater(name_similarity('Create Agent', 'New Agent'),0.3)
        self.assertEqual(name_similarity('','New Agent'),0.)
        self.assertLess(name_similarity('Open Settings','Delete Everything'),0.5)


@unittest.skipUnless(sync_playwright is not None and os.environ.get("RUN_BROWSER_SMOKE") == "1",
                     "Requires Chromium synthetic smoke")
class DiscoveryBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page=self.browser.new_page()
        self.page.set_content('''<button>New from Script</button>
           <button>New Agent</button>
           <button style="display:none">Create from Script</button>
           <a href="#">New from Script</a>''')
        self.addCleanup(self.page.close)

    def test_proposes_similar_unique_button_without_clicking(self):
        result=propose_locators(self.page,'button','Create from Script',min_similarity=0.3)
        self.assertTrue(result)
        self.assertEqual(result[0].candidate_name,'New from Script')
        self.assertTrue(all(x.status=='UNVERIFIED_CANDIDATE' for x in result))
        self.assertEqual(self.page.url,'about:blank')
        self.assertNotIn('Create from Script',[x.candidate_name for x in result])

    def test_role_boundary_and_invalid_role(self):
        self.assertFalse(propose_locators(self.page,'option','Create from Script'))
        with self.assertRaises(ValueError):
            propose_locators(self.page,'body','Create from Script')


if __name__=='__main__':
    unittest.main()
