"""Find the Salesforce instance (for example USA580S) an org runs on.

Salesforce exposes it as the read-only ``Organization.InstanceName`` field
(API 31.0 and later). The walk's own signed-in browser session asks for it
with one REST query, the same call a Developer Console query makes. The
page is not touched, so the guide walk stays in printed order. When the
query is refused, Setup > Company Information is read in a separate tab
after the walk; its "Instance" row shows the same value. Nothing is
clicked. The session id is used only in the request header and is never
stored or printed.

The public Salesforce Trust API then adds the release and region for that
instance. It is free and needs no sign-in.
"""

from urllib.parse import urlsplit
import json
import re
import urllib.request

API_VERSION = "v60.0"
SOQL = "SELECT InstanceName, IsSandbox, OrganizationType FROM Organization LIMIT 1"
TRUST_URL = "https://api.status.salesforce.com/v1/instances/{key}/status"
COMPANY_INFO_PATH = "/lightning/setup/CompanyProfileInfo/home"
UNKNOWN = "unknown"

_KEY = re.compile(r"^[A-Z]{2,6}\d{1,4}[A-Z]?$")
_INSTANCE_ROW = re.compile(r"\bInstance\s*[:\n\t ]\s*([A-Z]{2,6}\d{1,4}[A-Z]?)\b")


def valid_key(value: object) -> str | None:
    text = str(value or "").strip().upper()
    return text if _KEY.match(text) else None


def _api_host(cookies) -> tuple[str, str] | None:
    """The org's my.salesforce.com host and its API session cookie."""
    for cookie in cookies or ():
        domain = str(cookie.get("domain") or "").lstrip(".")
        if cookie.get("name") == "sid" and domain.endswith(".my.salesforce.com") and cookie.get("value"):
            return domain, str(cookie["value"])
    return None


def from_session(page, *, timeout_ms: int = 15000) -> dict | None:
    """Query Organization.InstanceName with the browser's own session."""
    try:
        context = page.context
        found = _api_host(context.cookies())
        if found is None:
            return None
        host, sid = found
        response = context.request.get(
            f"https://{host}/services/data/{API_VERSION}/query",
            params={"q": SOQL},
            headers={"Authorization": f"Bearer {sid}", "Accept": "application/json"},
            timeout=timeout_ms,
            max_redirects=0,
        )
        if not response.ok or 300 <= response.status < 400:
            return None
        records = response.json().get("records") or []
    except Exception:
        return None
    if not records:
        return None
    record = records[0]
    key = valid_key(record.get("InstanceName"))
    if key is None:
        return None
    return {
        "instance": key,
        "sandbox": bool(record.get("IsSandbox")),
        "edition": str(record.get("OrganizationType") or ""),
        "source": "api",
    }


def instance_from_text(text: str) -> str | None:
    match = _INSTANCE_ROW.search(text or "")
    return valid_key(match.group(1)) if match else None


def from_company_info(page, *, wait_s: float = 30) -> dict | None:
    """Read the Instance row of Setup > Company Information in a new tab."""
    try:
        parts = urlsplit(page.url)
        if not parts.hostname:
            return None
        tab = page.context.new_page()
    except Exception:
        return None
    try:
        tab.goto(f"https://{parts.hostname}{COMPANY_INFO_PATH}", wait_until="domcontentloaded",
                 timeout=int(wait_s * 1000))
        for _ in range(max(1, int(wait_s / 2))):
            for frame in tab.frames:
                try:
                    key = instance_from_text(frame.inner_text("body", timeout=2000))
                except Exception:
                    key = None
                if key:
                    return {"instance": key, "sandbox": None, "edition": "", "source": "company-info"}
            tab.wait_for_timeout(2000)
    except Exception:
        return None
    finally:
        try:
            tab.close()
        except Exception:
            pass
    return None


def _fetch_json(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https host
        return json.loads(response.read().decode("utf-8"))


def trust_details(key: str, *, fetch=_fetch_json, timeout: float = 5) -> dict:
    """Release and region for an instance from the public Trust API, or {}."""
    key = valid_key(key)
    if key is None:
        return {}
    try:
        data = fetch(TRUST_URL.format(key=key), timeout)
    except Exception:
        return {}
    if not isinstance(data, dict) or data.get("key") != key:
        return {}
    return {
        "release": str(data.get("releaseVersion") or ""),
        "location": str(data.get("location") or ""),
        "environment": str(data.get("environment") or ""),
    }


def detect(page, *, fallback: bool = True, fetch=_fetch_json) -> dict:
    """Instance details for the signed-in org; instance is UNKNOWN when not found."""
    found = from_session(page)
    if found is None and fallback:
        found = from_company_info(page)
    if found is None:
        return {"instance": UNKNOWN}
    return {**found, **trust_details(found["instance"], fetch=fetch)}


def label(info: dict | None) -> str:
    """Short text for reports: 'USA580S (Winter '27, NA)'."""
    key = (info or {}).get("instance") or UNKNOWN
    if key == UNKNOWN:
        return "Instance unknown"
    extra = ", ".join(x for x in ((info or {}).get("release"), (info or {}).get("location")) if x)
    return f"{key} ({extra})" if extra else key
