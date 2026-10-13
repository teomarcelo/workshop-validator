"""Fail-closed HTTPS input handoff for a private GitHub-hosted worker.

Secret URLs, tokens, guide bytes and source-specific plan contents are never
printed, passed in command arguments, or committed to the public repository.
"""

from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import argparse
import os


class DownloadError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise DownloadError("Unexpected redirect from approved private storage")


def download_one(url: str, destination: str | Path,
                 *, allowed_host: str, token: str = "",
                 max_bytes: int = 25 * 1024 * 1024):
    """Read from one explicitly allowlisted HTTPS host, refusing redirects."""
    parts = urlsplit(url)
    allowed_host = allowed_host.lower().strip()
    if (parts.scheme != "https" or not allowed_host or
            parts.hostname != allowed_host or parts.username or parts.password or
            parts.fragment or not parts.path or parts.port not in (None, 443)):
        raise DownloadError("Private input URL did not meet HTTPS host policy")
    destination = Path(destination)
    if destination.exists():
        raise DownloadError("Refusing to overwrite an input")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    opener = build_opener(NoRedirect())
    headers = {"Accept": "application/octet-stream"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(url, headers=headers, method="GET")
    written = 0
    try:
        with opener.open(request, timeout=35) as response:
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None and int(raw_length) > max_bytes:
                raise DownloadError("Private input exceeds size cap")
            with destination.open("xb") as output:
                destination.chmod(0o600)
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise DownloadError("Private input exceeds size cap")
                    output.write(chunk)
        if written == 0:
            raise DownloadError("Private input was empty")
    except Exception:
        destination.unlink(missing_ok=True)
        # Never surface underlying HTTP exception: it can contain a signed URL.
        raise DownloadError("Approved private input could not be retrieved") from None
    return written


def main():
    parser = argparse.ArgumentParser(description="Private storage handoff; no URL in args")
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    base = Path(args.dir)
    host = os.getenv("PRIVATE_STORAGE_HOST", "")
    token = os.getenv("PRIVATE_CONTENT_BEARER_TOKEN", "")
    inputs = {
        "guide.pdf": os.getenv("PRIVATE_GUIDE_URL", ""),
        "plan.json": os.getenv("PRIVATE_PLAN_URL", ""),
        "review.json": os.getenv("PRIVATE_REVIEW_URL", ""),
    }
    if not host or not inputs["guide.pdf"] or not inputs["plan.json"]:
        raise SystemExit("Private storage configuration incomplete; nothing downloaded")
    base.mkdir(parents=True, exist_ok=True)
    base.chmod(0o700)
    for filename, url in inputs.items():
        if url:
            download_one(url, base / filename, allowed_host=host,
                         token=token, max_bytes=(25 * 1024 * 1024 if filename.endswith(".pdf")
                                                 else 2 * 1024 * 1024))
    print("Private input handoff completed; URL and file contents suppressed.")


if __name__ == "__main__":
    main()
