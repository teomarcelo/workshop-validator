"""Read-only fail-closed private Salesforce org baseline gate."""
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlparse

class OrgBaselineError(ValueError):
    """Safe diagnostic: never include observed UI or host text."""

@dataclass(frozen=True)
class BaselineCheck:
    check_id: str
    category: str
    kind: str
    role: str
    accessible_name: str
    expected: object

@dataclass(frozen=True)
class OrgBaseline:
    guide_sha256: str
    org_hostname: str
    checks: tuple[BaselineCheck, ...]

def load_org_baseline(path, *, pdf_digest):
    """Read private, source-pinned, author-reviewed checks."""
    try:
        file = Path(path)
        if file.stat().st_size > 1048576:
            raise OrgBaselineError("Baseline too large")
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise OrgBaselineError("Invalid private baseline") from None
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise OrgBaselineError("Invalid baseline schema")
    if data.get("guide_sha256") != pdf_digest or not isinstance(pdf_digest, str) or len(pdf_digest) != 64:
        raise OrgBaselineError("Baseline source mismatch")
    host = data.get("org_hostname")
    if not isinstance(host, str) or host != host.lower() or not host or not all(ch.isalnum() or ch in ".-" for ch in host) or not (host.endswith(".salesforce.com") or host.endswith(".force.com")):
        raise OrgBaselineError("Invalid approved hostname")
    raw = data.get("checks")
    if not isinstance(raw, list) or not 2 <= len(raw) <= 100:
        raise OrgBaselineError("Invalid baseline checks")
    checks = []
    ids = set()
    for item in raw:
        if not isinstance(item, dict):
            raise OrgBaselineError("Invalid baseline check")
        try:
            check = BaselineCheck(**item)
        except TypeError:
            raise OrgBaselineError("Invalid baseline check") from None
        if (not isinstance(check.check_id, str) or not check.check_id or check.check_id in ids
            or check.category not in ("ORG_IDENTITY", "PREREQUISITE")
            or check.kind not in ("DOM_TEXT", "DOM_VISIBLE")
            or not isinstance(check.role, str) or not check.role.strip()
            or not isinstance(check.accessible_name, str) or not check.accessible_name.strip()
            or (check.kind == "DOM_TEXT" and (not isinstance(check.expected, str) or not check.expected.strip()))
            or (check.kind == "DOM_VISIBLE" and check.expected is not True)
            or (check.category == "ORG_IDENTITY" and check.kind != "DOM_TEXT")):
            raise OrgBaselineError("Invalid baseline check")
        ids.add(check.check_id)
        checks.append(check)
    if {check.category for check in checks} != {"ORG_IDENTITY", "PREREQUISITE"}:
        raise OrgBaselineError("Identity and prerequisite checks required")
    return OrgBaseline(pdf_digest, host, tuple(checks))

def require_org_baseline(page, baseline):
    """Never mutate org; fail before any workshop action."""
    if (urlparse(str(page.url)).hostname or "").lower() != baseline.org_hostname:
        raise OrgBaselineError("Authenticated org mismatch")
    for check in baseline.checks:
        try:
            locator = page.get_by_role(check.role, name=check.accessible_name, exact=True)
            if locator.count() != 1 or not locator.is_visible():
                raise OrgBaselineError("Baseline assertion failed")
            if check.kind == "DOM_TEXT" and locator.inner_text() != check.expected:
                raise OrgBaselineError("Baseline assertion failed")
        except OrgBaselineError:
            raise
        except Exception:
            raise OrgBaselineError("Baseline observation unavailable") from None
