"""Unattended guide walk for one verification org at a time.

Org rule: start on org 1. Move to the next org only after a run on the
current org ends in a final success or a real recorded failure that came
after the org was changed. A failure before anything was changed keeps the
org, because it is still as provisioned. An
inconclusive run (sign-in failure, 429, still loading, crash, or a step with
no check yet) keeps the same org. Passwords are read from a private file
outside the repository and are never printed.
"""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import sys
import time
import webbrowser

from .guide_inventory import extract_inventory
from .lessons import record_lessons
from .private_runner import run_uploaded_guide
from .step_board import (
    build_board,
    instance_runs_for,
    outcomes_from_report_steps,
    refresh_instance_reports,
    render_live_report,
    write_history_index,
    write_results_pdf,
    write_run_report,
)

# A real on-screen failure. The org was used, so a re-run needs a fresh org.
_REAL_FAIL = {"UI_MISMATCH", "FUNCTIONAL_FAIL"}
# Nothing was proven either way. The org may be tried again.
_INCONCLUSIVE = {"ENV_BLOCKED", "AUTH_BLOCKED", "NEEDS_REVIEW"}

GATE_NAME = "gate.json"
ORGS_NAME = "verification-orgs.json"
ALIASES_NAME = "aliases.json"
HISTORY_NAME = "history.json"

SUCCESS, FAILED, INCONCLUSIVE = "success", "failed", "inconclusive"
EXIT_CODES = {SUCCESS: 0, FAILED: 1, INCONCLUSIVE: 3}


def gate_path(root: Path) -> Path:
    return Path(root) / GATE_NAME


def load_gate(root: Path) -> dict:
    path = gate_path(root)
    if not path.is_file():
        return {"org": 0, "frozen": False, "reason": ""}
    gate = json.loads(path.read_text(encoding="utf-8"))
    if "org" not in gate:
        gate["org"] = int(gate.get("completed_clean") or 0)
    return gate


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)


def save_gate(root: Path, payload: dict) -> None:
    _write_private(gate_path(root), json.dumps(payload, indent=2) + "\n")


