import sqlite3
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from caseclerk_core import binary_update, db, update


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def _client(handler: httpx.MockTransport | object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def test_parse_semver() -> None:
    version = update.parse_semver("v1.2.3")
    assert (version.major, version.minor, version.patch) == (1, 2, 3)
    assert version.prerelease is None


def test_parse_semver_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="not a semver"):
        update.parse_semver("not-a-version")


def test_is_newer() -> None:
    assert update.is_newer("v1.3.0", "1.2.9")
    assert not update.is_newer("v1.2.0", "1.2.0")
    assert not update.is_newer("v1.1.9", "1.2.0")


def test_prerelease_ranks_below_release() -> None:
    assert update.is_newer("v1.2.0", "1.2.0-beta.1")
    assert not update.is_newer("v1.2.0-beta.1", "1.2.0")


def test_fetch_latest_release_tag_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tag_name": "v9.9.9"})

    with _client(handler) as client:
        assert update.fetch_latest_release_tag(client=client) == "v9.9.9"


def test_fetch_latest_release_tag_tolerates_network_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with _client(handler) as client:
        assert update.fetch_latest_release_tag(client=client) is None


def test_fetch_latest_release_tag_tolerates_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "rate limited"})

    with _client(handler) as client:
        assert update.fetch_latest_release_tag(client=client) is None


def test_check_for_update_detects_and_caches(conn: sqlite3.Connection) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"tag_name": "v2.0.0"})

    with _client(handler) as client:
        first = update.check_for_update(conn, current="1.0.0", client=client)
        second = update.check_for_update(conn, current="1.0.0", client=client)

    assert first == "v2.0.0"
    assert second == "v2.0.0"
    assert len(calls) == 1  # second call served from the meta-table cache


def test_check_for_update_returns_none_when_already_current(conn: sqlite3.Connection) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tag_name": "v1.0.0"})

    with _client(handler) as client:
        assert update.check_for_update(conn, current="1.0.0", client=client) is None


def test_check_for_update_rechecks_after_interval_elapses(conn: sqlite3.Connection) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"tag_name": "v2.0.0"})

    ticks = iter([datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)])

    with _client(handler) as client:
        update.check_for_update(
            conn, current="1.0.0", client=client, check_interval_hours=1, now=lambda: next(ticks)
        )
        update.check_for_update(
            conn, current="1.0.0", client=client, check_interval_hours=1, now=lambda: next(ticks)
        )

    assert len(calls) == 2


def test_check_for_update_ignores_non_semver_tag(conn: sqlite3.Connection) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"tag_name": "not-a-version"})

    with _client(handler) as client:
        assert update.check_for_update(conn, current="1.0.0", client=client) is None


def test_apply_update_spawns_uv_tool_install_via_injected_spawn() -> None:
    # `uv tool install` has no `--from`: the source must be the single PACKAGE argument,
    # here a PEP 508 direct reference with a #subdirectory= fragment (the repo's root has
    # no [project] -- caseclerk-cli lives under packages/caseclerk-cli).
    captured: dict[str, list[str]] = {}

    def fake_spawn(args: list[str]) -> object:
        captured["args"] = args
        return None

    update.apply_update("v1.2.3", spawn=fake_spawn)

    assert captured["args"] == [
        "uv",
        "tool",
        "install",
        "--force",
        "caseclerk-cli @ git+https://github.com/chrismeyersfsu/caseclerk@v1.2.3"
        "#subdirectory=packages/caseclerk-cli",
    ]


def test_apply_update_never_spawns_uv_when_frozen_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    # Platform-independent: this test only cares that spawn (uv) is never called
    # when frozen=True, regardless of what OS it happens to run on in CI.
    monkeypatch.setattr(binary_update, "release_asset_name", lambda: None)

    def _fail_spawn(args: list[str]) -> object:
        raise AssertionError("must not run uv tool install for a frozen (binary) install")

    result = update.apply_update("v1.2.3", spawn=_fail_spawn, frozen=True)

    assert isinstance(result, binary_update.BinaryUpdateResult)


def test_apply_update_uses_real_sys_frozen_when_not_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    # No platform has a published asset in this sandbox's actual sys.platform,
    # so apply_binary_update short-circuits before ever touching the network --
    # this test proves the frozen->binary_update routing, not the download path
    # (that's covered, fully network-mocked, in test_binary_update.py).
    monkeypatch.setattr(binary_update, "release_asset_name", lambda: None)

    def _fail_spawn(args: list[str]) -> object:
        raise AssertionError("must not run uv tool install when sys.frozen is set")

    result = update.apply_update("v1.2.3", spawn=_fail_spawn)

    assert isinstance(result, binary_update.BinaryUpdateResult)
    assert result.ok is False
