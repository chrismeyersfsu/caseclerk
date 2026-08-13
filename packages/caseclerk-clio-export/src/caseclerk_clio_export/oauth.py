"""OAuth 2.0 authorization-code flow against Clio.

Clio access tokens last 30 days; refresh tokens do not expire. After one
interactive `clio-export auth`, pulls can run unattended indefinitely.
"""

from __future__ import annotations

import http.server
import time
import urllib.parse
from typing import Any, cast

import httpx

TOKEN_TIMEOUT = 30.0


def authorize_url(base_url: str, client_id: str, redirect_uri: str, state: str) -> str:
    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{base_url}/oauth/authorize?{params}"


def _token_request(base_url: str, data: dict[str, str]) -> dict[str, Any]:
    resp = httpx.post(
        f"{base_url}/oauth/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=TOKEN_TIMEOUT,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token request failed ({resp.status_code}): {resp.text[:500]}")
    token: dict[str, Any] = resp.json()
    if "expires_in" in token:
        token["expires_at"] = time.time() + float(token["expires_in"])
    return token


def exchange_code(
    base_url: str, client_id: str, client_secret: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    return _token_request(
        base_url,
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
    )


def refresh_access_token(
    base_url: str, client_id: str, client_secret: str, token: dict[str, Any]
) -> dict[str, Any]:
    new = _token_request(
        base_url,
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": token["refresh_token"],
        },
    )
    if "refresh_token" not in new:  # Clio may not rotate refresh tokens
        new["refresh_token"] = token["refresh_token"]
    return new


class CallbackServer(http.server.HTTPServer):
    """Localhost server that captures the OAuth redirect parameters."""

    oauth_params: dict[str, str] | None = None


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404)
            return
        srv = cast(CallbackServer, self.server)
        srv.oauth_params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        body = (
            b"<html><body><p>Authorization received. You can close this tab "
            b"and return to the terminal.</p></body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def open_callback_server(port: int) -> CallbackServer:
    """Bind the localhost callback server. Call before opening the browser so
    the redirect can never race the server startup."""
    server = CallbackServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = 1.0
    server.oauth_params = None
    return server


def wait_for_code(server: CallbackServer, expected_state: str, timeout_seconds: float = 300.0) -> str:
    """Serve until Clio redirects back with an authorization code."""
    deadline = time.monotonic() + timeout_seconds
    try:
        while server.oauth_params is None:
            if time.monotonic() > deadline:
                raise TimeoutError("Timed out waiting for the OAuth callback.")
            server.handle_request()
    finally:
        server.server_close()
    params = server.oauth_params
    if "error" in params:
        raise RuntimeError(f"Authorization failed: {params['error']}")
    if params.get("state") != expected_state:
        raise RuntimeError("State mismatch in OAuth callback; aborting.")
    if "code" not in params:
        raise RuntimeError(f"OAuth callback had no authorization code: {params}")
    return params["code"]
