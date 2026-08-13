"""`caseclerk-tray` entry point: single-instance guard, config/db init, then
either the real tray app or the headless `--smoke` self-check CI uses to
prove a frozen build actually starts, without a display.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys

logger = logging.getLogger(__name__)


def _configure_logging(*, level: int = logging.INFO) -> None:
    logging.basicConfig(level=level)


def _attach_console_for_smoke() -> None:
    """caseclerk-tray.exe is built console=False (a normal double-click
    launch must never pop up a console window) -- but `--smoke` is meant to
    be run from CI's own terminal, which needs to actually see its one
    status line. AttachConsole(ATTACH_PARENT_PROCESS) + reopening CONOUT$ is
    the standard Win32 trick a windowed-subsystem executable uses to regain
    its launching console's stdio without ever allocating a console window
    of its own. No-op unless frozen+windowed on win32: a dev
    `uv run caseclerk-tray --smoke` already has a real console attached, and
    a double-click launch (no console to attach to) stays silent, as
    intended.

    `ctypes.WinDLL` is accessed via `importlib.import_module` (returning a
    plain module object, typed as ``Any``) rather than a direct
    `import ctypes` reference -- `ctypes.WinDLL`/`.windll` are typeshed-gated
    to `sys.platform == "win32"`, and mypy here runs on every OS in CI.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    ctypes_mod = importlib.import_module("ctypes")
    kernel32 = ctypes_mod.WinDLL("kernel32", use_last_error=True)
    attach_parent_process = -1
    if not kernel32.AttachConsole(attach_parent_process):
        return  # no parent console (e.g. a genuine double-click) -- stay silent
    sys.stdout = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115
    sys.stderr = open("CONOUT$", "w", encoding="utf-8")  # noqa: SIM115


def _run_smoke() -> int:
    """Build state + icons + menu model headlessly -- no pystray event loop,
    no tkinter mainloop -- and print exactly one status line, then exit 0.
    This is what release.yml's Windows job runs against both the unpacked
    onedir bundle and the silent-install directory to prove the frozen build
    actually starts and its pure logic runs."""
    _attach_console_for_smoke()

    from caseclerk_core import db
    from caseclerk_core import update as core_update
    from caseclerk_core.config import load_config
    from caseclerk_tray import autostart, icon, state

    cfg = load_config()
    conn = db.connect()
    try:
        autostart_enabled = autostart.is_enabled()
        tray_state = state.collect_state(conn, cfg, autostart_enabled=autostart_enabled)
    finally:
        conn.close()

    menu_model = state.build_menu_model(tray_state)
    # Building both icons exercises Pillow's raster drawing end to end.
    icon.sharing_off_icon()
    icon.sharing_on_icon()

    print(
        f"caseclerk-tray {core_update.current_version()}: "
        f"sharing={'on' if tray_state.sharing_on else 'off'} "
        f"processing_total={tray_state.processing_total} "
        f"failures={tray_state.failure_count} "
        f"menu_items={len(menu_model)}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="caseclerk-tray")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Headless self-check: build state/icons/menu, print one status line, exit 0.",
    )
    args = parser.parse_args(argv)

    # --smoke's one printed status line is what release.yml's CI asserts on
    # (stdout+stderr merged via `2>&1`) -- routine INFO noise (e.g. db.py's
    # migration log lines, guaranteed on the fresh data dir CI always smoke-
    # tests against) must never precede or interleave with it, so smoke mode
    # raises the root logging level to WARNING before any db work happens.
    # Normal tray runs keep INFO (useful for anyone capturing its logs).
    _configure_logging(level=logging.WARNING if args.smoke else logging.INFO)

    if args.smoke:
        return _run_smoke()

    from caseclerk_tray import singleinstance

    if not singleinstance.acquire():
        logger.info("another caseclerk-tray instance is already running; exiting")
        return 0

    from caseclerk_core import binary_update, db
    from caseclerk_core.config import load_config

    if binary_update.is_frozen():
        binary_update.cleanup_stale_files()

    load_config()
    db.connect().close()  # ensure the db exists/migrates before the tray starts polling it

    from caseclerk_tray import ui_tray

    ui_tray.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