def load_orgs(root: Path) -> list[dict]:
    """Read orgs as a list of username/password pairs, or one password for many usernames."""
    path = Path(root) / ORGS_NAME
    if not path.is_file():
        raise SystemExit(f"Missing private org file: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("The private org file has no orgs.")
    orgs = data.get("orgs")
    if not orgs and isinstance(data.get("usernames"), list):
        orgs = [{"username": name, "password": data.get("password")} for name in data["usernames"]]
    if not isinstance(orgs, list) or not orgs:
        raise SystemExit("The private org file has no orgs.")
    return orgs


def load_aliases(root: Path) -> dict[str, str]:
    """Reviewed renames: guide name to on-screen name. Private, because it quotes the guide."""
    path = Path(root) / ALIASES_NAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("aliases", []) if isinstance(data, dict) else []
    return {
        str(item["guide"]): str(item["screen"])
        for item in entries
        if isinstance(item, dict) and item.get("approved") and item.get("guide") and item.get("screen")
    }


def next_org_index(gate: dict) -> int | None:
    """Return the org to run, or None while a freeze waits for a verified fix."""
    if gate.get("frozen"):
        return None
    return int(gate.get("org") or 0)


def outcome_of(result) -> tuple[str, str]:
    """Classify a finished run as success, a real failure, or inconclusive, with the reason."""
    if not result.signed_in:
        return INCONCLUSIVE, result.message or "Sign-in did not open the org."
    steps = result.report_steps
    if not steps or not any(step.status == "PASS" for step in steps):
        return INCONCLUSIVE, result.message or "No guide step passed."
    for step in steps:
        if step.status in _REAL_FAIL:
            return FAILED, step.note or step.title
        if step.status in _INCONCLUSIVE:
            return INCONCLUSIVE, step.note or step.title
        if step.status == "NOT_RUN" and "exceeded its time limit" in (step.note or ""):
            return INCONCLUSIVE, step.note or step.title
    if any(step.status != "PASS" and "loading" in (step.note or "").lower() for step in steps):
        return INCONCLUSIVE, "A page was still loading when the wait ended."
    return SUCCESS, ""


# Evidence from steps that only sign in, open a page, or read a state. A run
# that passed nothing else left the org as it was provisioned.
_READ_ONLY = {
    "already-signed-in", "lightning-open", "org-launched", "orgclaim-open", "opened-url",
    "redeem-unavailable", "personal-information", "setup-open", "users-open", "observed",
    "agentforce-agents", "agentforce-studio", "helloworld-open", "live-test-open", "script-view",
}


def org_changed(result) -> bool:
    """True once a passed step did more than sign in, open a page, or read."""
    for step in result.report_steps:
        if step.status != "PASS":
            continue
        for item in step.evidence_ids or ("unknown",):
            token = item.split(":", 1)[-1]
            if token not in _READ_ONLY and not token.startswith("already-set"):
                return True
    return False


def org_key(username: str) -> str:
    """Folder name per org, so a saved session is never reused for another user."""
    return "org-" + sha256(username.strip().lower().encode("utf-8")).hexdigest()[:10]


def append_history(root: Path, entry: dict) -> Path:
    path = Path(root) / "reports" / HISTORY_NAME
    runs = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    runs.append(entry)
    _write_private(path, json.dumps(runs[-200:], indent=1) + "\n")
    return path


def _summary(result, index: int, total: int, outcome: str, reason: str, run_dir: Path) -> dict:
    counts: dict[str, int] = {}
    for step in result.report_steps:
        counts[step.status] = counts.get(step.status, 0) + 1
    first = next((s for s in result.report_steps if s.status not in {"PASS", "NOT_RUN"}), None)
    return {
        "org": index + 1,
        "orgs": total,
        "outcome": outcome,
        "reason": reason,
        "signed_in": result.signed_in,
        "instance": getattr(result, "org_instance", None) or {"instance": "unknown"},
        "counts": counts,
        "first_failure": None if first is None else {
            "step_id": first.step_id, "page": first.page, "status": first.status,
        },
        "run_dir": str(run_dir),
    }


class LiveReport:
    """Keeps report.html current while a run is going, and opens it once.

    The page refreshes itself; the final report replaces it at the end.
    """

    def __init__(self, output: Path, *, guide_name: str, run_id: str, org: int, orgs: int,
                 opener=None, min_interval_s: float = 1.0):
        self.output = Path(output)
        self.meta = {"guide_name": guide_name, "run_id": run_id, "org": org, "orgs": orgs}
        self.opener = opener
        self.min_interval_s = min_interval_s
        self.lines: list[str] = []
        self.statuses: dict[str, str] = {}
        self.instance: dict | None = None
        self._written = 0.0
        self._opened = False

    def _read_progress(self) -> None:
        path = self.output / "progress.json"
        if not path.is_file() or path.is_symlink():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        status = str(data.get("status") or "")
        if data.get("step_id") and status not in {"", "NOT_RUN", "RUNNING"}:
            self.statuses[str(data["step_id"])] = status

    def update(self, phase: str, detail: str, *, force: bool = False, ended: str = "") -> None:
        line = f"{phase}: {detail}"
        if not self.lines or self.lines[-1] != line:
            self.lines.append(line)
            del self.lines[:-50]
        now = time.monotonic()
        if not force and self._opened and now - self._written < self.min_interval_s:
            return
        self._read_progress()
        counts: dict[str, int] = {}
        for status in self.statuses.values():
            counts[status] = counts.get(status, 0) + 1
        html = render_live_report(lines=self.lines, counts=counts, instance=self.instance,
                                  ended=ended, **self.meta)
        _write_private(self.output / "report.html", html)
        self._written = now
        if not self._opened:
            self._opened = True
            if self.opener is not None:
                try:
                    self.opener((self.output / "report.html").resolve().as_uri())
                except Exception:
                    pass


def run_next(repo_root: Path, private_root: Path, guide: Path, *, as_json: bool = False,
             opener=webbrowser.open) -> int:
    """Run the current org. Returns 0 success, 1 real failure, 3 inconclusive, 2 refused.

    opener shows the live report in the default browser; None skips it.
    """
    gate = load_gate(private_root)
    index = next_org_index(gate)
    if index is None:
        print(gate.get("reason") or "The queue is frozen. Run --reset-gate after a verified fix.")
        return 2
    orgs = load_orgs(private_root)
    if index >= len(orgs):
        print("Every verification org has been used.")
        return 2
    org = orgs[index]
    username = str(org.get("username") or "")
    password = str(org.get("password") or "")
    if not username or not password:
        print("The selected org is missing a username or password.")
        return 2
    inventory = extract_inventory(guide)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-org{index + 1}"
    output = private_root / "runs" / org_key(username) / run_id
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    log = (lambda *a, **k: print(*a, file=sys.stderr, **k)) if as_json else print
    log(f"Starting org {index + 1} of {len(orgs)}. {len(inventory.steps)} steps.", flush=True)

    live = LiveReport(output, guide_name=guide.name, run_id=run_id, org=index + 1,
                      orgs=len(orgs), opener=opener)
    live.update("starting", f"Org {index + 1} of {len(orgs)}. {len(inventory.steps)} steps.", force=True)

    def show(phase: str, detail: str) -> None:
        log(f"{phase}: {detail}", flush=True)
        if phase == "instance":
            live.instance = {"instance": detail.removeprefix("Org instance: ").rstrip(".")}
        live.update(phase, detail)

    try:
        result = run_uploaded_guide(
            username,
            password,
            inventory,
            output_dir=output,
            repo_root=repo_root,
            run_id=run_id,
            progress=show,
            aliases=load_aliases(private_root),
        )
    except BaseException:
        live.update("stopped", "The run stopped unexpectedly.", force=True,
                    ended="The run stopped unexpectedly. The watchdog or the next run picks it up.")
        raise
    outcome, reason = outcome_of(result)
    board = None
    if result.report_steps:
        board = build_board(
            inventory,
            outcomes=outcomes_from_report_steps(result.report_steps),
            notice=reason or result.message or "The guide walk finished.",
            run_id=run_id,
        )
        write_results_pdf(board, output / "results.pdf")
    record_lessons(private_root, [(s.title, s.status, s.note) for s in result.report_steps])
    # A success or a real failure used this org, unless nothing in it changed.
    # Inconclusive runs keep it.
    used = outcome in {SUCCESS, FAILED} and (outcome == SUCCESS or org_changed(result))
    save_gate(private_root, {
        "org": index + 1 if used else index,
        "frozen": outcome != SUCCESS,
        "reason": reason,
        "last_outcome": outcome,
        "last_run": run_id,
    })
    summary = {**_summary(result, index, len(orgs), outcome, reason, output), "guide_name": guide.name}
    history_path = append_history(private_root, {"run_id": run_id, "guide": guide.name, **summary})
    _write_private(output / "summary.json", json.dumps(summary, indent=2) + "\n")
    if board is not None:
        history = json.loads(history_path.read_text(encoding="utf-8"))
        write_run_report(board, output, summary=summary, guide_name=guide.name,
                         instance_runs=instance_runs_for(history, guide.name, output))
        refresh_instance_reports(private_root, guide.name)
    else:
        live.instance = summary["instance"]
        live.update("finished", outcome, force=True, ended=reason or result.message or "No guide step ran.")
    write_history_index(private_root)
    if as_json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Org {index + 1}: {outcome}. {reason}".strip())
    return EXIT_CODES[outcome]


def reset_gate(private_root: Path) -> int:
    gate = load_gate(private_root)
    gate["frozen"] = False
    gate["reason"] = ""
    save_gate(private_root, gate)
    print(f"Gate open. Next run uses org {int(gate.get('org') or 0) + 1}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the current verification org.")
    parser.add_argument("--guide")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--private-root", default=str(Path.home() / ".workshop-validator"))
    parser.add_argument("--reset-gate", action="store_true", help="Unfreeze after a verified fix.")
    parser.add_argument("--json", action="store_true", help="Print a sanitized JSON summary.")
    parser.add_argument("--no-open", action="store_true", help="Do not open the live report in the browser.")
    args = parser.parse_args(argv)
    root = Path(args.private_root).expanduser()
    if args.reset_gate:
        return reset_gate(root)
    if not args.guide:
        parser.error("--guide is required unless --reset-gate is given")
    return run_next(Path(args.repo).resolve(), root, Path(args.guide).expanduser(), as_json=args.json,
                    opener=None if args.no_open else webbrowser.open)


if __name__ == "__main__":
    sys.exit(main())
