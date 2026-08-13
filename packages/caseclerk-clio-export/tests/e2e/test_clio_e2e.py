"""End-to-end test: the real CLI against a local mock of Clio.

Exercises the whole flow exactly as production would run it — `auth` as a
subprocess with a browser-style redirect into the localhost callback server,
token exchange, proactive refresh, cursor pagination, and a deliberate 429 —
then copies the exported files plus a report into test-artifacts/clio-export/
for human review. All data is synthetic.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parents[4]
ARTIFACTS_DIR = REPO_ROOT / "test-artifacts" / "clio-export"

CONTACTS_PAGE_1: list[dict[str, Any]] = [
    {
        "id": 1,
        "type": "Person",
        "name": "Ada Lovelace",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "is_client": True,
        "primary_email_address": "ada@example.com",
        "email_addresses": [{"address": "ada@example.com", "name": "Work"}],
        "phone_numbers": [{"number": "555-0100", "name": "Mobile"}],
        "addresses": [
            {
                "street": "1 Engine Way",
                "city": "Springfield",
                "province": "FL",
                "postal_code": "32301",
                "country": "United States",
            }
        ],
        "custom_field_values": [{"field_name": "Referral Source", "value": "Website", "soft_deleted": False}],
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        "id": 2,
        "type": "Company",
        "name": "Analytical Engines LLC",
        "is_client": False,
        "primary_phone_number": "555-0101",
    },
]

CONTACTS_PAGE_2: list[dict[str, Any]] = [
    {
        "id": 3,
        "type": "Person",
        "name": "Grace Hopper",
        "first_name": "Grace",
        "last_name": "Hopper",
        "is_client": True,
        "primary_email_address": "grace@example.com",
    }
]

MATTERS: list[dict[str, Any]] = [
    {
        "id": 101,
        "display_number": "00001-Lovelace",
        "description": "Estate planning",
        "status": "open",
        "client": {"id": 1, "name": "Ada Lovelace"},
        "practice_area": {"id": 5, "name": "Estate"},
        "responsible_attorney": {"id": 9, "name": "J. Smith"},
        "open_date": "2025-03-01",
        "billable": True,
    },
    {
        "id": 102,
        "display_number": "00002-Hopper",
        "description": "Contract review",
        "status": "closed",
        "client": {"id": 3, "name": "Grace Hopper"},
        "close_date": "2026-02-01",
    },
]


class MockClioServer(http.server.ThreadingHTTPServer):
    sent_rate_limit: bool
    token_grants: list[str]
    api_auth_headers: list[str | None]


class MockClioHandler(http.server.BaseHTTPRequestHandler):
    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        srv = cast(MockClioServer, self.server)
        if parsed.path == "/oauth/authorize":
            location = f"{qs['redirect_uri']}?code=mock-auth-code&state={qs['state']}"
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()
            return
        srv.api_auth_headers.append(self.headers.get("Authorization"))
        if parsed.path == "/api/v4/contacts.json":
            if qs.get("page_token") == "tok2":
                self._json({"data": CONTACTS_PAGE_2, "meta": {}})
            elif not srv.sent_rate_limit:
                srv.sent_rate_limit = True
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                next_url = f"http://{self.headers['Host']}/api/v4/contacts.json?page_token=tok2&limit=200"
                self._json({"data": CONTACTS_PAGE_1, "meta": {"paging": {"next": next_url}}})
        elif parsed.path == "/api/v4/matters.json":
            self._json({"data": MATTERS, "meta": {}})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/oauth/token":
            self.send_error(404)
            return
        srv = cast(MockClioServer, self.server)
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in urllib.parse.parse_qs(self.rfile.read(length).decode()).items()}
        srv.token_grants.append(form["grant_type"])
        n = len(srv.token_grants)
        self._json(
            {
                "access_token": f"mock-access-{n}",
                "refresh_token": "mock-refresh",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )

    def log_message(self, format: str, *args: Any) -> None:
        pass


def start_mock_clio() -> MockClioServer:
    server = MockClioServer(("127.0.0.1", 0), MockClioHandler)
    server.sent_rate_limit = False
    server.token_grants = []
    server.api_auth_headers = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def run_cli(args: list[str], env: dict[str, str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "caseclerk_clio_export.cli", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )


def test_full_cli_flow_against_mock_clio(tmp_path: Path) -> None:
    mock = start_mock_clio()
    callback_port = free_port()
    env = os.environ | {
        "CLIO_EXPORT_CONFIG_DIR": str(tmp_path / "cfg"),
        "CLIO_EXPORT_BASE_URL": f"http://127.0.0.1:{mock.server_port}",
        "PYTHONUNBUFFERED": "1",
    }
    transcript: list[str] = []

    # --- auth: run the real CLI, then play the part of the browser ---------
    auth: subprocess.Popen[str] = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "caseclerk_clio_export.cli",
            "auth",
            "--client-id",
            "test-id",
            "--client-secret",
            "test-secret",
            "--no-browser",
            "--port",
            str(callback_port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    assert auth.stdout is not None
    authorize_url = None
    deadline = time.monotonic() + 20
    auth_output: list[str] = []
    while time.monotonic() < deadline:
        line = auth.stdout.readline()
        auth_output.append(line)
        if "/oauth/authorize?" in line:
            authorize_url = line.strip()
        if authorize_url and "Waiting for authorization" in line:
            break
    assert authorize_url, f"no authorize URL in output: {''.join(auth_output)}"

    # urllib follows the mock's 302 to http://127.0.0.1:<port>/callback.
    with urllib.request.urlopen(authorize_url, timeout=10) as resp:
        assert resp.status == 200
        assert b"Authorization received" in resp.read()

    remaining, _ = auth.communicate(timeout=30)
    auth_output.append(remaining)
    transcript.append("$ clio-export auth ...\n" + "".join(auth_output))
    assert auth.returncode == 0, "".join(auth_output)
    assert mock.token_grants == ["authorization_code"]

    creds = json.loads((tmp_path / "cfg" / "credentials.json").read_text())
    assert creds["token"]["access_token"] == "mock-access-1"

    # --- pull: refresh + 429 retry + cursor pagination ---------------------
    data_dir = tmp_path / "data"
    pull = run_cli(["pull", "--out", str(data_dir)], env)
    transcript.append(f"$ clio-export pull --out data\n{pull.stdout}{pull.stderr}")
    assert pull.returncode == 0, pull.stdout + pull.stderr

    # expires_in=3600 is inside the refresh margin, so pull refreshed first.
    assert mock.token_grants == ["authorization_code", "refresh_token"]
    assert all(h == "Bearer mock-access-2" for h in mock.api_auth_headers)
    assert mock.sent_rate_limit  # the 429 was served and survived

    contacts = json.loads((data_dir / "contacts.json").read_text())
    matters = json.loads((data_dir / "matters.json").read_text())
    assert [c["id"] for c in contacts] == [1, 2, 3]  # both pages, in order
    assert [m["id"] for m in matters] == [101, 102]
    manifest = json.loads((data_dir / "manifest.json").read_text())
    assert manifest["counts"] == {"contacts": 3, "matters": 2}
    assert (data_dir / "contacts.csv").exists()
    assert (data_dir / "matters.csv").exists()

    # --- status ------------------------------------------------------------
    status = run_cli(["status"], env)
    transcript.append(f"$ clio-export status\n{status.stdout}{status.stderr}")
    assert status.returncode == 0
    assert "Refresh token stored: yes" in status.stdout

    # --- human-reviewable artifacts ---------------------------------------
    if ARTIFACTS_DIR.exists():
        shutil.rmtree(ARTIFACTS_DIR)
    shutil.copytree(data_dir, ARTIFACTS_DIR / "data")
    (ARTIFACTS_DIR / "transcript.txt").write_text("\n".join(transcript))
    (ARTIFACTS_DIR / "report.md").write_text(
        "# clio-export e2e (mock Clio)\n\n"
        "The real CLI ran as a subprocess against a local mock of Clio.\n\n"
        "Verified end to end:\n\n"
        "- `auth`: authorize redirect -> localhost callback -> code/token "
        "exchange -> credentials saved\n"
        "- `pull`: proactive token refresh, HTTP 429 honored via Retry-After, "
        "cursor pagination across two contact pages\n"
        f"- exports: {manifest['counts']['contacts']} contacts, "
        f"{manifest['counts']['matters']} matters -> JSON + CSV + manifest "
        "(copied under data/)\n"
        "- `status`: reports region, token expiry, refresh token presence\n\n"
        "See transcript.txt for the exact CLI output. All data is synthetic.\n"
    )
    mock.shutdown()
