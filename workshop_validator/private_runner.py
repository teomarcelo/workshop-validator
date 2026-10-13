"""Private-only AGT503 validation entry point.

This module is public *code*, not a public workflow. All private guide content,
credentials, traces, screenshots, manifests, reports and SQLite remain outside
the public source repository and public Actions artifacts.
"""

from collections import Counter
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse
from uuid import uuid4

from .browser_worker import UIAction, UIAssertion, BrowserActionError, perform_actions, collect_observations
from .evidence import AssertionSpec
from .guide_inventory import extract_inventory
from .reconciliation import (CandidateReview, AddedAction, PageReview,
                              SourceReview, file_sha256, reconcile_inventory)
from .pdf_annotations import ReviewNote, annotate_pdf
from .pipeline import PlannedStep, RateLimitDetected, validate_plan
from .retries import RetryPolicy
from .report import write_private_report


class PrivateRunnerError(ValueError):
    """Safe high-level diagnostics; no credential/page contents."""


def _json(path: str | Path):
    with Path(path).open("r", encoding="utf-8") as source:
        return json.load(source)


def load_plan(path: str | Path, *, pdf_digest: str, page_count: int) -> tuple[PlannedStep, ...]:
    """Parse an AUTHOR-REVIEWED executable plan; no LLM code execution."""
    data = _json(path)
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise PrivateRunnerError("Unsupported private plan schema")
    if data.get("guide_sha256") != pdf_digest:
        raise PrivateRunnerError("Private plan does not match exact guide PDF bytes")
    raw = data.get("steps")
    if not isinstance(raw, list) or not raw or len(raw) > 5000:
        raise PrivateRunnerError("Missing or oversized step plan")
    plans = []
    for item in raw:
        if not isinstance(item, dict):
            raise PrivateRunnerError("Invalid plan entry")
        step_id = item.get("step_id")
        page = item.get("page")
        title = item.get("title")
        if (not isinstance(step_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", step_id)
                or type(page) is not int or not 1 <= page <= page_count
                or not isinstance(title, str) or not title.strip()):
            raise PrivateRunnerError("Invalid private plan source anchor")
        actions = []
        for a in item.get("actions", []):
            if (not isinstance(a, dict) or a.get("kind") not in {"click", "fill"} or
                    not isinstance(a.get("role"), str) or not isinstance(a.get("accessible_name"), str)):
                raise PrivateRunnerError("Invalid or unsupported UI action")
            actions.append(UIAction(a["kind"], a["role"], a["accessible_name"], a.get("value")))
        assertions = []
        for a in item.get("assertions", []):
            try:
                spec = AssertionSpec(a["assertion_id"], a["kind"], a["expected"], a.get("operator", "eq"))
                assertion = UIAssertion(spec, a["role"], a["accessible_name"])
            except (TypeError, KeyError):
                raise PrivateRunnerError("Malformed expected assertion") from None
            assertions.append(assertion)
        dependencies = item.get("dependencies", [])
        if (not isinstance(dependencies, list) or
                not all(isinstance(s, str) for s in dependencies)):
            raise PrivateRunnerError("Invalid dependency IDs")
        plans.append(PlannedStep(step_id, page, title, tuple(actions),
                                 tuple(assertions), tuple(dependencies),
                                 item.get("required", True) is True))
    return tuple(plans)


def load_source_review(path: str | Path) -> SourceReview:
    """Typed private human review; exact page/candidate coverage checked elsewhere."""
    raw = _json(path)
    if not isinstance(raw, dict):
        raise PrivateRunnerError("Invalid source review")
    try:
        pages = tuple(PageReview(
            page=p["page"], text_checked=p["text_checked"],
            visuals_checked=p["visuals_checked"], no_other_actions=p["no_other_actions"],
            candidates=tuple(CandidateReview(
                step_id=c["step_id"], disposition=c["disposition"],
                reviewed_warnings=tuple(c.get("reviewed_warnings", ())),
                rationale=c.get("rationale", "")
            ) for c in p.get("candidates", ())),
            added_actions=tuple(AddedAction(**a) for a in p.get("added_actions", ())),
            unresolved=tuple(p.get("unresolved", ()))
        ) for p in raw["pages"])
        return SourceReview(
            pdf_sha256=raw["pdf_sha256"],
            inventory_sha256=raw["inventory_sha256"],
            reviewer_id=raw["reviewer_id"],
            reviewed_at=raw["reviewed_at"],
            pages=pages, reviewed_inventory_warnings=tuple(raw.get("reviewed_inventory_warnings", ()))
        )
    except (KeyError, TypeError, ValueError):
        raise PrivateRunnerError("Malformed source review attestation") from None


def login_to_salesforce(page, username: str, password: str) -> None:
    """Standard login form only. No SSO/MFA bypass; fail closed on other flow."""
    if not username or not password:
        raise PrivateRunnerError("Missing approved secret environment variables")
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    try:
        page.goto("https://login.salesforce.com/", wait_until="domcontentloaded", timeout=30000)
        page.locator("#username").fill(username, timeout=15000)
        page.locator("#password").fill(password, timeout=15000)
        page.locator("#Login").click(timeout=15000)
        page.wait_for_url(lambda url: (
            urlparse(str(url)).hostname not in {"login.salesforce.com", "test.salesforce.com"}
        ), timeout=45000)
    except PlaywrightTimeout:
        raise PrivateRunnerError("Salesforce login timed out or requires interactive authentication") from None
    except Exception:
        raise PrivateRunnerError("Salesforce authentication failed; no steps verified") from None
    host = (urlparse(page.url).hostname or "").lower()
    if not any(host == domain or host.endswith("." + domain)
               for domain in ("salesforce.com", "force.com")):
        raise PrivateRunnerError("Unexpected login redirect; no steps verified")


def _protected_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def run_private(guide_path: str | Path, plan_path: str | Path,
                output_dir: str | Path, *,
                source_review_path: str | Path | None = None,
                browser_factory=None) -> dict:
    """Perform a single bounded run; deliberately requires private filesystem.

    Unexpected exceptions abort the run rather than manufacture a green report.
    On failure, any partial DB/screenshots remain privately available for review.
    """
    guide = Path(guide_path)
    out = Path(output_dir)
    if out.exists() and any(out.iterdir()):
        raise PrivateRunnerError("Output directory must be new or empty")
    digest = file_sha256(guide)
    inventory = extract_inventory(guide)
    plans = load_plan(plan_path, pdf_digest=digest, page_count=inventory.page_count)
    reviewed_ids = None
    reconciled = False
    if source_review_path:
        review = load_source_review(source_review_path)
        audit = reconcile_inventory(inventory, review, digest)
        if audit.complete:
            reconciled, reviewed_ids = True, audit.action_ids
        # Never elevate an incomplete review to full source coverage.
    if reconciled and set(reviewed_ids) != {step.step_id for step in plans}:
        raise PrivateRunnerError("Executable plan does not cover reconciled source actions")
    user = os.environ.get("SF_USERNAME", "")
    password = os.environ.get("SF_PASSWORD", "")
    if not user or not password:
        raise PrivateRunnerError("Private runner secrets are not configured")
    _protected_dir(out)
    evidence = out / "screenshots"
    _protected_dir(evidence)
    run_id = "run-" + uuid4().hex[:14]
    org_id = "disposable-org"
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(accept_downloads=False)
            login_to_salesforce(page, user, password)

            def collect_privately(page, step, context):
                responses = []
                def on_response(response):
                    if response.status == 429:
                        host = urlparse(response.url).hostname or ""
                        if host.endswith(".salesforce.com") or host.endswith(".force.com"):
                            responses.append(response)
                page.on("response", on_response)
                try:
                    perform_actions(page, step.actions)
                    observed = collect_observations(page, context, step.assertions)
                    screenshot = evidence / (step.step_id.replace(":", "_") + ".png")
                    page.screenshot(path=str(screenshot), full_page=False, animations="disabled")
                    screenshot.chmod(0o600)
                    if responses:
                        raise RateLimitDetected("Observed Salesforce HTTP 429",
                            retry_after=responses[0].headers.get("retry-after"))
                    return observed
                except RateLimitDetected:
                    raise
                except BrowserActionError:
                    raise
                except Exception:
                    raise BrowserActionError("Browser operation/evidence capture failed") from None
                finally:
                    page.remove_listener("response", on_response)

            outcome = validate_plan(
                page, out / "checkpoint.sqlite", run_id, org_id, plans,
                source_action_ids=reviewed_ids, source_reconciled=reconciled,
                execute_step=collect_privately,
                retry_policy=RetryPolicy(max_attempts=3,base_seconds=60,
                                         max_backoff_seconds=3600),
                max_wait_seconds=600,
            )
        finally:
            browser.close()

    html_path = out / "validation-summary.html"
    write_private_report(html_path, outcome.html)
    flagged = [
        ReviewNote(
            step.page, step.step_id, outcome.statuses[step.step_id],
            step.title,
            "Step is blocked, unverified, or did not meet required assertions.",
            "Investigate the live UI and independently verify any proposed change.",
        )
        for step in plans if outcome.statuses[step.step_id] != "PASS"
    ]
    annotated = None
    if flagged:
        annotated = out / "annotated-instructor-review.pdf"
        annotate_pdf(guide, flagged, annotated)
    summary = {
        "run_id": run_id, "source_reconciled": reconciled,
        "fully_verified": outcome.fully_verified,
        "candidate_inventory_steps": len(inventory.steps),
        "planned_steps": len(plans),
        "status_counts": dict(Counter(outcome.statuses.values())),
        "report": str(html_path.name),
        "annotated_review": annotated.name if annotated else None,
    }
    with (out / "status.json").open("x", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    (out / "status.json").chmod(0o600)
    return summary


def main():
    parser = argparse.ArgumentParser(description="PRIVATE single-org workshop validation pilot")
    parser.add_argument("--guide", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--review", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        summary = run_private(args.guide, args.plan, args.output,
                              source_review_path=args.review)
        # No credentials, guide instructions or raw evidence in stdout.
        print(json.dumps(summary, indent=2))
        if not summary["fully_verified"]:
            sys.exit(2)  # incomplete validation, never a green claim
    except (PrivateRunnerError, ValueError, FileNotFoundError) as exc:
        print("Validation not completed: " + str(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
