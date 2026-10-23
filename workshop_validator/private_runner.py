"""Private-only AGT503 validation entry point.

This module is public *code*, not a public workflow. All private guide content,
credentials, traces, screenshots, manifests, reports and SQLite remain outside
the public source repository and public Actions artifacts.
"""

from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import argparse
import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from urllib.parse import urlparse
from uuid import uuid4

from .browser_worker import UIAction, UIAssertion, BrowserActionError
from .behavioral_probe import ActionProbe
from .behavioral_probe import execute_with_necessity_probe
from .discovery import propose_locators, proposals_to_private_dict
from .evidence import AssertionSpec
from .guide_inventory import GuideInventory, extract_inventory
from .guide_walk import walk_guide
from . import org_instance
from .reconciliation import (CandidateReview, AddedAction, PageReview,
                              SourceReview, file_sha256)
from .private_preflight import SourcePreflightError, require_executable_source_review
from .pdf_annotations import ReviewNote
from .pdf_inline import InlineNote, annotate_pdf_inline
from .pipeline import PlannedStep, RateLimitDetected, validate_plan
from .retries import RetryPolicy
from .report import ReportStep, render_report, write_private_report


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
        note_anchor = item.get("inline_after_y")
        if note_anchor is not None and (
            type(note_anchor) not in (int, float) or
            not math.isfinite(note_anchor) or note_anchor <= 36
        ):
            raise PrivateRunnerError("Invalid reviewed inline PDF anchor")
        actions = []
        for a in item.get("actions", []):
            if (not isinstance(a, dict) or a.get("kind") not in {"click", "fill"} or
                    not isinstance(a.get("role"), str) or not isinstance(a.get("accessible_name"), str)):
                raise PrivateRunnerError("Invalid or unsupported UI action")
            actions.append(UIAction(a["kind"], a["role"], a["accessible_name"], a.get("value")))
        def parse_assertions(key):
            raw_assertions = item.get(key, [])
            if not isinstance(raw_assertions, list):
                raise PrivateRunnerError("Invalid assertion list")
            parsed = []
            for a in raw_assertions:
                try:
                    spec = AssertionSpec(a["assertion_id"], a["kind"], a["expected"], a.get("operator", "eq"))
                    parsed.append(UIAssertion(spec, a["role"], a["accessible_name"]))
                except (TypeError, KeyError):
                    raise PrivateRunnerError("Malformed expected assertion") from None
            return tuple(parsed)
        assertions = parse_assertions("assertions")
        necessity_assertions = parse_assertions("necessity_assertions")
        all_assertions = assertions + necessity_assertions
        assertion_ids = [a.spec.assertion_id for a in all_assertions]
        if len(assertion_ids) != len(set(assertion_ids)):
            raise PrivateRunnerError("Duplicate postcondition assertion IDs")
        dependencies = item.get("dependencies", [])
        if (not isinstance(dependencies, list) or
                not all(isinstance(s, str) for s in dependencies)):
            raise PrivateRunnerError("Invalid dependency IDs")
        plans.append(PlannedStep(step_id, page, title, tuple(actions),
                                 all_assertions, tuple(dependencies),
                                 item.get("required", True) is True,
                                 necessity_assertions))
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


def _promo_failure(exc: BaseException) -> bool:
    return "promos.html" in str(exc).lower()


def _ignore_promo_iframe(page) -> None:
    """Drop the cosmetic login promo. A block on that frame is not a failed sign-in."""
    route = getattr(page, "route", None)
    if not callable(route):
        return

    def handler(intercepted):
        abort = getattr(intercepted, "abort", None)
        if callable(abort):
            try:
                abort()
                return
            except Exception:
                pass
        resume = getattr(intercepted, "continue_", None)
        if not callable(resume):
            resume = getattr(intercepted, "fallback", None)
        if callable(resume):
            try:
                resume()
            except Exception:
                pass

    try:
        route(re.compile(r"promos\.html", re.I), handler)
    except Exception:
        return


_LOGIN_HOSTS = {
    "login.salesforce.com",
    "test.salesforce.com",
    "salesforce.com",
    "www.salesforce.com",
}


