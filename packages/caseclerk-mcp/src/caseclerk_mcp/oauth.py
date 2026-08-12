"""A minimal, single-user OAuth 2.1 authorization server for the HTTP transport.

Implements `OAuthAuthorizationServerProvider`, backed by the existing SQLite db
(caseclerk_core.db's oauth_* tables). PKCE verification happens inside the mcp
SDK itself (mcp.server.auth.handlers.token hashes code_verifier and compares it
to the stored code_challenge); this provider only needs to store/retrieve that
challenge alongside each authorization code.

There is exactly one real user: the attorney at the keyboard on the far end of
the tunnel. `/authorize` auto-approves every request instead of showing a login
or consent screen -- the security boundary here is that the tunnel is off by
default and brought up deliberately (`caseclerk share start`), not a login
screen in front of a server nobody can reach unless that tunnel is already up.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from caseclerk_core import db

OAUTH_SCOPE = "caseclerk"
ACCESS_TOKEN_TTL_SECONDS = 60 * 60  # 1 hour
REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days
AUTH_CODE_TTL_SECONDS = 300  # 5 minutes to complete the redirect round-trip


def _redirect_with(base: str, **params: str) -> str:
    parsed = urlparse(base)
    query = parse_qs(parsed.query)
    for key, value in params.items():
        query[key] = [value]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


class CaseClerkOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return db.connect(self._db_path)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        conn = self._conn()
        try:
            data = db.get_oauth_client(conn, client_id)
        finally:
            conn.close()
        return OAuthClientInformationFull.model_validate_json(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        conn = self._conn()
        try:
            db.upsert_oauth_client(conn, client_info.client_id, client_info.model_dump_json())
        finally:
            conn.close()

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(32)
        # single-scope server: honor an explicit request, but a client that asks for
        # nothing (the common case -- ChatGPT's DCR flow doesn't request one) still gets
        # OAUTH_SCOPE, since that's the only scope this server has and requires.
        auth_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [OAUTH_SCOPE],
            expires_at=time.time() + AUTH_CODE_TTL_SECONDS,
            client_id=client.client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        conn = self._conn()
        try:
            db.insert_oauth_auth_code(
                conn, code, client.client_id, auth_code.model_dump_json(), expires_at=auth_code.expires_at
            )
        finally:
            conn.close()

        redirect_params = {"code": code}
        if params.state is not None:
            redirect_params["state"] = params.state
        return _redirect_with(str(params.redirect_uri), **redirect_params)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        conn = self._conn()
        try:
            data = db.get_oauth_auth_code(conn, authorization_code)
        finally:
            conn.close()
        if not data:
            return None
        code = AuthorizationCode.model_validate_json(data)
        if code.client_id != client.client_id or code.expires_at < time.time():
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        conn = self._conn()
        try:
            db.delete_oauth_auth_code(conn, authorization_code.code)  # single use
            return self._issue_token(
                conn, client.client_id, authorization_code.scopes, authorization_code.resource
            )
        finally:
            conn.close()

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        conn = self._conn()
        try:
            data = db.get_oauth_refresh_token(conn, refresh_token)
        finally:
            conn.close()
        if not data:
            return None
        token = RefreshToken.model_validate_json(data)
        if token.client_id != client.client_id:
            return None
        if token.expires_at is not None and token.expires_at < time.time():
            return None
        return token

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        conn = self._conn()
        try:
            db.delete_oauth_refresh_token(conn, refresh_token.token)  # rotate on use
            granted_scopes = scopes or refresh_token.scopes
            return self._issue_token(conn, client.client_id, granted_scopes, None)
        finally:
            conn.close()

    async def load_access_token(self, token: str) -> AccessToken | None:
        conn = self._conn()
        try:
            data = db.get_oauth_access_token(conn, token)
        finally:
            conn.close()
        if not data:
            return None
        access_token = AccessToken.model_validate_json(data)
        if access_token.expires_at is not None and access_token.expires_at < time.time():
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        conn = self._conn()
        try:
            if isinstance(token, AccessToken):
                db.delete_oauth_access_token(conn, token.token)
            else:
                db.delete_oauth_refresh_token(conn, token.token)
        finally:
            conn.close()

    def _issue_token(
        self, conn: sqlite3.Connection, client_id: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        access_token_str = secrets.token_urlsafe(32)
        refresh_token_str = secrets.token_urlsafe(32)
        now = int(time.time())

        access_token = AccessToken(
            token=access_token_str,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL_SECONDS,
            resource=resource,
        )
        refresh_token = RefreshToken(
            token=refresh_token_str,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL_SECONDS,
        )
        db.insert_oauth_access_token(
            conn,
            access_token_str,
            client_id,
            access_token.model_dump_json(),
            expires_at=access_token.expires_at,
        )
        db.insert_oauth_refresh_token(
            conn,
            refresh_token_str,
            client_id,
            refresh_token.model_dump_json(),
            expires_at=refresh_token.expires_at,
        )
        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL_SECONDS,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh_token_str,
        )
