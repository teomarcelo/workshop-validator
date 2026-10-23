"""Local website for one AGT503 validation run.

The page accepts an exercise-guide PDF and the disposable trial org
username and password. The password stays in that request. The PDF is
written to a private 0600 file outside the repository only long enough
to inventory it.

Submitting the form reads the PDF, opens a headed Chrome window, signs
in, and walks the guide checks in printed order. A blocked promos.html
iframe is ignored. When a saved sign-in is still logged in, the next
upload opens Lightning home and does not load the login page. The
command-line runner still requires an authored plan and a page review.
This local path does not. A step with no executable check stays NOT RUN.
PASS is shown only with evidence. A sign-in or still-loading page is
not a guide failure.

The Playwright session is recorded into that run's private directory.
The results page plays the recording. The saved browser state stays in
that same private run area, outside the repository, mode 0600. The
video stays outside the repository, including when the walk stops early.
"""

from dataclasses import dataclass
from html import escape
from pathlib import Path
import argparse
import hmac
import json
import logging
import os
import re
import secrets
import sys
import tempfile
import threading
from collections.abc import Callable, Sequence
from uuid import uuid4

from .guide_inventory import GuideInventory, extract_inventory
from .private_runner import (
    RECORDING_NAME,
    PrivateRunnerError,
    UploadedRun,
    run_uploaded_guide,
)
from .report import ReportStep
from .step_board import (
    Board,
    StepOutcome,
    build_board,
    is_check_line,
    is_freeze_line,
    outcomes_from_report_steps,
    recording_section,
    render_activity,
    render_board,
    render_document,
)


MAX_PDF = 32 * 1024 * 1024
_RUN_ID = re.compile(r"^[0-9a-f]{32}$")
_FORM_LEDE = "Check an exercise guide against one disposable trial org."
logger = logging.getLogger(__name__)


class LocalSiteError(ValueError):
    """Safe message for the form. Never carries a password or org host."""


@dataclass(frozen=True)
class LoginResult:
    attempted: bool
    signed_in: bool
    message: str


@dataclass(frozen=True)
class RunState:
    run_id: str
    phase: str
    detail: str
    board: Board | None = None
    log: tuple[str, ...] = ()


class RunStore:
    def __init__(self, limit: int = 8) -> None:
        self._limit = limit
        self._items: dict[str, RunState] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def begin(self, run_id: str, detail: str) -> None:
        self._remember(RunState(run_id, "reading", detail, None, _progress_log((), detail)))

    def update(self, run_id: str, phase: str, detail: str) -> None:
        with self._lock:
            current = self._items.get(run_id)
            if current is not None and current.phase == "done":
                return
            board = current.board if current is not None else None
            previous = current.log if current is not None else ()
        self._remember(RunState(run_id, phase, detail, board, _progress_log(previous, detail)))

    def fail(self, run_id: str, detail: str) -> None:
        self.update(run_id, "error", detail)

    def put(self, board: Board) -> None:
        with self._lock:
            current = self._items.get(board.run_id)
            previous = current.log if current is not None else ()
        self._remember(RunState(board.run_id, "done", "", board, previous))

    def get(self, run_id: str) -> Board | None:
        state = self.get_state(run_id)
        if state is None:
            return None
        return state.board

    def get_state(self, run_id: str) -> RunState | None:
        with self._lock:
            return self._items.get(run_id)

    def _remember(self, state: RunState) -> None:
        with self._lock:
            if state.run_id not in self._order:
                self._order.append(state.run_id)
            self._items[state.run_id] = state
            while len(self._order) > self._limit:
                old = self._order.pop(0)
                if old == state.run_id:
                    self._order.append(old)
                    if len(self._order) == 1:
                        break
                    continue
                self._items.pop(old, None)


def _progress_log(previous: tuple[str, ...], detail: str) -> tuple[str, ...]:
    line = " ".join(detail.split())
    if not line:
        return previous
    if previous and previous[-1] == line and not is_check_line(line):
        return previous
    return previous + (line,)


