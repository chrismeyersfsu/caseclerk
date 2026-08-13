from __future__ import annotations

import json
from typing import Any, cast

import httpx
import pytest

from caseclerk_clio_export.api import ClioApiError, ClioClient


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self.responses.pop(0)


def make_client(
    responses: list[FakeResponse],
    refresh: Any = None,
    sleeps: list[float] | None = None,
) -> tuple[ClioClient, FakeSession]:
    session = FakeSession(responses)
    client = ClioClient(
        "https://app.clio.com",
        "tok-1",
        refresh=refresh,
        session=cast(httpx.Client, session),
        sleep=(sleeps.append if sleeps is not None else lambda s: None),
    )
    return client, session


def test_paginate_follows_next_and_aggregates() -> None:
    page1 = {
        "data": [{"id": 1}, {"id": 2}],
        "meta": {"paging": {"next": "https://app.clio.com/api/v4/contacts.json?page_token=abc"}},
    }
    page2: dict[str, Any] = {"data": [{"id": 3}], "meta": {}}
    client, session = make_client([FakeResponse(payload=page1), FakeResponse(payload=page2)])

    records = list(client.paginate("contacts.json", {"limit": 200}))

    assert [r["id"] for r in records] == [1, 2, 3]
    assert session.calls[0]["url"] == "https://app.clio.com/api/v4/contacts.json"
    assert session.calls[0]["params"] == {"limit": 200}
    # The follow-up request uses the next URL verbatim, with no extra params.
    assert session.calls[1]["url"] == page1["meta"]["paging"]["next"]  # type: ignore[index]
    assert session.calls[1]["params"] is None


def test_refreshes_once_on_401() -> None:
    client, session = make_client(
        [FakeResponse(status_code=401), FakeResponse(payload={"data": []})],
        refresh=lambda: "tok-2",
    )

    assert list(client.paginate("matters.json", {})) == []
    assert session.calls[0]["headers"]["Authorization"] == "Bearer tok-1"
    assert session.calls[1]["headers"]["Authorization"] == "Bearer tok-2"


def test_401_without_refresh_raises() -> None:
    client, _ = make_client([FakeResponse(status_code=401)])
    with pytest.raises(ClioApiError) as exc:
        list(client.paginate("matters.json", {}))
    assert exc.value.status_code == 401


def test_429_sleeps_retry_after_then_retries() -> None:
    sleeps: list[float] = []
    client, session = make_client(
        [
            FakeResponse(status_code=429, headers={"Retry-After": "7"}),
            FakeResponse(payload={"data": [{"id": 1}]}),
        ],
        sleeps=sleeps,
    )

    assert len(list(client.paginate("contacts.json", {}))) == 1
    assert sleeps == [7.0]
    assert len(session.calls) == 2


def test_server_error_retries_then_succeeds() -> None:
    sleeps: list[float] = []
    client, _ = make_client(
        [FakeResponse(status_code=502), FakeResponse(payload={"data": []})],
        sleeps=sleeps,
    )
    assert list(client.paginate("contacts.json", {})) == []
    assert sleeps == [5.0]


def test_client_error_raises() -> None:
    client, _ = make_client([FakeResponse(status_code=400, payload={"error": "bad fields"})])
    with pytest.raises(ClioApiError) as exc:
        list(client.paginate("contacts.json", {}))
    assert exc.value.status_code == 400
    assert "bad fields" in str(exc.value)
