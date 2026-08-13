"""`caseclerk share start|stop|status|setup`: the HTTP transport + a cloudflared
named tunnel as two detached child processes, tracked by a pidfile in the data
dir.

Both processes are started the same way the auto-updater spawns things: detached,
stdio silenced, PID recorded so a later `stop` (a separate process invocation) can
find and terminate them. Neither child is a Python object we keep a handle to --
`share start` and `share stop` are two different CLI invocations.

The typer commands below are thin: each one's actual logic lives in a plain
function (`start_sharing`, `stop_sharing`, `setup_credentials`, `is_running`)
that takes/returns plain data and never calls `typer.echo` -- these are the
library entry points the caseclerk-tray GUI calls directly, so there is
exactly one implementation of "start sharing" / "run the non-interactive
tunnel setup" shared by both surfaces, not two.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer

from caseclerk_cli import cloudflared as cloudflared_module
from caseclerk_cli import shortcuts as shortcuts_module
from caseclerk_core import db
from caseclerk_core.config import data_dir, load_config, save_config

app = typer.Typer(help="Manage remote access for ChatGPT: the HTTP transport + a cloudflared tunnel.")

PIDFILE_NAME = "share.json"
STOP_TIMEOUT_SECONDS = 5.0
MCP_PATH = "/mcp"


def _pidfile_path() -> Path:
    return data_dir() / PIDFILE_NAME


def _caseclerk_binary() -> Path:
    # .resolve() so this is robust to how caseclerk was launched -- via PATH,
    # a Desktop/Start Menu shortcut, or a direct path all leave sys.executable
    # as an already-absolute path (Windows resolves it before exec either
    # way), but resolving normalizes away any `.`/`..` segments too, matching
    # binary_update.install_dir()'s same defensive pattern.
    bin_dir = Path(sys.executable).resolve().parent
    name = "caseclerk.exe" if sys.platform == "win32" else "caseclerk"
    return bin_dir / name


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


@dataclass(frozen=True)
class SetupOutcome:
    """Result of `setup_credentials` -- everything both `caseclerk share setup
    --credentials` and the tray app's Settings > Remote sharing setup need to
    report success/failure, without either one re-deriving it."""

    ok: bool
    message: str
    hostname: str | None = None
    tunnel_name: str | None = None
    tunnel_id: str | None = None
    public_url: str | None = None
    binary_path: Path | None = None
    credentials_path: Path | None = None
    config_path: Path | None = None


def setup_credentials(
    credentials: Path,
    *,
    hostname: str,
    tunnel_name: str | None = None,
    progress: Callable[[str], None] | None = None,
    binary: Path | None = None,
) -> SetupOutcome:
    """The entire non-interactive tunnel setup: resolve/download cloudflared
    (unless a resolved `binary` is already supplied), install the tunnel
    credentials, write cloudflared's config.yml, and persist
    share.hostname/tunnelName. This is the ONE implementation of that flow --
    `caseclerk share setup --credentials ... --hostname ...` and the tray
    app's Settings window both call this, neither duplicates it.

    Never raises: filesystem/format problems (missing file, unparseable
    credentials JSON, empty hostname) come back as `SetupOutcome(ok=False, ...)`
    so a GUI caller can show them inline instead of crashing.
    """
    if not hostname.strip():
        return SetupOutcome(False, "Hostname is required.")

    if binary is None:
        try:
            binary = cloudflared_module.resolve(progress=progress)
        except cloudflared_module.CloudflaredError as exc:
            return SetupOutcome(False, f"Could not obtain cloudflared: {exc}")

    cfg = load_config()
    effective_tunnel_name = tunnel_name or cfg.share.tunnel_name

    try:
        result = cloudflared_module.install_credentials(credentials, hostname=hostname, port=cfg.share.port)
    except cloudflared_module.CloudflaredError as exc:
        return SetupOutcome(False, f"Could not install the tunnel credentials: {exc}")

    new_cfg = cfg.model_copy(
        update={
            "share": cfg.share.model_copy(update={"hostname": hostname, "tunnel_name": effective_tunnel_name})
        }
    )
    save_config(new_cfg)

    return SetupOutcome(
        True,
        f"Installed tunnel {result.tunnel_id} -> {hostname}",
        hostname=hostname,
        tunnel_name=effective_tunnel_name,
        tunnel_id=result.tunnel_id,
        public_url=f"https://{hostname}{MCP_PATH}",
        binary_path=binary,
        credentials_path=result.credentials_path,
        config_path=result.config_path,
    )


@app.command("setup")
def share_setup(
    credentials: str | None = typer.Option(
        None,
        "--credentials",
        help=(
            "Path to a tunnel credentials JSON from `cloudflared tunnel create` on "
            "another, already-logged-in machine. With this, setup is fully "
            "non-interactive -- `cloudflared tunnel login` is never needed here."
        ),
    ),
    hostname: str | None = typer.Option(
        None, "--hostname", help="Public hostname to route to this tunnel (required with --credentials)."
    ),
    tunnel_name: str | None = typer.Option(
        None, "--tunnel-name", help="Tunnel name to record in config (defaults to share.tunnelName)."
    ),
) -> None:
    """Ensure a working cloudflared binary is available (downloading it if
    needed) without starting anything -- useful during one-time setup, before
    share.hostname is even configured, so the download happens up front rather
    than as a surprise the first time `share start` runs.

    With --credentials/--hostname, also performs the entire non-interactive
    tunnel setup: installs the credentials, writes cloudflared's config.yml,
    and updates share.hostname/tunnelName -- the whole on-site visit is
    `init`, this command, then `share shortcuts`; the Cloudflare-side steps
    (login, tunnel create, DNS route) happen ahead of time on the developer's
    own machine. (The tray app's Settings window offers the same
    --credentials/--hostname setup from a form, via `setup_credentials`
    above.)"""
    if tunnel_name is not None and credentials is None:
        typer.echo("--tunnel-name requires --credentials.", err=True)
        raise typer.Exit(code=1)
    if hostname is not None and credentials is None:
        typer.echo("--hostname requires --credentials.", err=True)
        raise typer.Exit(code=1)
    if credentials is not None and hostname is None:
        typer.echo("--credentials requires --hostname.", err=True)
        raise typer.Exit(code=1)

    try:
        binary = cloudflared_module.resolve(progress=lambda msg: typer.echo(msg))
    except cloudflared_module.CloudflaredError as exc:
        typer.echo(f"Could not obtain cloudflared: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    version = cloudflared_module.installed_version(binary) or "unknown version"
    source = cloudflared_module.source_label(binary)
    typer.echo(f"cloudflared ready ({source}, {version}): {binary}")

    if credentials is None or hostname is None:
        return

    outcome = setup_credentials(Path(credentials), hostname=hostname, tunnel_name=tunnel_name, binary=binary)
    if not outcome.ok:
        typer.echo(outcome.message, err=True)
        raise typer.Exit(code=1)

    typer.echo(outcome.message)
    typer.echo(f"share.hostname set to {outcome.hostname!r}, share.tunnelName set to {outcome.tunnel_name!r}")

    # A verification pass rather than a live tunnel dry-run: actually
    # connecting would need real network access to Cloudflare's edge and a
    # genuinely valid credentials file, neither of which this command can
    # assume (or a test can mock) -- what's checked here is that setup left
    # behind exactly the files `share start` will need.
    typer.echo("\nVerifying setup:")
    verified = True
    if outcome.binary_path is not None and outcome.binary_path.is_file():
        typer.echo(f"[ok]   cloudflared binary present: {outcome.binary_path}")
    else:
        verified = False
        typer.echo("[FAIL] cloudflared binary missing")
    if outcome.credentials_path is not None and outcome.credentials_path.is_file():
        typer.echo(f"[ok]   tunnel credentials installed: {outcome.credentials_path}")
    else:
        verified = False
        typer.echo("[FAIL] tunnel credentials missing")
    if outcome.config_path is not None and outcome.config_path.is_file():
        typer.echo(f"[ok]   cloudflared config written: {outcome.config_path}")
    else:
        verified = False
        typer.echo("[FAIL] cloudflared config missing")

    if not verified:
        raise typer.Exit(code=1)


@dataclass(frozen=True)
class StartOutcome:
    ok: bool
    message: str
    public_url: str | None = None


def start_sharing(*, progress: Callable[[str], None] | None = None) -> StartOutcome:
    """Start the HTTP transport and the cloudflared tunnel, both detached.
    Library entry point shared by `caseclerk share start` and the tray app's
    "Start Sharing" action -- no typer/echo side effects."""
    state = _read_state()
    if state and (_is_alive(state.get("server_pid")) or _is_alive(state.get("cloudflared_pid"))):
        return StartOutcome(False, "share is already running -- see `caseclerk share status`.")

    cfg = load_config()
    if not cfg.share.hostname:
        return StartOutcome(
            False,
            "share.hostname is not configured. Complete the one-time cloudflared setup "
            "(see the README's Remote access section), then run "
            "`caseclerk config set share.hostname <your-hostname>`.",
        )

    try:
        cloudflared_bin = cloudflared_module.resolve(progress=progress)
    except cloudflared_module.CloudflaredError as exc:
        return StartOutcome(False, f"Could not obtain cloudflared: {exc}")

    caseclerk_bin = _caseclerk_binary()
    server_pid = _spawn([str(caseclerk_bin), "serve", "--transport", "http", "--port", str(cfg.share.port)])

    # A config.yml from `share setup --credentials ...` means this machine
    # never ran `cloudflared tunnel login` -- run the tunnel by pointing
    # cloudflared straight at that config (which itself names the credentials
    # file), rather than the `--url ... <name>` quick-tunnel form, which
    # resolves the tunnel name through the Cloudflare API using a login-issued
    # cert.pem this machine was deliberately never given.
    cloudflared_config = cloudflared_module.config_path()
    if cloudflared_config.is_file():
        cloudflared_args = [str(cloudflared_bin), "--config", str(cloudflared_config), "tunnel", "run"]
    else:
        cloudflared_args = [
            str(cloudflared_bin),
            "tunnel",
            "run",
            "--url",
            f"http://127.0.0.1:{cfg.share.port}",
            cfg.share.tunnel_name,
        ]
    cloudflared_pid = _spawn(cloudflared_args)

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
    return StartOutcome(True, f"Started. Public URL: {public_url}", public_url=public_url)


@app.command("start")
def share_start() -> None:
    """Start the HTTP transport and the cloudflared tunnel, both detached."""
    outcome = start_sharing(progress=lambda msg: typer.echo(msg))
    if not outcome.ok:
        typer.echo(outcome.message, err=True)
        raise typer.Exit(code=1)

    typer.echo(outcome.message)
    typer.echo(
        "In ChatGPT: Settings -> Security and login -> Developer mode (web, chatgpt.com only), "
        f"then create a developer-mode app pointed at {outcome.public_url} with OAuth authentication."
    )
    typer.echo("Run `caseclerk share stop` when you're done.")


@dataclass(frozen=True)
class StopOutcome:
    ok: bool
    message: str


def stop_sharing() -> StopOutcome:
    """Stop the HTTP transport and the tunnel, verifying both processes are
    dead. Library entry point shared by `caseclerk share stop` and the tray
    app's "Stop Sharing" action -- no typer/echo side effects."""
    state = _read_state()
    if state is None:
        return StopOutcome(True, "share is not running.")

    server_dead = _terminate(state.get("server_pid"))
    cloudflared_dead = _terminate(state.get("cloudflared_pid"))
    _clear_state()

    if server_dead and cloudflared_dead:
        return StopOutcome(True, "Stopped.")
    return StopOutcome(False, "Stopped, but one or more processes may not have exited cleanly.")


@app.command("stop")
def share_stop() -> None:
    """Stop the HTTP transport and the tunnel, verifying both processes are dead."""
    outcome = stop_sharing()
    typer.echo(outcome.message, err=not outcome.ok)
    if not outcome.ok:
        raise typer.Exit(code=1)


def is_running() -> bool:
    """Whether the HTTP transport and/or the cloudflared tunnel is currently
    up -- the single source of truth `caseclerk share status` and the tray
    app's menu/Status window both read."""
    state = _read_state()
    if not state:
        return False
    return _is_alive(state.get("server_pid")) or _is_alive(state.get("cloudflared_pid"))


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
