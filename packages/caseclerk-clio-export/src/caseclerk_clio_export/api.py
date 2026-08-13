"""Minimal Clio Manage API v4 client: pagination, token refresh, rate limits."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from . import __version__

REQUEST_TIMEOUT = 60.0
MAX_RATE_LIMIT_RETRIES = 5
MAX_SERVER_ERROR_RETRIES = 3


class ClioApiError(RuntimeError):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"Clio API error {status_code}: {message}")
        self.status_code = status_code


class ClioClient:
    def __init__(
        self,
        base_url: str,
        access_token: str,
        refresh: Callable[[], str] | None = None,
        session: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self._refresh = refresh
        self.session = session or httpx.Client()
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": f"clio-export/{__version__}",
            "Accept": "application/json",
        }

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        refreshed = False
        rate_limited = 0
        server_errors = 0
        while True:
            resp = self.session.get(url, params=params, headers=self._headers(), timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                payload: dict[str, Any] = resp.json()
                return payload
            if resp.status_code == 401 and self._refresh and not refreshed:
                self.access_token = self._refresh()
                refreshed = True
                continue
            if resp.status_code == 429 and rate_limited < MAX_RATE_LIMIT_RETRIES:
                rate_limited += 1
                self._sleep(float(resp.headers.get("Retry-After", "60")))
                continue
            if resp.status_code >= 500 and server_errors < MAX_SERVER_ERROR_RETRIES:
                server_errors += 1
                self._sleep(5.0 * server_errors)
                continue
            raise ClioApiError(resp.status_code, resp.text[:1000])

    def paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Yield every record from a list endpoint, following cursor pagination."""
        page = self._get(f"{self.base_url}/api/v4/{path}", params=params)
        while True:
            yield from page.get("data", [])
            next_url = ((page.get("meta") or {}).get("paging") or {}).get("next")
            if not next_url:
                return
            # The next URL already carries fields/limit/page_token.
            page = self._get(next_url)
