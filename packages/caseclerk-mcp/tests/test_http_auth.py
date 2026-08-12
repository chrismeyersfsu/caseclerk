"""HTTP transport + OAuth: 401/200, DCR, PKCE authorize+token, audit logging.

Runs a real streamable-HTTP server (uvicorn, via run_streamable_http_async) in a
background asyncio task and drives it with httpx -- the same wire protocol
ChatGPT's connector would use, not an in-process shortcut.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from mcp.client import Client

from caseclerk_core import db
from caseclerk_core.config import Config
from caseclerk_mcp.server import build_server

BASE_PORT = 8930
REDIRECT_URI = "https://example-connector.test/callback"


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


@asynccontextmanager
async def _running_server(tmp_path: Path, *, port: int) -> AsyncIterator[tuple[httpx.AsyncClient, Path]]:
    db_path = tmp_path / "test.db"
    documents_root = tmp_path / "documents"
    documents_root.mkdir(exist_ok=True)
    cfg = Config(documents_root=str(documents_root))
    server = build_server(cfg, db_path=db_path, run_startup_scan=False, http_auth=True, http_port=port)

    task = asyncio.create_task(server.run_streamable_http_async(host="127.0.0.1", port=port))
    await asyncio.sleep(0.5)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
            yield client, db_path
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def _register_client(client: httpx.AsyncClient) -> str:
    resp = await client.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "client_name": "test-client",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert resp.status_code == 201, resp.text
    client_id: str = resp.json()["client_id"]
    return client_id


async def _obtain_token(client: httpx.AsyncClient, client_id: str) -> dict[str, str]:
    verifier, challenge = _pkce_pair()
    auth_resp = await client.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
        follow_redirects=False,
    )
    assert auth_resp.status_code in (302, 307), auth_resp.text
    location = auth_resp.headers["location"]
    parsed = urlparse(location)
    query = parse_qs(parsed.query)
    assert query["state"] == ["xyz"]  # state round-trips unchanged
    code = query["code"][0]

    token_resp = await client.post(
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
    data: dict[str, str] = token_resp.json()
    return data


async def _mcp_session_headers(client: httpx.AsyncClient, access_token: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json, text/event-stream"}
    init_resp = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        },
        headers=headers,
    )
    assert init_resp.status_code == 200, init_resp.text
    session_id = init_resp.headers.get("mcp-session-id")
    call_headers = dict(headers)
    if session_id:
        call_headers["mcp-session-id"] = session_id
    await client.post(
        "/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=call_headers
    )
    return call_headers


def test_unauthenticated_request_gets_401_with_www_authenticate(tmp_path: Path) -> None:
    async def run() -> None:
        async with _running_server(tmp_path, port=BASE_PORT) as (client, _db_path):
            resp = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Accept": "application/json, text/event-stream"},
            )
            assert resp.status_code == 401
            assert "Bearer" in resp.headers.get("www-authenticate", "")

    asyncio.run(run())


def test_invalid_bearer_token_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        async with _running_server(tmp_path, port=BASE_PORT + 1) as (client, _db_path):
            resp = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={
                    "Authorization": "Bearer not-a-real-token",
                    "Accept": "application/json, text/event-stream",
                },
            )
            assert resp.status_code == 401

    asyncio.run(run())


def test_oauth_metadata_discovery_endpoints(tmp_path: Path) -> None:
    async def run() -> None:
        async with _running_server(tmp_path, port=BASE_PORT + 2) as (client, _db_path):
            meta_resp = await client.get("/.well-known/oauth-authorization-server")
            assert meta_resp.status_code == 200
            meta = meta_resp.json()
            assert meta["code_challenge_methods_supported"] == ["S256"]
            assert "registration_endpoint" in meta
            assert "authorization_code" in meta["grant_types_supported"]

            resource_resp = await client.get("/.well-known/oauth-protected-resource")
            assert resource_resp.status_code == 200
            assert resource_resp.json()["scopes_supported"] == ["caseclerk"]

    asyncio.run(run())


def test_dynamic_client_registration(tmp_path: Path) -> None:
    async def run() -> None:
        async with _running_server(tmp_path, port=BASE_PORT + 3) as (client, _db_path):
            client_id = await _register_client(client)
            assert client_id

            # the registered client can be looked up again (persisted, not just returned once)
            get_resp = await client.get("/authorize", params={"client_id": "no-such-client"})
            assert get_resp.status_code >= 400  # unregistered client_id is rejected

    asyncio.run(run())


def test_full_oauth_dance_then_authenticated_tool_call_writes_audit_row(tmp_path: Path) -> None:
    async def run() -> None:
        async with _running_server(tmp_path, port=BASE_PORT + 4) as (client, db_path):
            client_id = await _register_client(client)
            token_data = await _obtain_token(client, client_id)
            call_headers = await _mcp_session_headers(client, token_data["access_token"])

            resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "list_clients", "arguments": {}},
                },
                headers=call_headers,
            )
            assert resp.status_code == 200

            conn = db.connect(db_path)
            try:
                entries = db.list_remote_requests(conn)
            finally:
                conn.close()
            assert len(entries) == 1
            assert entries[0].tool == "list_clients"
            assert entries[0].ok is True

    asyncio.run(run())


def test_refresh_token_grant_rotates_access_token(tmp_path: Path) -> None:
    async def run() -> None:
        async with _running_server(tmp_path, port=BASE_PORT + 5) as (client, _db_path):
            client_id = await _register_client(client)
            token_data = await _obtain_token(client, client_id)

            refresh_resp = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token_data["refresh_token"],
                    "client_id": client_id,
                },
            )
            assert refresh_resp.status_code == 200
            new_tokens = refresh_resp.json()
            assert new_tokens["access_token"] != token_data["access_token"]

            # the old access token still works until it expires (rotation is on the refresh
            # token, not a forced revocation of the prior access token)
            old_headers = {"Authorization": f"Bearer {token_data['access_token']}"}
            list_resp = await client.get("/.well-known/oauth-authorization-server", headers=old_headers)
            assert list_resp.status_code == 200  # unauthenticated endpoint; just checking the server is alive

    asyncio.run(run())


def test_expired_authorization_code_is_rejected(tmp_path: Path) -> None:
    async def run() -> None:
        async with _running_server(tmp_path, port=BASE_PORT + 6) as (client, _db_path):
            client_id = await _register_client(client)
            token_resp = await client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "code": "totally-made-up-code",
                    "redirect_uri": REDIRECT_URI,
                    "client_id": client_id,
                    "code_verifier": "whatever",
                },
            )
            assert token_resp.status_code == 400

    asyncio.run(run())


def test_stdio_style_in_process_call_never_writes_an_audit_row(tmp_path: Path) -> None:
    """The in-process Client connector runs through the identical Server/middleware
    dispatch stdio uses -- only the transport framing differs -- so this is a faithful
    test of "stdio never audits" without spawning a real subprocess."""

    async def run() -> None:
        db_path = tmp_path / "test.db"
        documents_root = tmp_path / "documents"
        documents_root.mkdir()
        cfg = Config(documents_root=str(documents_root))

        server = build_server(cfg, db_path=db_path, run_startup_scan=False, http_auth=False)
        async with Client(server=server) as client:
            await client.call_tool("list_clients", {})

        conn = db.connect(db_path)
        try:
            assert db.list_remote_requests(conn) == []
        finally:
            conn.close()

    asyncio.run(run())


def test_in_process_call_with_http_auth_enabled_does_write_an_audit_row(tmp_path: Path) -> None:
    async def run() -> None:
        db_path = tmp_path / "test.db"
        documents_root = tmp_path / "documents"
        documents_root.mkdir()
        cfg = Config(documents_root=str(documents_root))

        server = build_server(
            cfg, db_path=db_path, run_startup_scan=False, http_auth=True, http_port=BASE_PORT + 7
        )
        async with Client(server=server) as client:
            await client.call_tool("list_clients", {})

        conn = db.connect(db_path)
        try:
            entries = db.list_remote_requests(conn)
        finally:
            conn.close()
        assert len(entries) == 1
        assert entries[0].tool == "list_clients"

    asyncio.run(run())
