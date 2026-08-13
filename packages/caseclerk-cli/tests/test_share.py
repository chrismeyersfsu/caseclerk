from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from caseclerk_cli import share as share_module
from caseclerk_cli.main import app

_FAKE_PROCESS_PY = """
import time
try:
    while True:
        time.sleep(3600)
except KeyboardInterrupt:
    pass
"""


@pytest.fixture
def fake_spawn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace share._spawn with one that always launches a tiny long-running
    Python process via sys.executable, regardless of the (fake) binary path/args
    share.py thinks it's spawning. share.py's job under test is process lifecycle
    management (pidfile, is_alive, terminate), not what either child actually is --
    and a plain script isn't directly launchable by Windows' CreateProcess the way
    a POSIX shebang script is, so we always go through the real interpreter."""
    script = tmp_path / "fake_process.py"
    script.write_text(_FAKE_PROCESS_PY, encoding="utf-8")

    def _fake_spawn(_args: list[str]) -> int:
        process = subprocess.Popen(
            [sys.executable, str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return process.pid

    monkeypatch.setattr(share_module, "_spawn", _fake_spawn)


def test_caseclerk_binary_resolves_regardless_of_how_it_was_launched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # sys.executable is always an already-absolute path by the time Python
    # sees it (Windows resolves PATH lookups/shortcut targets before exec),
    # but _caseclerk_binary() still calls .resolve() defensively, matching
    # binary_update.install_dir()'s pattern -- compare against an equally
    # resolved tmp_path, not a hand-written string (see the Windows CI bug
    # this exact mistake caused for install_dir()'s own test).
    exe_path = tmp_path / "caseclerk.exe"
    monkeypatch.setattr(sys, "executable", str(exe_path))
    monkeypatch.setattr(sys, "platform", "win32")
    assert share_module._caseclerk_binary() == tmp_path.resolve() / "caseclerk.exe"


def test_share_start_requires_hostname_configured(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        share_module.cloudflared_module, "resolve", lambda **kwargs: Path("/usr/bin/cloudflared")
    )
    result = runner.invoke(app, ["share", "start"])
    assert result.exit_code == 1
    assert "share.hostname is not configured" in result.output


def test_share_start_reports_cloudflared_resolution_failure(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.invoke(app, ["config", "set", "share.hostname", "caseclerk.example.com"])

    def _fail_resolve(**kwargs: object) -> Path:
        raise share_module.cloudflared_module.CloudflaredError("offline, nothing cached")

    monkeypatch.setattr(share_module.cloudflared_module, "resolve", _fail_resolve)
    result = runner.invoke(app, ["share", "start"])
    assert result.exit_code == 1
    assert "Could not obtain cloudflared" in result.output


def test_share_setup_reports_the_resolved_binary(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real Path built from isolated_env, not a hand-written POSIX string --
    # str(Path("/usr/bin/x")) renders with backslashes on Windows, which would
    # never match a forward-slash substring check below.
    binary_path = isolated_env / "cloudflared"
    monkeypatch.setattr(share_module.cloudflared_module, "resolve", lambda **kwargs: binary_path)
    monkeypatch.setattr(
        share_module.cloudflared_module, "installed_version", lambda _path: "cloudflared 1.2.3"
    )
    monkeypatch.setattr(share_module.cloudflared_module, "source_label", lambda _path: "downloaded")

    result = runner.invoke(app, ["share", "setup"])
    assert result.exit_code == 0
    assert "downloaded" in result.output
    assert "cloudflared 1.2.3" in result.output
    assert str(binary_path) in result.output


def test_share_setup_reports_failure(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_resolve(**kwargs: object) -> Path:
        raise share_module.cloudflared_module.CloudflaredError("offline")

    monkeypatch.setattr(share_module.cloudflared_module, "resolve", _fail_resolve)
    result = runner.invoke(app, ["share", "setup"])
    assert result.exit_code == 1
    assert "Could not obtain cloudflared" in result.output


def _write_fake_credentials(tmp_path: Path, tunnel_id: str = "tunnel-abc") -> Path:
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps({"AccountTag": "a", "TunnelSecret": "s", "TunnelID": tunnel_id}), encoding="utf-8"
    )
    return path


def test_share_setup_credentials_requires_hostname(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        share_module.cloudflared_module, "resolve", lambda **kwargs: isolated_env / "cloudflared"
    )
    creds = _write_fake_credentials(isolated_env)

    result = runner.invoke(app, ["share", "setup", "--credentials", str(creds)])
    assert result.exit_code == 1
    assert "--credentials requires --hostname" in result.output


def test_share_setup_hostname_requires_credentials(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        share_module.cloudflared_module, "resolve", lambda **kwargs: isolated_env / "cloudflared"
    )
    result = runner.invoke(app, ["share", "setup", "--hostname", "files.example.com"])
    assert result.exit_code == 1
    assert "--hostname requires --credentials" in result.output


def test_share_setup_with_credentials_never_needs_cloudflared_login(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of --credentials: no `cloudflared tunnel login` call is
    ever made by `share setup` -- only resolve() (the managed binary download)
    and install_credentials() (a pure filesystem operation) run."""
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    monkeypatch.setattr(share_module.cloudflared_module, "resolve", lambda **kwargs: binary_path)
    monkeypatch.setattr(
        share_module.cloudflared_module, "installed_version", lambda _p: "cloudflared 2026.7.3"
    )
    monkeypatch.setattr(share_module.cloudflared_module, "source_label", lambda _p: "downloaded")

    def _fail_if_ever_spawned(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "share setup --credentials must never spawn a process (e.g. `cloudflared login`)"
        )

    monkeypatch.setattr(share_module, "_spawn", _fail_if_ever_spawned)
    monkeypatch.setattr(subprocess, "run", _fail_if_ever_spawned)
    monkeypatch.setattr(subprocess, "Popen", _fail_if_ever_spawned)

    creds = _write_fake_credentials(isolated_env)
    result = runner.invoke(
        app,
        [
            "share",
            "setup",
            "--credentials",
            str(creds),
            "--hostname",
            "files.example.com",
            "--tunnel-name",
            "custom-tunnel",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Installed tunnel tunnel-abc -> files.example.com" in result.output
    assert "share.hostname set to 'files.example.com'" in result.output
    assert "share.tunnelName set to 'custom-tunnel'" in result.output
    assert "Verifying setup:" in result.output
    assert "[FAIL]" not in result.output

    from caseclerk_core.config import load_config

    cfg = load_config()
    assert cfg.share.hostname == "files.example.com"
    assert cfg.share.tunnel_name == "custom-tunnel"

    config_path = share_module.cloudflared_module.config_path()
    assert config_path.is_file()
    assert "files.example.com" in config_path.read_text(encoding="utf-8")


def test_share_setup_credentials_defaults_tunnel_name_from_config(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    monkeypatch.setattr(share_module.cloudflared_module, "resolve", lambda **kwargs: binary_path)
    monkeypatch.setattr(share_module.cloudflared_module, "installed_version", lambda _p: "v")
    monkeypatch.setattr(share_module.cloudflared_module, "source_label", lambda _p: "downloaded")

    creds = _write_fake_credentials(isolated_env)
    result = runner.invoke(
        app, ["share", "setup", "--credentials", str(creds), "--hostname", "files.example.com"]
    )
    assert result.exit_code == 0, result.output
    assert "share.tunnelName set to 'caseclerk'" in result.output  # ShareConfig's default


def test_share_setup_credentials_reports_bad_credentials_file(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        share_module.cloudflared_module, "resolve", lambda **kwargs: isolated_env / "cloudflared"
    )
    bad = isolated_env / "not-credentials.json"
    bad.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app, ["share", "setup", "--credentials", str(bad), "--hostname", "files.example.com"]
    )
    assert result.exit_code == 1
    assert "Could not install the tunnel credentials" in result.output


def test_share_start_uses_config_yml_when_credentials_were_installed(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, fake_spawn: None
) -> None:
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    monkeypatch.setattr(share_module.cloudflared_module, "resolve", lambda **kwargs: binary_path)
    monkeypatch.setattr(share_module.cloudflared_module, "installed_version", lambda _p: "v")
    monkeypatch.setattr(share_module.cloudflared_module, "source_label", lambda _p: "downloaded")

    creds = _write_fake_credentials(isolated_env)
    setup_result = runner.invoke(
        app, ["share", "setup", "--credentials", str(creds), "--hostname", "files.example.com"]
    )
    assert setup_result.exit_code == 0, setup_result.output

    captured_args: list[list[str]] = []
    original_spawn = share_module._spawn

    def _capturing_spawn(args: list[str]) -> int:
        captured_args.append(args)
        return original_spawn(args)

    monkeypatch.setattr(share_module, "_spawn", _capturing_spawn)

    start_result = runner.invoke(app, ["share", "start"])
    assert start_result.exit_code == 0, start_result.output

    # the second _spawn call is the cloudflared invocation (the first is the
    # HTTP server); it must use --config, not the login-dependent --url form
    cloudflared_args = captured_args[1]
    assert "--config" in cloudflared_args
    assert str(share_module.cloudflared_module.config_path()) in cloudflared_args
    assert "--url" not in cloudflared_args

    runner.invoke(app, ["share", "stop"])


def test_share_shortcuts_non_windows_prints_message(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_module.sys, "platform", "linux")
    result = runner.invoke(app, ["share", "shortcuts"])
    assert result.exit_code == 0
    assert "only supported on Windows" in result.output


def test_share_shortcuts_windows_creates_and_reports(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_module.sys, "platform", "win32")
    created = [Path("C:/Users/x/Desktop/CaseClerk Sharing ON.lnk")]
    monkeypatch.setattr(share_module.shortcuts_module, "create_shortcuts", lambda **kwargs: created)

    result = runner.invoke(app, ["share", "shortcuts"])
    assert result.exit_code == 0
    assert "CaseClerk Sharing ON.lnk" in result.output


def test_share_status_when_not_running(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["share", "status"])
    assert result.exit_code == 0
    assert "not running" in result.output


def test_share_stop_when_not_running(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["share", "stop"])
    assert result.exit_code == 0
    assert "not running" in result.output


def test_share_start_stop_status_lifecycle(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, fake_spawn: None
) -> None:
    runner.invoke(app, ["config", "set", "share.hostname", "caseclerk.example.com"])
    monkeypatch.setattr(
        share_module.cloudflared_module, "resolve", lambda **kwargs: Path("/usr/bin/cloudflared")
    )

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


# --- Library entry points (share.start_sharing/stop_sharing/is_running/
# setup_credentials): these are what caseclerk-tray calls directly, with no
# typer/CliRunner involved -- the CLI-command-level tests above already cover
# the same underlying behavior end to end through `caseclerk share ...`, so
# these focus specifically on the plain-function return values a non-CLI
# caller (the tray) depends on.


def test_setup_credentials_direct_call_returns_structured_outcome(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    monkeypatch.setattr(share_module.cloudflared_module, "resolve", lambda **kwargs: binary_path)

    creds = _write_fake_credentials(isolated_env, tunnel_id="tunnel-direct")
    outcome = share_module.setup_credentials(creds, hostname="direct.example.com")

    assert outcome.ok is True
    assert outcome.tunnel_id == "tunnel-direct"
    assert outcome.hostname == "direct.example.com"
    assert outcome.tunnel_name == "caseclerk"  # ShareConfig's default, since none was passed
    assert outcome.public_url == "https://direct.example.com/mcp"
    assert outcome.binary_path == binary_path
    assert outcome.credentials_path is not None and outcome.credentials_path.is_file()
    assert outcome.config_path is not None and outcome.config_path.is_file()

    from caseclerk_core.config import load_config

    assert load_config().share.hostname == "direct.example.com"


def test_setup_credentials_direct_call_empty_hostname_never_touches_cloudflared(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(**kwargs: object) -> Path:
        raise AssertionError("resolve() must not run before the hostname is even validated")

    monkeypatch.setattr(share_module.cloudflared_module, "resolve", _fail_if_called)
    creds = _write_fake_credentials(isolated_env)

    outcome = share_module.setup_credentials(creds, hostname="   ")
    assert outcome.ok is False
    assert "hostname" in outcome.message.lower()


def test_setup_credentials_direct_call_accepts_a_pre_resolved_binary(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI passes an already-resolved `binary=` (it needs cloudflared.resolve()
    for its own "cloudflared ready" report first); the tray's Settings window
    doesn't, so setup_credentials must resolve one itself when `binary` is
    omitted -- both call paths are exercised here."""
    binary_path = isolated_env / "cloudflared"
    binary_path.write_text("fake binary")
    resolve_calls: list[None] = []

    def _fail_if_called(**kwargs: object) -> Path:
        resolve_calls.append(None)
        raise AssertionError("resolve() must not be called when a pre-resolved binary= is supplied")

    monkeypatch.setattr(share_module.cloudflared_module, "resolve", _fail_if_called)

    creds = _write_fake_credentials(isolated_env)
    outcome = share_module.setup_credentials(creds, hostname="pre-resolved.example.com", binary=binary_path)

    assert outcome.ok is True
    assert resolve_calls == []  # resolve() was never called -- the pre-resolved binary was used as-is


def test_library_start_stop_is_running_without_a_cli_runner(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch, fake_spawn: None
) -> None:
    """caseclerk-tray calls these three functions directly (no typer, no
    CliRunner) -- this exercises exactly that call shape."""
    from caseclerk_core.config import load_config, save_config

    cfg = load_config().model_copy(update={"share": load_config().share.model_copy(update={"hostname": "x"})})
    save_config(cfg)
    monkeypatch.setattr(
        share_module.cloudflared_module, "resolve", lambda **kwargs: Path("/usr/bin/cloudflared")
    )

    assert share_module.is_running() is False

    start_outcome = share_module.start_sharing()
    assert start_outcome.ok is True
    assert share_module.is_running() is True

    stop_outcome = share_module.stop_sharing()
    assert stop_outcome.ok is True
    assert share_module.is_running() is False


def test_share_status_shows_recent_audit_entries(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, fake_spawn: None
) -> None:
    from caseclerk_core import db

    runner.invoke(app, ["config", "set", "share.hostname", "caseclerk.example.com"])
    monkeypatch.setattr(
        share_module.cloudflared_module, "resolve", lambda **kwargs: Path("/usr/bin/cloudflared")
    )

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
