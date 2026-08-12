"""One HTTP-transport integration test: spawn the real `caseclerk serve
--transport http` as a subprocess, do the OAuth dance over the wire exactly as
ChatGPT's connector would, call one tool, and confirm the audit log recorded
it. The main e2e happy path (test_happy_path.py) stays stdio-based and
unchanged; this is the one addition for the ChatGPT remote-access stage.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from caseclerk_core import db
from caseclerk_fixtures import build_fixture_drive

REPO_ROOT = Path(__file__).resolve().parents[2]
PORT = 8940
STARTUP_TIMEOUT_SECONDS = 20
PROCESS_TIMEOUT_SECONDS = 120
REDIRECT_URI = "https://example-connector.test/callback"


def _caseclerk_binary() -> Path:
    venv_bin = Path(sys.executable).parent
    name = "caseclerk.exe" if sys.platform == "win32" else "caseclerk"
    binary = venv_bin / name
    if not binary.is_file():
        raise RuntimeError(f"caseclerk console script not found at {binary}; run `uv sync --all-packages`")
    return binary


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


def _wait_until_ready(client: httpx.Client, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            resp = client.get("/.well-known/oauth-authorization-server")
            if resp.status_code == 200:
                return
        except httpx.HTTPError as exc:  # noqa: PERF203 - a startup poll loop, not a hot path
            last_exc = exc
        time.sleep(0.3)
    raise TimeoutError(f"HTTP transport did not become ready within {timeout}s: {last_exc}")


@pytest.mark.e2e
def test_http_transport_oauth_dance_and_audit_log(tmp_path: Path) -> None:
    documents_root = build_fixture_drive(tmp_path / "documents")
    env_overrides = {
        "CASECLERK_CONFIG_DIR": str(tmp_path / "config"),
        "CASECLERK_DATA_DIR": str(tmp_path / "data"),
        "CASECLERK_DOCUMENTS_ROOT": str(documents_root),
    }
    caseclerk_bin = _caseclerk_binary()

    # deterministic: index before the server even starts, same reasoning as the stdio e2e
    process_result = subprocess.run(
        [str(caseclerk_bin), "process"],
        env={**os.environ, **env_overrides},
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )
    assert process_result.returncode == 0, process_result.stdout + process_result.stderr

    server_process = subprocess.Popen(
        [str(caseclerk_bin), "serve", "--transport", "http", "--port", str(PORT)],
        env={**os.environ, **env_overrides},
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=10) as client:
            _wait_until_ready(client, STARTUP_TIMEOUT_SECONDS)

            dcr_resp = client.post(
                "/register",
                json={
                    "redirect_uris": [REDIRECT_URI],
                    "client_name": "e2e-test-client",
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
            assert dcr_resp.status_code == 201, dcr_resp.text
            client_id = dcr_resp.json()["client_id"]

            verifier, challenge = _pkce_pair()
            auth_resp = client.get(
                "/authorize",
                params={
                    "client_id": client_id,
                    "redirect_uri": REDIRECT_URI,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": "e2e",
                },
                follow_redirects=False,
            )
            assert auth_resp.status_code in (302, 307), auth_resp.text
            code = parse_qs(urlparse(auth_resp.headers["location"]).query)["code"][0]

            token_resp = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                    "client_id": client_id,
                    "code_verifier": verifier,
                },
            )
            assert token_resp.status_code == 200, token_resp.text
            access_token = token_resp.json()["access_token"]

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/event-stream",
            }
            init_resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "e2e-test-client", "version": "1.0"},
                    },
                },
                headers=headers,
            )
            assert init_resp.status_code == 200, init_resp.text
            session_id = init_resp.headers.get("mcp-session-id")
            call_headers = dict(headers)
            if session_id:
                call_headers["mcp-session-id"] = session_id
            client.post(
                "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=call_headers
            )

            tool_resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "list_clients", "arguments": {}},
                },
                headers=call_headers,
            )
            assert tool_resp.status_code == 200, tool_resp.text
    finally:
        server_process.terminate()
        try:
            server_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_process.wait(timeout=10)

    conn = db.connect(tmp_path / "data" / db.DB_FILE_NAME)
    try:
        entries = db.list_remote_requests(conn)
    finally:
        conn.close()
    assert len(entries) == 1
    assert entries[0].tool == "list_clients"
    assert entries[0].ok is True
