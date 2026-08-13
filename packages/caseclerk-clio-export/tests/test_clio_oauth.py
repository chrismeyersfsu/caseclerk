from __future__ import annotations

import urllib.parse
from typing import Any

import pytest

from caseclerk_clio_export import oauth


class FakePostResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.status_code = 200
        self._payload = payload
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload


def test_authorize_url_contains_required_params() -> None:
    url = oauth.authorize_url("https://app.clio.com", "my-id", "http://127.0.0.1:8788/callback", "st4te")
    parsed = urllib.parse.urlparse(url)
    params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
    assert url.startswith("https://app.clio.com/oauth/authorize?")
    assert params == {
        "response_type": "code",
        "client_id": "my-id",
        "redirect_uri": "http://127.0.0.1:8788/callback",
        "state": "st4te",
    }


def test_exchange_code_computes_expires_at(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_post(
        url: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakePostResponse:
        captured["url"] = url
        captured["data"] = data
        return FakePostResponse({"access_token": "at", "refresh_token": "rt", "expires_in": 2592000})

    monkeypatch.setattr(oauth.httpx, "post", fake_post)
    token = oauth.exchange_code(
        "https://app.clio.com", "id", "secret", "c0de", "http://127.0.0.1:8788/callback"
    )
    assert captured["url"] == "https://app.clio.com/oauth/token"
    assert captured["data"]["grant_type"] == "authorization_code"
    assert token["access_token"] == "at"
    assert token["expires_at"] > 0


def test_refresh_keeps_old_refresh_token_when_not_rotated(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str,
        data: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakePostResponse:
        assert data is not None
        assert data["grant_type"] == "refresh_token"
        assert data["refresh_token"] == "old-rt"
        return FakePostResponse({"access_token": "new-at", "expires_in": 60})

    monkeypatch.setattr(oauth.httpx, "post", fake_post)
    token = oauth.refresh_access_token(
        "https://app.clio.com",
        "id",
        "secret",
        {"access_token": "old-at", "refresh_token": "old-rt"},
    )
    assert token["access_token"] == "new-at"
    assert token["refresh_token"] == "old-rt"