def _write_progress(folder: Path, log: tuple[str, ...]) -> None:
    """Server-side progress for this run. Not browser localStorage."""
    try:
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / "progress.txt"
        path.write_text("\n".join(log) + ("\n" if log else ""), encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        return


def _read_progress(folder: Path) -> tuple[str, ...]:
    path = folder / "progress.txt"
    if not path.is_file():
        return ()
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def package_repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_private_root() -> Path:
    return Path.home() / ".workshop-validator" / "runs"


def salesforce_login_attempt(
    username: str,
    password: str,
    *,
    connect: Callable[[str, str], None] | None = None,
) -> LoginResult:
    """Sign in with the username-first login form. Do not record the password."""
    if not username or not password:
        return LoginResult(False, False, "Sign-in did not run.")
    connect = connect or _playwright_login
    try:
        connect(username, password)
    except ImportError:
        return LoginResult(
            False, False, "Playwright is not installed, so sign-in did not run."
        )
    except PrivateRunnerError as exc:
        return LoginResult(True, False, _guard(str(exc), username, password))
    except Exception:
        return LoginResult(True, False, "Sign-in could not finish.")
    return LoginResult(True, True, "Signed in to the trial org.")


def compose_notice(
    login: LoginResult,
    inventory: GuideInventory,
    *,
    executed: bool,
    validator_ran: bool = False,
) -> str:
    count = len(inventory.steps)
    noun = "step" if count == 1 else "steps"
    read = f"The PDF was read ({count} numbered {noun})."
    if login.signed_in:
        sign = "Signed in to the trial org."
    else:
        sign = _sentence(login.message or "Sign-in did not run.")
    if executed:
        tail = (
            "These statuses come from the validator. "
            "A task passes only when every step in it passes. "
            "A step shows PASS only when that run recorded evidence for it. "
            "A step that was not executed stays NOT RUN."
        )
    elif login.signed_in and validator_ran:
        tail = (
            "The validator ran after sign-in. "
            "Guide steps were not executed. "
            "Every guide step is NOT RUN. "
            "No executable check is available for those steps. "
            "A step shows PASS only when a run recorded that result with evidence."
        )
    elif login.signed_in:
        tail = (
            "Guide steps were not executed. "
            "Every guide step is NOT RUN. "
            "A step shows PASS only when a run recorded that result with evidence."
        )
    else:
        tail = (
            "Guide steps were not executed. "
            "Every guide step is NOT RUN. "
            "A sign-in failure is not a failure of the guide. "
            "A step shows PASS only when a run recorded that result with evidence."
        )
    text = f"{read} {sign} {tail}"
    warnings = len(inventory.warnings)
    if warnings == 1:
        text += " The PDF inventory reported 1 extraction warning. That is not a step result."
    elif warnings:
        text += (
            f" The PDF inventory reported {warnings} extraction warnings. "
            "Those are not step results."
        )
    missing = len(inventory.pages_without_steps)
    if missing == 1:
        text += " 1 page had no numbered steps."
    elif missing:
        text += f" {missing} pages had no numbered steps."
    if not inventory.steps:
        text += " No numbered steps were found in this PDF."
    return text


def perform_run(
    pdf: bytes,
    username: str,
    password: str,
    *,
    repo_root: Path,
    private_root: Path,
    login: Callable[[str, str], LoginResult],
    extract: Callable[[str | Path], GuideInventory] | None = None,
    report_steps: Sequence[ReportStep] = (),
    validate: Callable | None = None,
    progress: Callable[[str, str], None] | None = None,
    run_id: str | None = None,
    output_dir: Path | None = None,
) -> Board:
    """Read the PDF, sign in, run available checks, and build the board.

    The local-site validator does not require an authored plan or a page
    review. Steps it does not execute stay NOT RUN. A caller can still pass
    finished report rows, which are shown only when sign-in succeeded.
    """
    def tell(phase: str, detail: str) -> None:
        if progress is not None:
            progress(phase, detail)

    tell("reading", "Reading the exercise guide PDF.")
    username = _require_username(username)
    password = _require_password(password)
    if not isinstance(pdf, (bytes, bytearray)) or not bytes(pdf).startswith(b"%PDF-"):
        raise LocalSiteError("Choose a PDF file.")
    if len(pdf) > MAX_PDF:
        raise LocalSiteError("That PDF is larger than 32 MB.")
    try:
        inventory = _extract_privately(
            bytes(pdf),
            Path(repo_root),
            Path(private_root),
            extract or extract_inventory,
        )
    except LocalSiteError:
        raise
    except RuntimeError as exc:
        if "pdfplumber" in str(exc):
            raise LocalSiteError("Install the project dependencies, then try again.") from None
        raise LocalSiteError("Could not read that PDF.") from None
    except Exception:
        raise LocalSiteError("Could not read that PDF.") from None
    if _secret_in_inventory(inventory, password):
        raise LocalSiteError("The run could not be completed.")
    site_run = run_id.strip() if isinstance(run_id, str) and run_id.strip() else uuid4().hex
    if validate is not None:
        uploaded = validate(
            username,
            password,
            inventory,
            output_dir=output_dir,
            progress=progress,
            repo_root=repo_root,
            run_id=site_run,
        )
        if not isinstance(uploaded, UploadedRun):
            uploaded = UploadedRun(True, False, "Sign-in could not finish.", (), 0)
        login_result = LoginResult(
            bool(uploaded.attempted),
            bool(uploaded.signed_in),
            _guard(str(uploaded.message), username, password),
        )
        produced = _without_guide_failures(uploaded.report_steps, login_result.signed_in)
    else:
        tell(
            "signing_in",
            "Signing in at login.salesforce.com. Sign-in is limited to about two minutes.",
        )
        try:
            login_result = login(username, password)
        except Exception:
            login_result = LoginResult(True, False, "Sign-in could not finish.")
        if not isinstance(login_result, LoginResult):
            login_result = LoginResult(True, False, "Sign-in could not finish.")
        login_result = LoginResult(
            bool(login_result.attempted),
            bool(login_result.signed_in),
            _guard(str(login_result.message), username, password),
        )
        produced = _without_guide_failures(tuple(report_steps), login_result.signed_in)
    executed = login_result.signed_in and any(
        step.status not in {"NOT_RUN", "RUNNING"} for step in produced
    )
    validator_ran = validate is not None and login_result.signed_in and not executed
    outcomes = outcomes_from_report_steps(produced)
    board = build_board(
        inventory,
        outcomes=outcomes,
        notice=compose_notice(
            login_result,
            inventory,
            executed=executed,
            validator_ran=validator_ran,
        ),
        run_id=site_run,
    )
    rendered = render_board(board)
    if len(password) >= 8 and password in rendered:
        raise LocalSiteError("The run could not be completed.")
    if output_dir is not None:
        _save_results_pdf(board, Path(output_dir), password)
    return board


def _save_results_pdf(board: Board, folder: Path, password: str) -> None:
    from .step_board import write_results_pdf

    destination = folder / "results.pdf"
    try:
        write_results_pdf(board, destination)
    except OSError:
        return
    try:
        text = destination.read_bytes().decode("latin-1", errors="ignore")
    except OSError:
        return
    if len(password) >= 8 and password in text:
        destination.unlink(missing_ok=True)


def _without_guide_failures(steps: Sequence[ReportStep], signed_in: bool) -> tuple[ReportStep, ...]:
    """A failed sign-in must not be stored as a failed guide step."""
    rows = tuple(steps)
    if signed_in:
        return rows
    return tuple(
        ReportStep(step.step_id, step.page, step.title, "NOT_RUN", (), "")
        for step in rows
    )


def _write_orgs_file(private_root: Path, usernames: list[str], password: str) -> None:
    """Write verification-orgs.json in the shape automate.load_orgs expects."""
    orgs = [{"username": u, "password": password} for u in usernames]
    payload = {"orgs": orgs}
    path = private_root / "verification-orgs.json"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _orgs_changed(private_root: Path, usernames: list[str]) -> bool:
    """Check if the org list differs from the existing file."""
    path = private_root / "verification-orgs.json"
    if not path.is_file():
        return True
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        orgs = data.get("orgs", [])
        existing = [o.get("username") for o in orgs if isinstance(o, dict)]
        return existing != usernames
    except (OSError, json.JSONDecodeError, ValueError):
        return True


def _reset_gate_if_needed(private_root: Path, usernames: list[str]) -> None:
    """Reset gate to org 1 if the org list changed."""
    if _orgs_changed(private_root, usernames):
        gate_path = private_root / "gate.json"
        gate_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        gate_path.write_text(json.dumps({"org": 0, "frozen": False, "reason": ""}, indent=2) + "\n", encoding="utf-8")
        gate_path.chmod(0o600)


def _launch_team(repo_root: Path, pdf_path: Path, mode: str, launcher: Callable | None = None) -> None:
    """Launch detached claude process for AI team validation (injectable for tests)."""
    if launcher is not None:
        launcher(repo_root, pdf_path, mode)
        return
    import subprocess
    cmd = [
        "claude",
        "-p", f"/validate-guide {pdf_path} --mode {mode}",
        "--permission-mode", "acceptEdits",
    ]
    subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _execute(app, pdf: bytes, username: str, password: str, *, run_id: str | None, progress):
    output = None
    if run_id and app.config["validate"] is not None:
        output = Path(app.config["private_root"]) / run_id
    return perform_run(
        pdf,
        username,
        password,
        repo_root=app.config["repo_root"],
        private_root=app.config["private_root"],
        login=app.config["login"],
        extract=app.config["extract"],
        validate=app.config["validate"],
        progress=progress,
        run_id=run_id,
        output_dir=output,
    )


def _start_background(app, pdf: bytes, username: str, password: str):
    from flask import redirect

    run_id = uuid4().hex
    store = app.config["runs"]
    folder = Path(app.config["private_root"]) / run_id
    store.begin(run_id, "Reading the exercise guide PDF.")

    def persist() -> None:
        state = store.get_state(run_id)
        if state is not None:
            _write_progress(folder, state.log)

    persist()

    def worker() -> None:
        def progress(phase: str, detail: str) -> None:
            store.update(run_id, phase, detail)
            persist()

        with app.config["run_lock"]:
            try:
                board = _execute(
                    app, pdf, username, password, run_id=run_id,
                    progress=progress,
                )
                store.put(board)
                persist()
            except LocalSiteError as exc:
                store.fail(run_id, _guard(str(exc), username, password))
                persist()
            except Exception as exc:
                logger.error("request failed: %s", type(exc).__name__)
                store.fail(run_id, "The run could not be completed.")
                persist()

    threading.Thread(target=worker, daemon=True).start()
    return redirect(f"/runs/{run_id}", code=303)


def _recording_file(private_root: Path, repo_root: Path, run_id: str) -> Path | None:
    """The one video for this run, or None. Never follows a caller-supplied path."""
    if not _RUN_ID.fullmatch(run_id or ""):
        return None
    root = Path(private_root).expanduser()
    repo = Path(repo_root).resolve()
    if root.is_symlink():
        return None
    try:
        root = root.resolve()
    except OSError:
        return None
    if _inside(root, repo):
        return None
    directory = root / run_id
    if directory.is_symlink() or not directory.is_dir():
        return None
    try:
        directory = directory.resolve()
    except OSError:
        return None
    if directory.parent != root or not _inside(directory, root):
        return None
    path = directory / RECORDING_NAME
    if path.is_symlink() or not path.is_file():
        return None
    try:
        resolved = path.resolve()
        if resolved.stat().st_size <= 0:
            return None
    except OSError:
        return None
    if resolved.parent != directory or resolved.name != RECORDING_NAME:
        return None
    if _inside(resolved, repo):
        return None
    return resolved


def _results_file(private_root: Path, repo_root: Path, run_id: str) -> Path | None:
    """The results PDF for this run, or None. Never follows a caller-supplied path."""
    if not _RUN_ID.fullmatch(run_id or ""):
        return None
    root = Path(private_root).expanduser()
    repo = Path(repo_root).resolve()
    if root.is_symlink():
        return None
    try:
        root = root.resolve()
    except OSError:
        return None
    if _inside(root, repo):
        return None
    directory = root / run_id
    if directory.is_symlink() or not directory.is_dir():
        return None
    try:
        directory = directory.resolve()
    except OSError:
        return None
    if directory.parent != root or not _inside(directory, root):
        return None
    path = directory / "results.pdf"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        resolved = path.resolve()
        if resolved.stat().st_size <= 0 or not resolved.read_bytes().startswith(b"%PDF-"):
            return None
    except OSError:
        return None
    if resolved.parent != directory or resolved.name != "results.pdf":
        return None
    if _inside(resolved, repo):
        return None
    return resolved


def _results_href(app, run_id: str) -> str:
    path = _results_file(app.config["private_root"], app.config["repo_root"], run_id)
    if path is None:
        return ""
    return f"/runs/{run_id}/results.pdf"


def _recording_href(app, run_id: str) -> str:
    path = _recording_file(app.config["private_root"], app.config["repo_root"], run_id)
    if path is None:
        return ""
    return f"/runs/{run_id}/video"


def _status_page(
    detail: str,
    *,
    refresh: bool,
    failed: bool = False,
    recording_href: str = "",
    log: tuple[str, ...] = (),
):
    tone = "failed" if failed else "pending"
    if refresh:
        helper = (
            "This page updates every few seconds. The list is kept on this server "
            "for this run, not in the browser. Sign-in is limited to about two minutes. "
            "When the list stops growing, the run has finished."
        )
        script = "<meta http-equiv='refresh' content='2'>"
        lede = "The run is still going."
        recording_href = ""
    else:
        helper = "Guide steps were not marked failed because of this."
        script = ""
        lede = "The run stopped before the guide steps were executed."
    footer = (
        f"<p class='helper'>{helper}</p>"
        "<p class='back'><a href='/'>Back</a></p>"
    )
    activity = render_activity(log, live=bool(refresh), footer=footer)
    frozen = is_freeze_line(detail) or any(is_freeze_line(line) for line in log)
    if activity and (refresh or frozen):
        main = activity
    elif activity:
        main = (
            "<div class='card'>"
            f"<p class='notice {tone}'>{escape(detail)}</p>"
            "</div>"
            f"{activity}"
        )
    else:
        main = (
            "<div class='card'>"
            f"<p class='notice {tone}'>{escape(detail)}</p>"
            f"{footer}"
            "</div>"
        )
    body = f"{main}{recording_section(recording_href)}{script}"
    return render_document(title="Workshop Validator", lede=lede, body=body), 200


def create_app(
    *,
    login: Callable[[str, str], LoginResult] | None = None,
    validate: Callable | None = None,
    background: bool = False,
    private_root: Path | None = None,
    repo_root: Path | None = None,
    extract: Callable[[str | Path], GuideInventory] | None = None,
    team_launcher: Callable | None = None,
):
    """Flask app bound to local use. The secret key lives only in this process."""
    from flask import Flask, redirect, request, send_file, session
    from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

    app = Flask(__name__)
    app.secret_key = secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = MAX_PDF
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    if validate is not None:
        chosen = validate
    elif login is None:
        chosen = run_uploaded_guide
    else:
        chosen = None
    app.config["login"] = login or salesforce_login_attempt
    app.config["validate"] = chosen
    app.config["background"] = background
    app.config["private_root"] = Path(private_root) if private_root else default_private_root()
    app.config["repo_root"] = Path(repo_root) if repo_root else package_repo_root()
    app.config["extract"] = extract or extract_inventory
    app.config["runs"] = RunStore()
    app.config["run_lock"] = threading.Lock()
    app.config["team_launcher"] = team_launcher

    def form_page(*, error: str = "", username: str = "", status: int = 200):
        token = _csrf_token(session)
        body = _form_body(error=error, username=username, token=token)
        html = render_document(title="Workshop Validator", lede=_FORM_LEDE, body=body)
        return html, status

    @app.get("/")
    def index():
        return form_page()

    @app.post("/run")
    def run():
        no_ai_team = request.form.get("no_ai_team") == "1"

        # Handle both old (username) and new (usernames) fields for backward compatibility
        if "username" in request.form and not request.form.get("usernames"):
            # Legacy single-username field
            username = request.form.get("username", "")
            usernames = [username.strip()] if username.strip() else []
            echo_usernames = username
        else:
            # New multi-username textarea
            usernames_text = request.form.get("usernames", "")
            usernames = [
                line.strip() for line in usernames_text.split("\n")
                if line.strip()
            ]
            echo_usernames = "\n".join(usernames) if usernames else ""

        password = request.form.get("password", "")
        use_saved = request.form.get("use_saved_orgs") == "1"
        mode = request.form.get("mode", "quick")

        if not _csrf_ok(session, request.form.get("csrf_token", "")):
            return form_page(
                error="The form expired. Reload the page and try again.",
                username=echo_usernames,
                status=400,
            )

        try:
            pdf = _posted_pdf(request)

            # If using AI team, handle org file and launch
            if not no_ai_team:
                if not use_saved:
                    if not usernames:
                        return form_page(
                            error="Enter at least one username.",
                            username=echo_usernames,
                            status=400,
                        )
                    if len(usernames) > 5:
                        return form_page(
                            error="Enter at most 5 usernames.",
                            username=echo_usernames,
                            status=400,
                        )
                    if not password:
                        return form_page(
                            error="Enter the password.",
                            username=echo_usernames,
                            status=400,
                        )
                    private_root = Path(app.config["private_root"])
                    _reset_gate_if_needed(private_root, usernames)
                    _write_orgs_file(private_root, usernames, password)

                # Save PDF to uploads/<id>.pdf
                pdf_id = uuid4().hex
                private_root = Path(app.config["private_root"])
                uploads_dir = private_root / "uploads"
                uploads_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                pdf_path = uploads_dir / f"{pdf_id}.pdf"
                pdf_path.write_bytes(pdf)
                pdf_path.chmod(0o600)

                # Start job
                from .team_state import start_job
                start_job(private_root, str(pdf_path), mode)

                # Launch AI team
                repo_root = Path(app.config["repo_root"])
                launcher = app.config.get("team_launcher")
                _launch_team(repo_root, pdf_path, mode, launcher=launcher)

                # Redirect to team status page
                return redirect("/team", code=303)

            # Legacy direct walk path (no AI team checkbox checked)
            safe_user = usernames[0] if usernames else ""
            safe_password = password if isinstance(password, str) else ""
            if app.config["background"]:
                return _start_background(app, pdf, safe_user, safe_password)
            with app.config["run_lock"]:
                board = _execute(app, pdf, safe_user, safe_password, run_id=None, progress=None)
                app.config["runs"].put(board)
            return redirect(f"/runs/{board.run_id}", code=303)
        except LocalSiteError as exc:
            message = str(exc)
            if isinstance(password, str) and len(password) >= 8 and password in message:
                message = "The run could not be completed."
            return form_page(error=message, username=echo_usernames, status=400)
        except Exception:
            logger.error("request failed: %s", "run")
            return form_page(error="The run could not be completed.", username=echo_usernames, status=500)

    @app.get("/runs/<run_id>")
    def results(run_id: str):
        if not _RUN_ID.fullmatch(run_id):
            return _missing()
        state = app.config["runs"].get_state(run_id)
        if state is None:
            return _missing()
        if state.phase == "error":
            return _status_page(
                state.detail, refresh=False, failed=True,
                recording_href=_recording_href(app, run_id),
                log=state.log,
            )
        if state.board is None:
            return _status_page(
                state.detail or "Reading the exercise guide PDF.",
                refresh=True,
                log=state.log,
            )
        return render_board(
            state.board,
            recording_href=_recording_href(app, run_id),
            results_href=_results_href(app, run_id),
            activity=state.log,
        )

    @app.get("/runs/<run_id>/results.pdf")
    def results_pdf(run_id: str):
        if app.config["runs"].get_state(run_id) is None:
            return _missing()
        path = _results_file(
            app.config["private_root"], app.config["repo_root"], run_id,
        )
        if path is None:
            return _missing()
        return send_file(path, mimetype="application/pdf", conditional=True, max_age=0)

    @app.get("/runs/<run_id>/video")
    def recording(run_id: str):
        if app.config["runs"].get_state(run_id) is None:
            return _missing()
        path = _recording_file(
            app.config["private_root"], app.config["repo_root"], run_id,
        )
        if path is None:
            return _missing()
        return send_file(path, mimetype="video/webm", conditional=True, max_age=0)

    @app.get("/team")
    def team_status():
        from .team_state import load
        private_root = Path(app.config["private_root"])
        state = load(private_root)

        if state is None:
            body = (
                "<div class='card'><p>No active AI team job.</p>"
                "<p class='back'><a href='/'>Back</a></p></div>"
            )
            return render_document(
                title="AI Team Status",
                lede="No active job",
                body=body,
            ), 200

        # Check for stalled marker
        stalled_path = private_root / "team" / "stalled.json"
        stalled = ""
        if stalled_path.is_file():
            try:
                stalled_data = json.loads(stalled_path.read_text(encoding="utf-8"))
                stalled = stalled_data.get("reason", "Team stalled")
            except (OSError, json.JSONDecodeError):
                stalled = "Team stalled"

        # Load team log (last ~15 entries)
        log_path = private_root / "team" / "team-log.json"
        log_entries = []
        if log_path.is_file():
            try:
                all_entries = json.loads(log_path.read_text(encoding="utf-8"))
                log_entries = all_entries[-15:] if isinstance(all_entries, list) else []
            except (OSError, json.JSONDecodeError):
                pass

        # Load progress if run_dir exists
        progress_text = ""
        if state.get("run_dir"):
            progress_path = Path(state["run_dir"]) / "progress.json"
            if progress_path.is_file():
                try:
                    progress_data = json.loads(progress_path.read_text(encoding="utf-8"))
                    step_index = progress_data.get("step_index", "?")
                    step_id = progress_data.get("step_id", "")
                    progress_text = f"Step {step_index}: {step_id}"
                except (OSError, json.JSONDecodeError):
                    pass

        # Build status HTML
        phase = state.get("phase", "unknown")
        iteration = state.get("iteration", 0)
        stalled_banner = ""
        if stalled:
            stalled_banner = (
                "<div class='notice failed' style='margin-bottom:16px'>"
                f"<strong>Team stalled:</strong> {escape(stalled)}"
                "</div>"
            )

        log_html = ""
        if log_entries:
            rows = []
            for entry in log_entries:
                agent = escape(entry.get("agent", ""))
                action = escape(entry.get("action", ""))
                detail = escape(entry.get("detail", ""))
                time_str = escape(entry.get("time", "")[-8:] if entry.get("time") else "")
                row = f"<li><strong>{time_str}</strong> {agent}: {action}"
                if detail:
                    row += f" — {detail}"
                row += "</li>"
                rows.append(row)
            log_html = f"<h3>Recent activity</h3><ol class='progress'>{''.join(rows)}</ol>"

        body = (
            f"{stalled_banner}"
            "<div class='card'>"
            f"<h2>Phase: {escape(phase)}</h2>"
            f"<p>Iteration: {iteration}</p>"
        )
        if progress_text:
            body += f"<p>Current: {escape(progress_text)}</p>"
        body += (
            f"{log_html}"
            "<p class='back'><a href='/'>Back</a></p>"
            "</div>"
            "<meta http-equiv='refresh' content='5'>"
        )

        return render_document(
            title="AI Team Status",
            lede="Validation in progress",
            body=body,
        ), 200

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return form_page(error="That PDF is larger than 32 MB.", status=413)

    @app.errorhandler(404)
    def missing(_exc):
        return _missing()

    @app.errorhandler(Exception)
    def failed(exc):
        if isinstance(exc, HTTPException):
            return exc
        logger.error("request failed: %s", type(exc).__name__)
        return form_page(error="The run could not be completed.", status=500)

    @app.after_request
    def harden(response):
        if (response.mimetype or "").lower() == "video/webm":
            response.headers["Cache-Control"] = "private, max-age=0"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; "
            "script-src 'unsafe-inline'; img-src data:; media-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        return response

    return app


def main(argv: list[str] | None = None, *, serve: Callable | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local workshop validation site")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        print("The site only listens on 127.0.0.1.", file=sys.stderr)
        return 2
    if type(args.port) is not int or not 1 <= args.port <= 65535:
        print("Choose a port from 1 to 65535.", file=sys.stderr)
        return 2
    app = create_app(background=True)
    (serve or _serve)(app, args.port)
    return 0


def _serve(app, port: int) -> None:
    print(f"Workshop Validator is at http://127.0.0.1:{port}", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


def _playwright_login(username: str, password: str) -> None:
    from .private_runner import playwright_session

    with playwright_session(username, password):
        return


def _extract_privately(
    pdf: bytes,
    repo_root: Path,
    private_root: Path,
    extract: Callable[[str | Path], GuideInventory],
) -> GuideInventory:
    root = _prepare_private_root(private_root, repo_root)
    with tempfile.TemporaryDirectory(prefix="wv-", dir=root) as directory:
        folder = Path(directory)
        resolved = folder.resolve()
        if folder.is_symlink() or _inside(resolved, repo_root.resolve()):
            raise LocalSiteError("Refusing to store a guide inside the repository")
        folder.chmod(0o700)
        path = folder / "guide.pdf"
        _write_private(path, pdf)
        if _inside(path.resolve(), repo_root.resolve()):
            raise LocalSiteError("Refusing to store a guide inside the repository")
        return extract(path)


def _prepare_private_root(root: Path, repo_root: Path) -> Path:
    root = Path(root).expanduser()
    repo = repo_root.resolve()
    existing = root
    while not existing.exists():
        if existing.parent == existing:
            break
        existing = existing.parent
    if _inside(existing.resolve(), repo):
        raise LocalSiteError("Refusing to store a guide inside the repository")
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    if root.is_symlink() or _inside(root.resolve(), repo):
        raise LocalSiteError("Refusing to store a guide inside the repository")
    return root.resolve()


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    os.chmod(path, 0o600)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _secret_in_inventory(inventory: GuideInventory, secret: str) -> bool:
    if not secret or len(secret) < 8:
        return False
    blobs = [" ".join(inventory.warnings)]
    for step in inventory.steps:
        blobs.extend((step.text, step.exercise or "", step.task or "", step.number))
    return any(secret in blob for blob in blobs)


def _guard(message: str, username: str, password: str) -> str:
    cleaned = message.strip()
    for secret in (password, username):
        if not secret:
            continue
        if cleaned == secret or (len(secret) >= 8 and secret in cleaned):
            return "Sign-in could not finish."
    return message


def _sentence(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return "Sign-in did not finish."
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def _require_username(value: str) -> str:
    if not isinstance(value, str):
        raise LocalSiteError("Enter the trial org username.")
    text = value.strip()
    if not text or len(text) > 200 or any(ord(char) < 32 for char in text):
        raise LocalSiteError("Enter the trial org username.")
    return text


def _require_password(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 500:
        raise LocalSiteError("Enter the trial org password.")
    if any(char in value for char in ("\r", "\n", "\x00")):
        raise LocalSiteError("Enter the trial org password.")
    return value


def _echo_username(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or len(text) > 200 or any(ord(char) < 32 for char in text):
        return ""
    return text


def _csrf_token(session) -> str:
    token = session.get("csrf")
    if not isinstance(token, str) or len(token) < 16:
        token = secrets.token_urlsafe(32)
        session["csrf"] = token
    return token


def _csrf_ok(session, sent: object) -> bool:
    expected = session.get("csrf", "")
    if not isinstance(expected, str) or not isinstance(sent, str):
        return False
    if len(expected) < 16 or not sent:
        return False
    return hmac.compare_digest(expected, sent)


def _posted_pdf(request) -> bytes:
    upload = request.files.get("guide")
    if upload is None or not (upload.filename or "").strip():
        raise LocalSiteError("Choose a PDF file.")
    data = upload.read(MAX_PDF + 1)
    if not isinstance(data, (bytes, bytearray)) or not bytes(data).startswith(b"%PDF-"):
        raise LocalSiteError("Choose a PDF file.")
    if len(data) > MAX_PDF:
        raise LocalSiteError("That PDF is larger than 32 MB.")
    return bytes(data)


def _form_body(*, error: str, username: str, token: str) -> str:
    error_html = f"<p class='error'>{escape(error)}</p>" if error else ""
    return (
        "<div class='card'><form method='post' action='/run' "
        "enctype='multipart/form-data'>"
        f"<input type='hidden' name='csrf_token' value='{escape(token, quote=True)}'>"
        f"{error_html}"
        "<div class='fields'>"
        "<label for='guide'>Exercise guide PDF</label>"
        "<input id='guide' name='guide' type='file' accept='.pdf,application/pdf' required>"
        '<label for="usernames">Salesforce usernames (1-5, one per line)</label>'
        '<textarea id="usernames" name="usernames" rows="5" '
        'placeholder="user1@example.com\nuser2@example.com" '
        'style="width:100%;border:1px solid #d5d0c6;background:#fff;border-radius:10px;'
        'padding:10px 12px;font:16px/1.4 system-ui,sans-serif;color:#1c2430">'
        f"{escape(username)}</textarea>"
        "<label>"
        '<input type="checkbox" name="use_saved_orgs" value="1"> Use saved orgs'
        "</label>"
        "<label for='password'>Salesforce password</label>"
        "<input id='password' name='password' type='password' "
        "autocomplete='current-password'>"
        '<fieldset style="border:1px solid #d5d0c6;border-radius:10px;padding:12px;margin:16px 0">'
        '<legend style="font-weight:600">Verification mode</legend>'
        '<label style="display:flex;align-items:center;margin:4px 0">'
        '<input type="radio" name="mode" value="quick" checked style="margin-right:8px"> '
        "Quick — verify failures and suspicious passes"
        "</label>"
        '<label style="display:flex;align-items:center;margin:4px 0">'
        '<input type="radio" name="mode" value="full" style="margin-right:8px"> '
        "Full AI review — verify every step"
        "</label>"
        "</fieldset>"
        '<label style="margin-top:8px">'
        '<input type="checkbox" name="no_ai_team" value="1"> No AI team (direct walk)'
        "</label>"
        "</div>"
        "<button type='submit'>Run validation</button>"
        "<p class='helper'>The run reads the PDF, signs in at "
        "login.salesforce.com, and walks the guide checks in order. "
        "A saved sign-in that is still logged in opens the org home instead. "
        "Sign-in is limited to about two minutes. A step stays "
        "<span class='nowrap'>NOT RUN</span> until a check executes it. "
        "PASS is shown only with evidence. The password is not saved. "
        "The results page plays a recording of the browser session.</p>"
        "</form></div>"
    )


def _missing():
    body = (
        "<div class='card'><p>That page is not available.</p>"
        "<p class='back'><a href='/'>Back</a></p></div>"
    )
    html = render_document(title="Workshop Validator", lede=_FORM_LEDE, body=body)
    return html, 404


if __name__ == "__main__":
    raise SystemExit(main())
