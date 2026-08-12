"""`caseclerk share start|stop|status`: the HTTP transport + a cloudflared named
tunnel as two detached child processes, tracked by a pidfile in the data dir.

Both processes are started the same way the auto-updater spawns things: detached,
stdio silenced, PID recorded so a later `stop` (a separate process invocation) can
find and terminate them. Neither child is a Python object we keep a handle to --
`share start` and `share stop` are two different CLI invocations.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import typer

from caseclerk_cli import cloudflared as cloudflared_module
from caseclerk_cli import shortcuts as shortcuts_module
from caseclerk_core import db
from caseclerk_core.config import data_dir, load_config

app = typer.Typer(help="Manage remote access for ChatGPT: the HTTP transport + a cloudflared tunnel.")

PIDFILE_NAME = "share.json"
STOP_TIMEOUT_SECONDS = 5.0
MCP_PATH = "/mcp"


def _pidfile_path() -> Path:
    return data_dir() / PIDFILE_NAME


def _caseclerk_binary() -> Path:
    venv_bin = Path(sys.executable).parent
    name = "caseclerk.exe" if sys.platform == "win32" else "caseclerk"
    return venv_bin / name


def _read_state() -> dict[str, object] | None:
    path = _pidfile_path()
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_state(state: dict[str, object]) -> None:
    path = _pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_state() -> None:
    path = _pidfile_path()
    with contextlib.suppress(OSError):
        path.unlink()


def _is_alive(pid: object) -> bool:
    if not isinstance(pid, int):
        return False
    if sys.platform == "win32":
        return _is_alive_windows(pid)
    # Reap first if it's our (zombie, already-signaled) child -- a no-op, harmlessly
    # suppressed, when it isn't: real usage runs `start` and `stop` as separate CLI
    # invocations, so by the time `stop` runs the child has long since been reparented
    # to init, which reaps it. Tests invoke both in one process, where we ARE still the
    # parent; without reaping, a dead-but-unwaited child keeps answering kill(pid, 0).
    # os.WNOHANG doesn't exist in Windows' typeshed stub at all (not just at runtime),
    # so this is a getattr, not a plain attribute access -- and skipped entirely there
    # (this whole branch is POSIX-only anyway; see _is_alive_windows for win32).
    wnohang = getattr(os, "WNOHANG", None)
    if wnohang is not None:
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, wnohang)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _is_alive_windows(pid: int) -> bool:
    """`os.kill(pid, 0)` is not a safe existence probe on Windows: signal 0 is
    literally CTRL_C_EVENT there, so it delivers a real Ctrl+C to the target
    instead of merely checking it exists. `tasklist` is a stock Windows tool
    that answers the question without touching the process at all."""
    try:
        result = subprocess.run(
            ["tasklist", "/fi", f"PID eq {pid}", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return str(pid) in result.stdout


def _spawn(args: list[str]) -> int:
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return process.pid


def _terminate(pid: object) -> bool:
    """Best-effort SIGTERM then SIGKILL. Returns True once the process is confirmed dead."""
    if not _is_alive(pid):
        return True
    assert isinstance(pid, int)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return True
        time.sleep(0.2)

    sigkill = getattr(signal, "SIGKILL", None)  # POSIX only; Windows SIGTERM already force-kills
    if sigkill is not None:
        with contextlib.suppress(OSError):
            os.kill(pid, sigkill)
        time.sleep(0.2)
    return not _is_alive(pid)


@app.command("setup")
def share_setup() -> None:
    """Ensure a working cloudflared binary is available (downloading it if
    needed) without starting anything -- useful during one-time setup, before
    share.hostname is even configured, so the download happens up front rather
    than as a surprise the first time `share start` runs."""
    try:
        binary = cloudflared_module.resolve(progress=lambda msg: typer.echo(msg))
    except cloudflared_module.CloudflaredError as exc:
        typer.echo(f"Could not obtain cloudflared: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    version = cloudflared_module.installed_version(binary) or "unknown version"
    source = cloudflared_module.source_label(binary)
    typer.echo(f"cloudflared ready ({source}, {version}): {binary}")


@app.command("start")
def share_start() -> None:
    """Start the HTTP transport and the cloudflared tunnel, both detached."""
    state = _read_state()
    if state and (_is_alive(state.get("server_pid")) or _is_alive(state.get("cloudflared_pid"))):
        typer.echo("share is already running -- see `caseclerk share status`.", err=True)
        raise typer.Exit(code=1)

    cfg = load_config()
    if not cfg.share.hostname:
        typer.echo(
            "share.hostname is not configured. Complete the one-time cloudflared setup "
            "(see the README's Remote access section), then run "
            "`caseclerk config set share.hostname <your-hostname>`.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        cloudflared_bin = cloudflared_module.resolve(progress=lambda msg: typer.echo(msg))
    except cloudflared_module.CloudflaredError as exc:
        typer.echo(f"Could not obtain cloudflared: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    caseclerk_bin = _caseclerk_binary()
    server_pid = _spawn([str(caseclerk_bin), "serve", "--transport", "http", "--port", str(cfg.share.port)])
    cloudflared_pid = _spawn(
        [
            str(cloudflared_bin),
            "tunnel",
            "run",
            "--url",
            f"http://127.0.0.1:{cfg.share.port}",
            cfg.share.tunnel_name,
        ]
    )

    _write_state(
        {
            "server_pid": server_pid,
            "cloudflared_pid": cloudflared_pid,
            "port": cfg.share.port,
            "hostname": cfg.share.hostname,
            "tunnel_name": cfg.share.tunnel_name,
            "started_at": datetime.now(UTC).isoformat(),
        }
    )

    public_url = f"https://{cfg.share.hostname}{MCP_PATH}"
    typer.echo(f"Started. Public URL: {public_url}")
    typer.echo(
        "In ChatGPT: Settings -> Apps & Connectors -> Advanced settings -> Developer mode, "
        f"then create a connector pointed at {public_url} with OAuth authentication."
    )
    typer.echo("Run `caseclerk share stop` when you're done.")


@app.command("stop")
def share_stop() -> None:
    """Stop the HTTP transport and the tunnel, verifying both processes are dead."""
    state = _read_state()
    if state is None:
        typer.echo("share is not running.")
        return

    server_dead = _terminate(state.get("server_pid"))
    cloudflared_dead = _terminate(state.get("cloudflared_pid"))
    _clear_state()

    if server_dead and cloudflared_dead:
        typer.echo("Stopped.")
    else:
        typer.echo("Stopped, but one or more processes may not have exited cleanly.", err=True)
        raise typer.Exit(code=1)


@app.command("status")
def share_status() -> None:
    """Show whether share is running, its public URL, and recent audit entries."""
    state = _read_state()
    server_alive = bool(state) and _is_alive(state.get("server_pid")) if state else False
    tunnel_alive = bool(state) and _is_alive(state.get("cloudflared_pid")) if state else False

    if not state or not (server_alive or tunnel_alive):
        typer.echo("share is not running.")
        return

    typer.echo(
        f"HTTP server:  {'running' if server_alive else 'NOT running'} (pid {state.get('server_pid')})"
    )
    typer.echo(
        f"cloudflared:  {'running' if tunnel_alive else 'NOT running'} (pid {state.get('cloudflared_pid')})"
    )
    hostname = state.get("hostname")
    if hostname:
        typer.echo(f"Public URL:   https://{hostname}{MCP_PATH}")
    typer.echo(f"Started at:   {state.get('started_at')}")

    conn = db.connect()
    try:
        entries = db.list_remote_requests(conn, limit=10)
    finally:
        conn.close()

    if not entries:
        typer.echo("\nNo audit entries yet.")
        return
    typer.echo("\nRecent audit entries:")
    for entry in entries:
        label = "ok" if entry.ok else "FAIL"
        line = f"  [{entry.ts.isoformat()}] {entry.tool} - {label}"
        if entry.error:
            line += f": {entry.error}"
        typer.echo(line)


@app.command("shortcuts")
def share_shortcuts() -> None:
    """Create desktop shortcuts that toggle sharing on/off with a double-click
    (Windows only -- there's no equivalent asked for on macOS/Linux)."""
    if sys.platform != "win32":
        typer.echo("Desktop shortcuts are only supported on Windows.")
        return

    try:
        created = shortcuts_module.create_shortcuts(caseclerk_bin=_caseclerk_binary())
    except shortcuts_module.ShortcutsUnsupportedError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo("Created desktop shortcuts:")
    for path in created:
        typer.echo(f"  {path}")
