from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from caseclerk_cli import share as share_module
from caseclerk_cli.main import app

_FAKE_CLOUDFLARED_SCRIPT = """#!/usr/bin/env python3
import time
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
"""


@pytest.fixture
def fake_process_script(tmp_path: Path) -> Path:
    """A tiny long-running executable standing in for both cloudflared and the
    caseclerk HTTP server subprocess -- share.py's job is process lifecycle
    management (pidfile, is_alive, terminate), not what either child actually does."""
    script = tmp_path / "fake_process"
    script.write_text(_FAKE_CLOUDFLARED_SCRIPT, encoding="utf-8")
    mode = script.stat().st_mode
    script.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def test_share_start_requires_hostname_configured(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, fake_process_script: Path
) -> None:
    monkeypatch.setattr(share_module.shutil, "which", lambda _name: str(fake_process_script))
    result = runner.invoke(app, ["share", "start"])
    assert result.exit_code == 1
    assert "share.hostname is not configured" in result.output


def test_share_start_requires_cloudflared_on_path(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.invoke(app, ["config", "set", "share.hostname", "caseclerk.example.com"])
    monkeypatch.setattr(share_module.shutil, "which", lambda _name: None)
    result = runner.invoke(app, ["share", "start"])
    assert result.exit_code == 1
    assert "cloudflared is not on PATH" in result.output


def test_share_status_when_not_running(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["share", "status"])
    assert result.exit_code == 0
    assert "not running" in result.output


def test_share_stop_when_not_running(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["share", "stop"])
    assert result.exit_code == 0
    assert "not running" in result.output


def test_share_start_stop_status_lifecycle(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, fake_process_script: Path
) -> None:
    runner.invoke(app, ["config", "set", "share.hostname", "caseclerk.example.com"])
    monkeypatch.setattr(share_module.shutil, "which", lambda _name: str(fake_process_script))
    monkeypatch.setattr(share_module, "_caseclerk_binary", lambda: fake_process_script)

    start_result = runner.invoke(app, ["share", "start"])
    assert start_result.exit_code == 0, start_result.output
    assert "Started. Public URL: https://caseclerk.example.com/mcp" in start_result.output

    state = json.loads(share_module._pidfile_path().read_text(encoding="utf-8"))
    assert share_module._is_alive(state["server_pid"])
    assert share_module._is_alive(state["cloudflared_pid"])

    status_result = runner.invoke(app, ["share", "status"])
    assert status_result.exit_code == 0
    assert "running" in status_result.output
    assert "https://caseclerk.example.com/mcp" in status_result.output
    assert "No audit entries yet." in status_result.output

    # starting again while already running must fail cleanly, not spawn a second pair
    second_start = runner.invoke(app, ["share", "start"])
    assert second_start.exit_code == 1
    assert "already running" in second_start.output

    stop_result = runner.invoke(app, ["share", "stop"])
    assert stop_result.exit_code == 0
    assert "Stopped." in stop_result.output
    assert not share_module._is_alive(state["server_pid"])
    assert not share_module._is_alive(state["cloudflared_pid"])
    assert not share_module._pidfile_path().is_file()

    stop_again = runner.invoke(app, ["share", "stop"])
    assert stop_again.exit_code == 0
    assert "not running" in stop_again.output


def test_share_status_shows_recent_audit_entries(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, fake_process_script: Path
) -> None:
    from caseclerk_core import db

    runner.invoke(app, ["config", "set", "share.hostname", "caseclerk.example.com"])
    monkeypatch.setattr(share_module.shutil, "which", lambda _name: str(fake_process_script))
    monkeypatch.setattr(share_module, "_caseclerk_binary", lambda: fake_process_script)

    runner.invoke(app, ["share", "start"])

    conn = db.connect()
    try:
        db.insert_remote_request(conn, tool="list_clients", args_summary="{}", ok=True, error=None)
    finally:
        conn.close()

    status_result = runner.invoke(app, ["share", "status"])
    assert "list_clients" in status_result.output
    assert "ok" in status_result.output

    runner.invoke(app, ["share", "stop"])
