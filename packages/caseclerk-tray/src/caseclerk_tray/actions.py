"""Tray menu/window actions: start/stop sharing, the remote-sharing setup
form, autostart toggling, update check/apply, and relaunch. Every function
here is GUI-free -- no pystray or tkinter import -- and reports outcomes via
the `logging` module rather than printing, per this repo's library-code
convention (only the CLI's typer output and the tray's own `--smoke` line
print to stdout). `ui_tray.py`/`windows.py` call these from menu/button
handlers and translate the return values into UI updates.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

from caseclerk_cli import share as share_module
from caseclerk_core import binary_update
from caseclerk_core import update as core_update
from caseclerk_core.config import Config
from caseclerk_tray import autostart

logger = logging.getLogger(__name__)


def start_sharing() -> share_module.StartOutcome:
    outcome = share_module.start_sharing()
    if outcome.ok:
        logger.info("sharing started: %s", outcome.message)
    else:
        logger.warning("could not start sharing: %s", outcome.message)
    return outcome


def stop_sharing() -> share_module.StopOutcome:
    outcome = share_module.stop_sharing()
    if outcome.ok:
        logger.info("sharing stopped: %s", outcome.message)
    else:
        logger.warning("problem stopping sharing: %s", outcome.message)
    return outcome


def setup_sharing(
    credentials: Path, *, hostname: str, tunnel_name: str | None = None
) -> share_module.SetupOutcome:
    """Tray-facing wrapper around the one shared `share.setup_credentials`
    implementation -- used by the Settings window's "Remote sharing setup"
    section, exactly the same code path `caseclerk share setup
    --credentials/--hostname` runs."""
    outcome = share_module.setup_credentials(credentials, hostname=hostname, tunnel_name=tunnel_name)
    if outcome.ok:
        logger.info("remote sharing set up: %s", outcome.message)
    else:
        logger.warning("remote sharing setup failed: %s", outcome.message)
    return outcome


def set_autostart(enabled: bool) -> bool:
    """Best-effort: returns False (and logs) rather than raising when
    autostart is unsupported (non-Windows) so a menu click never crashes the
    tray."""
    try:
        if enabled:
            autostart.enable()
        else:
            autostart.disable()
        return True
    except autostart.AutostartUnsupportedError as exc:
        logger.warning("could not change autostart: %s", exc)
        return False


def check_for_update(conn: sqlite3.Connection, cfg: Config) -> str | None:
    """Newer version tag if one is available, respecting core's own
    cache/interval logic (at most one real network check per
    updates.checkIntervalHours) -- what the tray's background poll loop
    calls on every tick; cheap, since it only actually hits the network
    once per interval."""
    return core_update.check_for_update(conn, check_interval_hours=cfg.updates.check_interval_hours)


def check_for_update_now(conn: sqlite3.Connection) -> str | None:
    """Explicit, user-initiated check -- the tray menu's "Check for
    Updates..." item and the Status window's button -- always asks GitHub
    fresh rather than respecting updates.checkIntervalHours. Mirrors
    `caseclerk update`'s own fix for the same problem: honoring the
    configured interval here would silently repeat a stale "no update"
    answer for up to a day after the user explicitly asked."""
    return core_update.check_for_update(conn, check_interval_hours=0)


def apply_staged_update(version_tag: str) -> binary_update.BinaryUpdateResult | None:
    """Download and swap in `version_tag`'s packaged build. None (a no-op) if
    this isn't a frozen (PyInstaller) install -- caseclerk-tray only ever
    ships as one, but dev runs (`uv run caseclerk-tray`) must not attempt a
    binary swap they have no zip asset for."""
    if not binary_update.is_frozen():
        return None
    return binary_update.apply_binary_update(version_tag)


def _tray_binary() -> list[str]:
    """The command line to relaunch this same tray app with: the frozen exe
    itself, or (in dev) the current interpreter running the package."""
    if binary_update.is_frozen():
        name = "caseclerk-tray.exe" if sys.platform == "win32" else "caseclerk-tray"
        return [str(Path(sys.executable).resolve().parent / name)]
    return [sys.executable, "-m", "caseclerk_tray"]


def relaunch() -> None:
    """Spawn a fresh detached tray process (picking up a just-applied binary
    swap) and let the caller exit this one -- this function does not exit
    the current process itself, so callers control shutdown ordering (e.g.
    stopping the pystray icon first)."""
    args = _tray_binary()
    logger.info("relaunching: %s", " ".join(args))
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