def _hostname(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text.lstrip(".")
    return (urlparse(text).hostname or "").lower()


def _usable_org_host(host: str) -> bool:
    if not host or host in _LOGIN_HOSTS:
        return False
    return host.endswith(".salesforce.com") or host.endswith(".force.com")


def _visible_login_error(page) -> str:
    for selector in ("#error", ".loginError"):
        try:
            loc = page.locator(selector)
            if loc.count() == 0 or not loc.first.is_visible():
                continue
            text = re.sub(r"\s+", " ", loc.first.inner_text(timeout=1000) or "").strip()
        except Exception:
            continue
        if text:
            return text[:180]
    return ""


def _login_form_visible(page) -> bool:
    """True on the org's own login wall. That host is not an open org."""
    try:
        box = page.locator("#username")
        if box.count() > 0 and box.is_visible():
            return True
    except Exception:
        pass
    try:
        text = (page.locator("body").inner_text(timeout=1500) or "").lower()
    except Exception:
        text = ""
    return "you have to log in" in text


def _on_lightning(url: str) -> bool:
    return "/lightning/" in (url or "")


def _org_open(page) -> bool:
    url = str(getattr(page, "url", "") or "")
    if not _on_lightning(url) or not _usable_org_host(_hostname(url)):
        return False
    return not _login_form_visible(page)


def login_to_salesforce(page, username: str, password: str) -> None:
    """Standard login form only. No SSO/MFA bypass; fail closed on other flow.

    login.salesforce.com currently asks for the username first, then the
    password on the next screen. Older combined forms still work: if the
    password field is already visible, it is filled without an extra click.
    The promos.html iframe is cosmetic. A block on that frame is not a
    failed login. Sign-in fails only when the form never submits or
    Lightning never opens. The password Login control is clicked once.
    """
    if not username or not password:
        raise PrivateRunnerError("Missing approved secret environment variables")
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    _ignore_promo_iframe(page)
    submitted = False
    try:
        try:
            page.goto(
                "https://login.salesforce.com/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
        except Exception as exc:
            if not _promo_failure(exc):
                raise
        page.locator("#username").fill(username, timeout=15000)
        password_box = page.locator("#password")
        if password_box.count() == 0 or not password_box.is_visible():
            page.locator("#Login").click(timeout=15000)
            password_box.wait_for(state="visible", timeout=20000)
        password_box.fill(password, timeout=15000)
        submitted = True
        page.locator("#Login").click(timeout=15000)
        try:
            page.wait_for_url(
                lambda url: (
                    _on_lightning(str(url))
                    and _usable_org_host(_hostname(str(url)))
                    and not _login_form_visible(page)
                ),
                timeout=45000,
            )
        except Exception as exc:
            if not (_promo_failure(exc) and _org_open(page)):
                raise
    except PlaywrightTimeout:
        detail = _visible_login_error(page)
        message = "Salesforce login timed out or requires interactive authentication"
        if detail:
            message = f"{message}. {detail}"
        raise PrivateRunnerError(message) from None
    except PrivateRunnerError:
        raise
    except Exception as exc:
        if submitted and _promo_failure(exc) and _org_open(page):
            return
        raise PrivateRunnerError(
            "Salesforce authentication failed; no steps verified"
        ) from None
    if not _org_open(page):
        raise PrivateRunnerError("Unexpected login redirect; no steps verified")


# Local-site bound. Login's own Playwright waits already stop a normal attempt.
# This outer join catches a call that never returns.
LOGIN_BUDGET_SECONDS = 180
# The results page serves only this file from the private run directory.
RECORDING_NAME = "walk.webm"
_VIEWPORT = {"width": 1500, "height": 1000}
_CHROME_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/156.0.8078.36 Safari/537.36"
)
_STORAGE_NAME = "browser-state.json"
# A spinner, "Loading", or "Nothing here yet" is not a guide failure.
LOAD_WAIT_SECONDS = 90


class PageStillLoading(Exception):
    """A browser check was still loading when the wait ended."""


@dataclass(frozen=True)
class UploadedRun:
    attempted: bool
    signed_in: bool
    message: str
    report_steps: tuple[ReportStep, ...]
    checks_executed: int
    org_instance: dict | None = None


def executable_plans(inventory: GuideInventory) -> tuple[PlannedStep, ...]:
    """Pipeline plans shipped for an uploaded guide.

    The local site does not use this list. It opens Playwright, signs in, and
    runs the guide checks in printed order. Steps with no check stay NOT RUN.
    This never invents a pass.
    """
    del inventory
    return ()


def _context_options(folder: Path | None, storage: Path | None = None) -> dict:
    options = {
        "accept_downloads": False,
        "viewport": dict(_VIEWPORT),
        "user_agent": _CHROME_USER_AGENT,
    }
    if folder is not None:
        options["record_video_dir"] = str(folder)
        options["record_video_size"] = dict(_VIEWPORT)
    if storage is not None and storage.is_file() and not storage.is_symlink():
        options["storage_state"] = str(storage)
    return options


def _accept_video_dir(video_dir, repo_root) -> Path | None:
    """Return a private directory for the session video, outside the repository."""
    if video_dir is None:
        return None
    path = Path(video_dir).expanduser()
    repo = None if repo_root is None else Path(repo_root)
    if repo is not None and _path_inside(path, repo):
        return None
    existing = path if path.exists() else path.parent
    while not existing.exists():
        if existing.parent == existing:
            break
        existing = existing.parent
    try:
        if existing.is_symlink():
            return None
        if repo is not None and _path_inside(existing, repo):
            return None
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            return None
        path.chmod(0o700)
        resolved = path.resolve()
        if repo is not None and _path_inside(resolved, repo):
            return None
    except OSError:
        return None
    return resolved


def _video_of(page):
    if page is None:
        return None
    try:
        video = page.video
    except Exception:
        return None
    if callable(video):
        try:
            video = video()
        except Exception:
            return None
    return video


def _recorded_source(video, directory: Path) -> Path | None:
    path_of = getattr(video, "path", None)
    if not callable(path_of):
        return None
    try:
        raw = Path(path_of())
    except Exception:
        return None
    if raw.is_symlink() or not raw.is_file():
        return None
    resolved = raw.resolve()
    if resolved.parent != directory:
        return None
    return resolved


def _publish_video(video, directory: Path) -> None:
    directory = Path(directory).resolve()
    if directory.is_symlink() or not directory.is_dir():
        return
    directory.chmod(0o700)
    destination = directory / RECORDING_NAME
    if destination.is_symlink():
        return
    source = _recorded_source(video, directory)
    if source is not None and source != destination:
        os.replace(source, destination)
    elif source is None:
        save_as = getattr(video, "save_as", None)
        if not callable(save_as):
            _promote_recorded_files(directory)
            return
        save_as(str(destination))
    if destination.is_file() and not destination.is_symlink():
        destination.chmod(0o600)
    for extra in directory.glob("*.webm"):
        if extra.is_file() and not extra.is_symlink():
            extra.chmod(0o600)


def _promote_recorded_files(directory: Path) -> None:
    directory = Path(directory).resolve()
    if directory.is_symlink() or not directory.is_dir():
        return
    webms = [
        path for path in directory.glob("*.webm")
        if path.is_file() and not path.is_symlink()
    ]
    if not webms:
        return
    destination = directory / RECORDING_NAME
    if destination.is_symlink():
        return
    if not destination.is_file():
        source = max(webms, key=lambda item: item.stat().st_size)
        if source.resolve() != destination:
            os.replace(source, destination)
    if destination.is_file() and not destination.is_symlink():
        destination.chmod(0o600)
    directory.chmod(0o700)


def _finalize_recording(context, page, folder: Path | None) -> None:
    """Keep the video handle, close the context, then publish the file."""
    video = _video_of(page)
    if context is not None:
        try:
            context.close()
        except Exception:
            pass
    if folder is None:
        return
    try:
        if video is not None:
            _publish_video(video, folder)
        destination = Path(folder) / RECORDING_NAME
        if not destination.is_file() or destination.is_symlink():
            _promote_recorded_files(Path(folder))
        elif destination.is_file():
            destination.chmod(0o600)
            Path(folder).chmod(0o700)
    except Exception:
        pass


def _accept_ok_dialogs(page) -> None:
    """Salesforce confirms the email change with a popup whose button is OK."""
    page._accepted_dialog = ""

    def accept(dialog):
        try:
            page._accepted_dialog = (dialog.message or "OK").strip()
        except Exception:
            page._accepted_dialog = "OK"
        try:
            dialog.accept()
        except Exception:
            pass

    try:
        page.on("dialog", accept)
    except Exception:
        return


def _launch_chrome(playwright):
    """Headed browser with a normal desktop user agent.

    Installed Google Chrome on this Mac closes the tab during Lightning
    because remote debugging is disallowed. Playwright's Chrome build stays
    open, so the walk uses that.
    """
    return playwright.chromium.launch(headless=False)


def _session_storage_path(run_dir: Path | None, repo_root) -> Path | None:
    """Session file next to the run folders, outside the git repository."""
    if run_dir is None:
        return None
    parent = Path(run_dir).parent
    if parent.is_symlink():
        return None
    repo = None if repo_root is None else Path(repo_root)
    if repo is not None and _path_inside(parent, repo):
        return None
    try:
        resolved = parent.resolve()
    except OSError:
        return None
    if resolved.is_symlink() or (repo is not None and _path_inside(resolved, repo)):
        return None
    path = resolved / _STORAGE_NAME
    if path.is_symlink():
        return None
    return path


def _home_from_storage(path: Path | None) -> str | None:
    if path is None or path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    values: list[str] = []
    origins = data.get("origins")
    if isinstance(origins, list):
        for item in origins:
            if isinstance(item, dict):
                values.append(str(item.get("origin") or ""))
    cookies = data.get("cookies")
    if isinstance(cookies, list):
        for item in cookies:
            if isinstance(item, dict):
                values.append(str(item.get("domain") or ""))
    lightning: list[str] = []
    other: list[str] = []
    for value in values:
        host = _hostname(value)
        if not _usable_org_host(host):
            continue
        if host.endswith(".lightning.force.com"):
            lightning.append(host)
        else:
            other.append(host)
    chosen = next(iter(lightning), "") or next(iter(other), "")
    if not chosen:
        return None
    return f"https://{chosen}/lightning/page/home"


def _resume_logged_in(page, storage: Path | None) -> bool:
    """Open Lightning home when the saved session is still logged in."""
    home = _home_from_storage(storage)
    if not home:
        return False
    try:
        page.goto(home, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        if not _promo_failure(exc):
            return False
    try:
        page.wait_for_timeout(2000)
    except Exception:
        pass
    return _org_open(page)


def _save_browser_state(context, page, storage: Path | None, password: str) -> None:
    if context is None or page is None or storage is None:
        return
    if storage.is_symlink() or not _org_open(page):
        return
    parent = storage.parent
    if parent.is_symlink() or not parent.is_dir():
        return
    save = getattr(context, "storage_state", None)
    if not callable(save):
        return
    try:
        data = save()
    except Exception:
        return
    if not isinstance(data, dict):
        return
    try:
        text = json.dumps(data)
    except (TypeError, ValueError):
        return
    if password and len(password) >= 8 and password in text:
        return
    temporary = parent / f".{_STORAGE_NAME}.{uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
    except OSError:
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, storage)
        os.chmod(storage, 0o600)
        parent.chmod(0o700)
    except OSError:
        try:
            if not temporary.is_symlink():
                temporary.unlink()
        except OSError:
            pass


@contextmanager
def playwright_session(username: str, password: str, *, video_dir=None, repo_root=None):
    """Open one headed Chrome window, sign in once, and yield that page.

    When video_dir is outside the repository, the browser context records the
    session there. A storage file in that same private run area is reused
    when the session is still logged in: Lightning home opens and
    login.salesforce.com is not loaded. The context is closed before the
    browser so the video file is finalized, including when sign-in or the
    walk fails early. The password is typed into the login form and is not
    written or logged.
    """
    if not username or not password:
        raise PrivateRunnerError("Missing approved secret environment variables")
    folder = _accept_video_dir(video_dir, repo_root)
    storage = _session_storage_path(folder, repo_root)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = _launch_chrome(playwright)
        context = None
        page = None
        try:
            options = _context_options(folder, storage)
            try:
                context = browser.new_context(**options)
            except Exception:
                if "storage_state" not in options:
                    raise
                context = browser.new_context(**_context_options(folder, None))
            page = context.new_page()
            page.set_default_timeout(20000)
            _accept_ok_dialogs(page)
            if not _resume_logged_in(page, storage):
                login_to_salesforce(page, username, password)
            yield page
        finally:
            try:
                _save_browser_state(context, page, storage, password)
            finally:
                try:
                    _finalize_recording(context, page, folder)
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass


def refuse_unsettled(status: str, *, still_loading: bool) -> str:
    """A page that is still loading is not a failed guide step."""
    if still_loading:
        return "NOT_RUN"
    return status


def run_uploaded_guide(
    username: str,
    password: str,
    inventory: GuideInventory,
    *,
    login: Callable[[str, str], None] | None = None,
    plans: Sequence[PlannedStep] | None = None,
    execute_step: Callable | None = None,
    page=None,
    output_dir: str | Path | None = None,
    progress: Callable[[str, str], None] | None = None,
    repo_root: str | Path | None = None,
    run_id: str | None = None,
    login_budget: int = LOGIN_BUDGET_SECONDS,
    session: Callable | None = None,
    checks=None,
    walk_timeout: int = LOAD_WAIT_SECONDS,
    page_wait_s: float = 90,
    aliases: dict[str, str] | None = None,
) -> UploadedRun:
    """Sign in and run the checks this repo can execute.

    The CLI entry point still requires an authored plan and a page review.
    The local site opens one Playwright browser, signs in with the posted
    username and password, and walks guide checks in printed order. A step
    with no check stays NOT RUN. A sign-in or still-loading page is not a
    guide failure. PASS is recorded only with evidence.
    """
    if type(login_budget) is not int or not 0 <= login_budget <= 600:
        login_budget = LOGIN_BUDGET_SECONDS
    if type(walk_timeout) is not int or not 0 <= walk_timeout <= 180:
        walk_timeout = LOAD_WAIT_SECONDS
    report_id = run_id.strip() if isinstance(run_id, str) and run_id.strip() else uuid4().hex

    def tell(phase: str, detail: str) -> None:
        if progress is not None:
            progress(phase, detail)

    def signed_out(attempted: bool, message: str) -> UploadedRun:
        if _contains_secret(message, username, password):
            message = "Sign-in could not finish."
        return _uploaded(
            inventory, attempted, False, message,
            report_id, output_dir, repo_root, password,
        )

    def finish(produced: tuple[ReportStep, ...], instance: dict | None = None) -> UploadedRun:
        executed = sum(
            1 for step in produced if step.status not in {"NOT_RUN", "RUNNING"}
        )
        result = UploadedRun(
            True, True, "Signed in to the trial org.", produced, executed, instance,
        )
        work, temporary = _work_dir(output_dir, repo_root)
        try:
            _store_uploaded(result, report_id, work, password)
        finally:
            if temporary is not None:
                temporary.cleanup()
        return result

    def run_walk(opened) -> UploadedRun:
        tell(
            "running",
            "Running every guide step in order. The next step starts only after this one is verified.",
        )
        import time
        video_origin = time.monotonic()

        def heartbeat_callback(run_id_str: str, index: int, total: int, step_id: str, status: str, timestamp: float) -> None:
            if output_dir is None:
                return
            from datetime import datetime, timezone
            progress_path = Path(output_dir) / "progress.json"
            if progress_path.is_symlink():
                return
            payload = {
                "run_id": report_id,
                "index": index,
                "total": total,
                "step_id": step_id,
                "status": status,
                "time": datetime.now(timezone.utc).isoformat(),
            }
            import os
            temp = progress_path.parent / f".progress.{uuid4().hex}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                fd = os.open(temp, flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                os.chmod(temp, 0o600)
                os.replace(temp, progress_path)
                os.chmod(progress_path, 0o600)
            except OSError:
                try:
                    if temp.exists() and not temp.is_symlink():
                        temp.unlink()
                except OSError:
                    pass

        # Read-only REST query on the signed-in session; the page is not touched.
        instance = org_instance.from_session(opened)
        if instance is not None:
            tell("instance", f"Org instance: {instance['instance']}.")
        outcome = walk_guide(
            opened, inventory, checks=checks, progress=progress,
            timeout_s=walk_timeout,
            page_wait_s=page_wait_s,
            aliases=aliases,
            video_origin=video_origin,
            heartbeat=heartbeat_callback,
        )
        notes = {
            step_id: _scrub(note, username, password)
            for step_id, note in outcome.notes.items()
        }
        evidence = {}
        for step_id, ids in outcome.evidence.items():
            safe = tuple(
                item for item in ids
                if not _contains_secret(item, username, password)
            )
            if safe:
                evidence[step_id] = safe
        produced = _inventory_report(inventory, outcome.statuses, notes, evidence, outcome.timestamps, outcome.suspects)
        # Company Information is read in its own tab only after the walk, so
        # the guide order is never disturbed.
        if instance is None:
            instance = org_instance.detect(opened)
        else:
            instance = {**instance, **org_instance.trust_details(instance["instance"])}
        return finish(produced, instance)

    def run_plans(opened, chosen: tuple[PlannedStep, ...]) -> UploadedRun:
        tell(
            "running",
            "Running the validator. Steps without an executable check stay NOT RUN.",
        )
        return _execute_plans(
            opened, inventory, chosen, execute_step, report_id,
            output_dir, repo_root, password,
        )

    tell(
        "signing_in",
        "Signing in at login.salesforce.com. Sign-in is limited to about two minutes.",
    )
    # A caller that injects login= is a unit test. It does not open a browser.
    # The local site injects neither login nor session, so it launches Playwright.
    if page is None and session is None and login is not None:
        try:
            _bounded_call(login, username, password, login_budget)
        except ImportError:
            return signed_out(False, "Playwright is not installed, so sign-in did not run.")
        except PrivateRunnerError as exc:
            return signed_out(True, str(exc))
        except Exception:
            return signed_out(True, "Sign-in could not finish.")
        if plans:
            return run_plans(page, tuple(plans))
        return _uploaded(
            inventory, True, True, "Signed in to the trial org.",
            report_id, output_dir, repo_root, password,
        )

    if page is not None and session is None:
        if plans:
            return run_plans(page, tuple(plans))
        return run_walk(page)

    if session is not None:
        manager = session(username, password)
    else:
        manager = playwright_session(
            username, password, video_dir=output_dir, repo_root=repo_root,
        )
    try:
        opened = manager.__enter__()
    except ImportError:
        return signed_out(False, "Playwright is not installed, so sign-in did not run.")
    except PrivateRunnerError as exc:
        return signed_out(True, str(exc))
    except Exception as exc:
        if type(exc).__module__.startswith("playwright"):
            return signed_out(True, "Playwright could not open a browser, so sign-in did not run.")
        return signed_out(True, "Sign-in could not finish.")
    try:
        if plans:
            return run_plans(opened, tuple(plans))
        return run_walk(opened)
    finally:
        manager.__exit__(None, None, None)


def _contains_secret(text: str, username: str, password: str) -> bool:
    if not isinstance(text, str) or not text:
        return False
    for secret in (password, username):
        if secret and len(secret) >= 8 and secret in text:
            return True
    return False


def _scrub(text: str, username: str, password: str) -> str:
    if _contains_secret(text, username, password):
        return "The step note was omitted."
    return text


def _execute_plans(
    page, inventory, chosen, execute_step, report_id, output_dir, repo_root, password,
) -> UploadedRun:
    loading_ids: set[str] = set()

    def executor(browser_page, step, context):
        try:
            if execute_step is not None:
                return execute_step(browser_page, step, context)
            from .pipeline import _default_execute
            return _default_execute(browser_page, step, context)
        except PageStillLoading:
            loading_ids.add(step.step_id)
            raise BrowserActionError("page still loading") from None

    work, temporary = _work_dir(output_dir, repo_root)
    try:
        db_path = work / "checkpoint.sqlite"
        outcome = validate_plan(
            page, db_path, report_id, "disposable-org", chosen,
            source_reconciled=False,
            execute_step=executor,
            max_wait_seconds=0,
        )
        if db_path.exists():
            db_path.chmod(0o600)
        evidence = _checkpoint_evidence(db_path, report_id)
        statuses = {
            step_id: refuse_unsettled(status, still_loading=step_id in loading_ids)
            for step_id, status in outcome.statuses.items()
        }
        notes = {
            step_id: (
                "The page was still loading after about 90 seconds, "
                "so this step was not marked failed."
            )
            for step_id in loading_ids
        }
        produced = _inventory_report(inventory, statuses, notes, evidence)
        executed = sum(
            1 for step in produced if step.status not in {"NOT_RUN", "RUNNING"}
        )
        result = UploadedRun(
            True, True, "Signed in to the trial org.", produced, executed,
        )
        _store_uploaded(result, report_id, work, password)
        return result
    finally:
        if temporary is not None:
            temporary.cleanup()


def _uploaded(
    inventory: GuideInventory,
    attempted: bool,
    signed_in: bool,
    message: str,
    report_id: str,
    output_dir: str | Path | None,
    repo_root: str | Path | None,
    password: str,
) -> UploadedRun:
    result = UploadedRun(
        attempted, signed_in, message, _inventory_report(inventory), 0,
    )
    work, temporary = _work_dir(output_dir, repo_root)
    try:
        _store_uploaded(result, report_id, work, password)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return result


def _inventory_report(
    inventory: GuideInventory,
    statuses: dict[str, str] | None = None,
    notes: dict[str, str] | None = None,
    evidence: dict[str, tuple[str, ...]] | None = None,
    timestamps: dict[str, tuple[float, float]] | None = None,
    suspects: dict[str, tuple[str, ...]] | None = None,
) -> tuple[ReportStep, ...]:
    statuses = statuses or {}
    notes = notes or {}
    evidence = evidence or {}
    timestamps = timestamps or {}
    suspects = suspects or {}
    items = []
    for step in inventory.steps:
        status = statuses.get(step.step_id, "NOT_RUN")
        if status not in {
            "PASS", "UI_MISMATCH", "FUNCTIONAL_FAIL", "ENV_BLOCKED",
            "AUTH_BLOCKED", "NEEDS_REVIEW", "NOT_RUN", "RUNNING", "RETRY_WAIT",
        }:
            status = "NOT_RUN"
        found = tuple(evidence.get(step.step_id, ()))
        note = notes.get(step.step_id, "")
        if status == "PASS" and not found:
            status = "NEEDS_REVIEW"
            note = note or "A pass was reported without evidence."
        if status == "NOT_RUN" and step.step_id not in notes:
            note = ""
        already = tuple(item for item in found if ":already-set" in item)
        ts = timestamps.get(step.step_id)
        t_start = ts[0] if ts else None
        t_end = ts[1] if ts else None
        items.append(ReportStep(
            step.step_id, step.page, step.text, status, found, note,
            behavioral_classification=(
                "PRECONDITION_ALREADY_MET_UNPROVEN" if status == "PASS" and already else None
            ),
            behavioral_evidence_ids=already if status == "PASS" else (),
            t_start=t_start,
            t_end=t_end,
            suspect=suspects.get(step.step_id, ()),
        ))
    return tuple(items)


def _bounded_call(login: Callable[[str, str], None], username: str, password: str, budget: int) -> None:
    box: dict = {}

    def work() -> None:
        try:
            login(username, password)
            box["ok"] = True
        except Exception as exc:
            box["exc"] = exc

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(budget)
    if thread.is_alive():
        raise PrivateRunnerError(
            "Salesforce login timed out or requires interactive authentication"
        )
    if "exc" in box:
        raise box["exc"]


def _work_dir(output_dir: str | Path | None, repo_root: str | Path | None):
    persist = output_dir is not None and not (
        repo_root is not None and _path_inside(Path(output_dir), Path(repo_root))
    )
    if not persist:
        temporary = tempfile.TemporaryDirectory(prefix="wv-run-")
        return Path(temporary.name), temporary
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path, None


def _path_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _store_uploaded(result: UploadedRun, report_id: str, work: Path, password: str) -> None:
    """Write counts and the private HTML report. Skip anything that contains the password."""
    if not result.report_steps:
        _write_counts(work, result, password)
        return
    try:
        html = render_report(report_id, list(result.report_steps), inventory_verified=False)
    except ValueError:
        html = ""
    if html and not _leaks_secret(html, password):
        destination = work / "validation-summary.html"
        if not destination.exists():
            write_private_report(destination, html)
    _write_counts(work, result, password)


def _write_counts(work: Path, result: UploadedRun, password: str) -> None:
    counts = Counter(step.status for step in result.report_steps)
    payload = {
        "signed_in": result.signed_in,
        "checks_executed": result.checks_executed,
        "steps": len(result.report_steps),
        "status_counts": {key: counts[key] for key in sorted(counts)},
    }
    text = json.dumps(payload)
    if _leaks_secret(text, password):
        return
    path = work / "status.json"
    if path.exists():
        return
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def _leaks_secret(text: str, password: str) -> bool:
    return bool(password) and len(password) >= 8 and password in text


def _checkpoint_evidence(db_path: Path, run_id: str) -> dict[str, tuple[str, ...]]:
    if not db_path.exists():
        return {}
    with sqlite3.connect(str(db_path)) as db:
        rows = db.execute(
            "SELECT step_id, verification_json FROM checkpoints WHERE run_id=?",
            (run_id,),
        ).fetchall()
    found = {}
    for step_id, data in rows:
        try:
            raw = json.loads(data or "{}").get("evidence_ids", [])
        except (TypeError, ValueError):
            raw = []
        if isinstance(raw, list):
            found[step_id] = tuple(
                item.strip() for item in raw if isinstance(item, str) and item.strip()
            )
    return found


def _protected_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


def build_review_notes(plans, statuses: dict[str, str],
                       probes: dict[str, ActionProbe]) -> list[ReviewNote]:
    """Create evidence-bound instructor notes, never untested guide edits."""
    notes = []
    for step in plans:
        status = statuses[step.step_id]
        probe = probes.get(step.step_id)
        probe_ids = tuple(dict.fromkeys(
            probe.before.evidence_ids + probe.after.evidence_ids
        )) if probe is not None else ()
        diagnostic = (
            f" Behavioral diagnostic: {probe.necessity.classification}; "
            f"before {probe.before.status}, after {probe.after.status}."
            if probe is not None else ""
        )
        if status != "PASS":
            note_status = status if status != "RUNNING" else "NEEDS_REVIEW"
            notes.append(ReviewNote(
                step.page, step.step_id, note_status, step.title,
                "Step blocked, unverified or failed an independent assertion." + diagnostic,
                "Investigate the live UI and independently verify any proposed change.",
                probe_ids,
            ))
        elif probe is not None and probe.before.status == "PASS":
            notes.append(ReviewNote(
                step.page, step.step_id, "NEEDS_REVIEW", step.title,
                "Desired state already existed before the literal action." + diagnostic +
                " No clean-baseline omission or negative-control replay was performed by this runner.",
                "Do not remove or rewrite this instruction without controlled replay and instructor approval.",
                probe_ids,
            ))
    return notes


def build_inline_note_placements(
    plan_path: str | Path, notes: list[ReviewNote],
    probes: dict[str, ActionProbe] | None = None,
) -> list[InlineNote]:
    """Use explicit, reviewed PDF source anchors for short inline findings.

    An unanchored finding must never be placed on an unrelated page. Private
    plans keep source-specific page coordinates outside the public repository.
    """
    content = _json(plan_path)
    if not isinstance(content, dict) or not isinstance(content.get("steps"), list):
        raise PrivateRunnerError("Invalid private plan for PDF placement")
    anchors = {}
    for item in content["steps"]:
        if not isinstance(item, dict):
            raise PrivateRunnerError("Malformed PDF source placement")
        step_id = item.get("step_id")
        if step_id in anchors:
            raise PrivateRunnerError("Duplicate step source anchor")
        anchors[step_id] = item.get("inline_after_y")
    probes = probes or {}
    output = []
    labels = {
        "UI_MISMATCH": "The guide's UI target did not match the observed interface.",
        "FUNCTIONAL_FAIL": "The result did not meet its required assertion.",
        "NEEDS_REVIEW": "This instruction still needs independent behavioral verification.",
        "ENV_BLOCKED": "Environment blocked; not evidence that the guide is wrong.",
        "AUTH_BLOCKED": "Authentication or permissions blocked this instruction.",
        "NOT_RUN": "Prerequisite unverified; this instruction was not executed.",
        "RETRY_WAIT": "The step is waiting for the rate-limit retry cooldown.",
    }
    for note in notes:
        anchor = anchors.get(note.step_id)
        if (type(anchor) not in (int, float) or not math.isfinite(anchor)):
            raise PrivateRunnerError(
                "Each affected guide step needs a reviewed inline_after_y anchor"
            )
        probe = probes.get(note.step_id)
        if probe is not None and probe.before.status == "PASS":
            summary = "The expected state was already correct before this manual edit."
            check = "Test skipping only this edit and verify downstream behavior."
        elif note.status in {"ENV_BLOCKED", "RETRY_WAIT"}:
            summary = labels.get(note.status, "Environment blocked.")
            check = "Respect cooldowns and retry; do not call the guide outdated."
        else:
            summary = labels.get(note.status, "Instruction requires instructor review.")
            check = "Inspect private evidence and verify the corrected UI path."
        output.append(InlineNote(note.source_page, note.step_id, anchor,
                                 summary, check, note.status))
    return output


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
    # Require full page review and exact executable coverage BEFORE authentication.
    if not source_review_path:
        raise PrivateRunnerError("A complete private page review is required before login")
    review = load_source_review(source_review_path)
    try:
        reviewed_ids = require_executable_source_review(inventory, review, digest, plans)
    except SourcePreflightError:
        raise PrivateRunnerError("Private source review or plan preflight failed") from None
    reconciled = True
    user = os.environ.get("SF_USERNAME", "")
    password = os.environ.get("SF_PASSWORD", "")
    if not user or not password:
        raise PrivateRunnerError("Private runner secrets are not configured")
    _protected_dir(out)
    evidence = out / "screenshots"
    _protected_dir(evidence)
    run_id = "run-" + uuid4().hex[:14]
    org_id = "disposable-org"
    screenshot_refs: dict[str, str] = {}
    probe_records: dict[str, dict] = {}
    probe_objects: dict[str, ActionProbe] = {}
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
                screenshot = evidence / (step.step_id.replace(":", "_") + ".png")
                def capture_screenshot():
                    try:
                        page.screenshot(path=str(screenshot), full_page=False, animations="disabled")
                        screenshot.chmod(0o600)
                        screenshot_refs[step.step_id] = "screenshots/" + screenshot.name
                    except Exception:
                        raise BrowserActionError("Required private screenshot capture failed") from None
                try:
                    observed, probe = execute_with_necessity_probe(page, step, context)
                    capture_screenshot()
                    if responses:
                        raise RateLimitDetected("Observed Salesforce HTTP 429",
                            retry_after=responses[0].headers.get("retry-after"))
                    if probe is not None:
                        # Only classifications and evidence IDs; never raw DOM values.
                        probe_objects[step.step_id] = probe
                        probe_records[step.step_id] = asdict(probe)
                    return observed
                except RateLimitDetected:
                    raise
                except BrowserActionError:
                    if not screenshot.exists():
                        capture_screenshot()
                    # Save only private, unverified label candidates for later
                    # re-execution; do not silently adopt them as guide edits.
                    proposals = []
                    for action in step.actions:
                        try:
                            proposals += proposals_to_private_dict(
                                propose_locators(page, action.role, action.accessible_name))
                        except (ValueError, BrowserActionError):
                            continue
                    if proposals:
                        target = out / ("suggestions-" + step.step_id.replace(":", "_") + ".json")
                        with target.open("x", encoding="utf-8") as sink:
                            json.dump(proposals, sink, indent=2)
                        target.chmod(0o600)
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
                screenshot_refs=screenshot_refs,
                behavioral_probes=probe_objects,
            )
        finally:
            browser.close()

    if probe_records:
        probe_path = out / "behavioral-probes.json"
        with probe_path.open("x", encoding="utf-8") as sink:
            json.dump(probe_records, sink, indent=2)
        probe_path.chmod(0o600)

    html_path = out / "validation-summary.html"
    write_private_report(html_path, outcome.html)
    flagged = build_review_notes(plans, outcome.statuses, probe_objects)
    annotated = out / "annotated-instructor-review.pdf"
    # Source-reviewed coordinates make brief notes appear directly below the
    # affected instructions, never in disconnected afterthought sheets.
    placements = build_inline_note_placements(plan_path, flagged, probe_objects)
    annotate_pdf_inline(guide, placements, annotated, quick_summary=True)
    summary = {
        "run_id": run_id, "source_reconciled": reconciled,
        "fully_verified": outcome.fully_verified,
        "candidate_inventory_steps": len(inventory.steps),
        "planned_steps": len(plans),
        "status_counts": dict(Counter(outcome.statuses.values())),
        "report": str(html_path.name),
        "annotated_review": annotated.name,
        "annotated_layout": "compact_inline_after_step",
        "behavioral_probes": "behavioral-probes.json" if probe_records else None,
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
