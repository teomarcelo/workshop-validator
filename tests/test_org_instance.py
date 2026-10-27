"""Org instance lookup, with fake pages only (no network, no org)."""

import unittest

from workshop_validator import org_instance


class _Response:
    def __init__(self, ok, payload, status=200):
        self.ok, self._payload, self.status = ok, payload, status

    def json(self):
        return self._payload


class _Request:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class _Context:
    def __init__(self, cookies, response):
        self._cookies = cookies
        self.request = _Request(response)

    def cookies(self):
        return self._cookies


class _Page:
    def __init__(self, context, url="https://demo.lightning.force.example/lightning/page/home"):
        self.context, self.url = context, url


_SID = [{"name": "sid", "domain": "demo.my.salesforce.com", "value": "token-1"},
        {"name": "sid", "domain": "demo.lightning.force.com", "value": "ui-only"}]


class SessionQueryTests(unittest.TestCase):
    def test_reads_instance_name_with_the_api_session_cookie(self):
        response = _Response(True, {"records": [
            {"InstanceName": "usa123s", "IsSandbox": False, "OrganizationType": "Developer Edition"}]})
        page = _Page(_Context(_SID, response))
        found = org_instance.from_session(page)
        self.assertEqual(found, {"instance": "USA123S", "sandbox": False,
                                 "edition": "Developer Edition", "source": "api"})
        url, kwargs = page.context.request.calls[0]
        self.assertTrue(url.startswith("https://demo.my.salesforce.com/services/data/"))
        self.assertIn("InstanceName", kwargs["params"]["q"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer token-1")
        self.assertEqual(kwargs["max_redirects"], 0)

    def test_refused_query_missing_cookie_or_odd_value_is_none(self):
        refused = _Page(_Context(_SID, _Response(False, {})))
        self.assertIsNone(org_instance.from_session(refused))
        no_cookie = _Page(_Context([_SID[1]], _Response(True, {"records": [{"InstanceName": "NA1"}]})))
        self.assertIsNone(org_instance.from_session(no_cookie))
        odd = _Page(_Context(_SID, _Response(True, {"records": [{"InstanceName": "<b>x</b>"}]})))
        self.assertIsNone(org_instance.from_session(odd))
        self.assertIsNone(org_instance.from_session(object()))

    def test_redirect_response_is_treated_as_not_found(self):
        # 3xx response with max_redirects=0 is treated as not found
        redirect = _Page(_Context(_SID, _Response(True, {"records": [{"InstanceName": "NA1"}]}, status=302)))
        self.assertIsNone(org_instance.from_session(redirect))

    def test_company_information_text(self):
        self.assertEqual(org_instance.instance_from_text("Organization Edition\tDeveloper\nInstance\tAUS58\n"), "AUS58")
        self.assertIsNone(org_instance.instance_from_text("No instance row here"))


class TrustTests(unittest.TestCase):
    def test_release_and_region_from_trust_api(self):
        seen = []

        def fetch(url, timeout):
            seen.append(url)
            return {"key": "AP52", "releaseVersion": "Winter '27", "location": "APAC", "environment": "production"}

        details = org_instance.trust_details("AP52", fetch=fetch)
        self.assertEqual(details["release"], "Winter '27")
        self.assertEqual(details["location"], "APAC")
        self.assertTrue(seen[0].endswith("/instances/AP52/status"))

    def test_trust_failure_or_bad_key_is_empty(self):
        def boom(url, timeout):
            raise OSError("offline")

        self.assertEqual(org_instance.trust_details("AP52", fetch=boom), {})
        self.assertEqual(org_instance.trust_details("../x", fetch=boom), {})

    def test_detect_without_any_source_is_unknown(self):
        self.assertEqual(org_instance.detect(object(), fetch=lambda u, t: {}), {"instance": "unknown"})

    def test_label(self):
        self.assertEqual(org_instance.label({"instance": "AP52", "release": "Winter '27", "location": "APAC"}),
                         "AP52 (Winter '27, APAC)")
        self.assertEqual(org_instance.label(None), "Instance unknown")


if __name__ == "__main__":
    unittest.main()
