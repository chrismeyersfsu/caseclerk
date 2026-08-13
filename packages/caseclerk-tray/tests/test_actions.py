from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from caseclerk_cli import cloudflared as cloudflared_module
from caseclerk_cli import share as share_module
from caseclerk_core import binary_update
from caseclerk_core.config import load_config
from caseclerk_tray import actions


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Local fixture rather than a shared tests/conftest.py -- see the longer
    # explanation in test_state.py's copy of this fixture (a same-named
    # conftest.py collides across packages under a whole-tree mypy sweep).
    monkeypatch.setenv("CASECLERK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CASECLERK_DATA_DIR", str(tmp_path / "data"))
    for key in list(os.environ):
        if key.startswith("CASECLERK_") and key not in ("CASECLERK_CONFIG_DIR", "CASECLERK_DATA_DIR"):
            monkeypatch.delenv(key, raising=False)
    yield tmp_path


def test_start_sharing_delegates_to_share_module(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = share_module.StartOutcome(
        True, "Started. Public URL: https://x/mcp", public_url="https://x/mcp"
    )
    monkeypatch.setattr(share_module, "start_sharing", lambda **kwargs: outcome)
    result = actions.start_sharing()
    assert result is outcome


def test_stop_sharing_delegates_to_share_module(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outcome = share_module.StopOutcome(True, "Stopped.")
    monkeypatch.setattr(share_module, "stop_sharing", lambda: outcome)
    result = actions.stop_sharing()
    assert result is outcome


def test_set_autostart_returns_false_when_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert actions.set_autostart(True) is False
    assert actions.set_autostart(False) is False


def test_check_for_update_delegates_with_configured_interval(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from caseclerk_core import db
    from caseclerk_core import update as core_update

    monkeypatch.setattr(core_update, "fetch_latest_release_tag", lambda client=None: "v9.9.9")
    monkeypatch.setattr(core_update, "current_version", lambda distribution=None: "0.0.1")

    cfg = load_config()
    conn = db.connect()
    try:
        available = actions.check_for_update(conn, cfg)
    finally:
        conn.close()
    assert available == "v9.9.9"


def test_apply_staged_update_noop_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_update, "is_frozen", lambda: False)
    assert actions.apply_staged_update("v1.0.0") is None


def test_apply_staged_update_delegates_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_update, "is_frozen", lambda: True)
    result = binary_update.BinaryUpdateResult(ok=True, detail="Updated to v1.0.0.")
    monkeypatch.setattr(binary_update, "apply_binary_update", lambda tag, **kwargs: result)
    assert actions.apply_staged_update("v1.0.0") is result


def test_relaunch_spawns_a_detached_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(binary_update, "is_frozen", lambda: False)
    captured: dict[str, list[str]] = {}

    class _FakeProcess:
        pid = 4321

    def _fake_popen(args: list[str], **kwargs: object) -> _FakeProcess:
        captured["args"] = args
        return _FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    actions.relaunch()
    assert captured["args"][0] == sys.executable
    assert captured["args"][1:] == ["-m", "caseclerk_tray"]


def test_relaunch_frozen_uses_the_exe_next_to_the_running_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(binary_update, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "caseclerk-tray.exe"))
    captured: dict[str, list[str]] = {}

    class _FakeProcess:
        pid = 4321

    def _fake_popen(args: list[str], **kwargs: object) -> _FakeProcess:
        captured["args"] = args
        return _FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    actions.relaunch()
    assert captured["args"] == [str(tmp_path.resolve() / "caseclerk-tray.exe")]


# --- setup_sharing: the tray-facing wrapper around share.setup_credentials --
# (the CLI's own `share setup --credentials/--hostname` behavior is already
# covered end to end in caseclerk-cli/tests/test_share.py -- these exercise
# the tray's wrapper specifically, not the underlying flow again.)


def _write_fake_credentials(tmp_path: Path, tunnel_id: str = "tunnel-xyz") -> Path:
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({"AccountTag": "a", "TunnelSecret": "s", "TunnelID": tunnel_id}), encoding="utf-8"
    )
    return path


def test_setup_sharing_success(isolated_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    monkeypatch.setattr(cloudflared_module, "resolve", lambda **kwargs: binary_path)

    creds = _write_fake_credentials(isolated_env)
    outcome = actions.setup_sharing(creds, hostname="files.example.com")

    assert outcome.ok is True
    assert outcome.hostname == "files.example.com"
    assert outcome.public_url == "https://files.example.com/mcp"
    assert load_config().share.hostname == "files.example.com"


def test_setup_sharing_reports_missing_credentials_file_without_crashing(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    monkeypatch.setattr(cloudflared_module, "resolve", lambda **kwargs: binary_path)

    outcome = actions.setup_sharing(isolated_env / "missing.json", hostname="files.example.com")

    assert outcome.ok is False
    assert "credentials" in outcome.message.lower()


def test_setup_sharing_reports_bad_json_without_crashing(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    monkeypatch.setattr(cloudflared_module, "resolve", lambda **kwargs: binary_path)

    bad = isolated_env / "not-credentials.json"
    bad.write_text("{}", encoding="utf-8")

    outcome = actions.setup_sharing(bad, hostname="files.example.com")

    assert outcome.ok is False


def test_setup_sharing_reports_empty_hostname_without_crashing(isolated_env: Path) -> None:
    creds = _write_fake_credentials(isolated_env)
    outcome = actions.setup_sharing(creds, hostname="   ")
    assert outcome.ok is False
    assert "hostname" in outcome.message.lower()
