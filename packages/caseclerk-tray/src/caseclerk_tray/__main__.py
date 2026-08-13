"""`caseclerk-tray` entry point: single-instance guard, config/db init, then
either the real tray app or the headless `--smoke` self-check CI uses to
prove a frozen build actually starts, without a display.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

_STD_OUTPUT_HANDLE = -11
_ATTACH_PARENT_PROCESS = -1
# GetStdHandle's two different "nothing here" answers: NULL (no handle
# associated) and INVALID_HANDLE_VALUE (an error retrieving it) -- distinct
# from each other, and from a real handle, but both mean "not valid".
# ctypes' c_void_p restype surfaces a non-null pointer as an unsigned
# Python int (so INVALID_HANDLE_VALUE, all bits set, comes back as a large
# positive number on a 64-bit process, not literally -1) -- this set covers
# every representation a testable double might plausibly hand back too.
_INVALID_STDIO_HANDLES = {0, -1, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF}


def _configure_logging(*, level: int = logging.INFO) -> None:
    # A console=False frozen build launched with no console and no
    # redirection can have sys.stderr as None (older PyInstaller
    # bootloaders) or a dummy/NUL-backed stream (newer ones); either way,
    # don't depend on exactly which behavior this build's PyInstaller
    # version has -- logging.basicConfig()'s default StreamHandler would
    # crash on the very first log call (AttributeError: 'NoneType' object
    # has no attribute 'write') if it's genuinely None.
    stream = sys.stderr if sys.stderr is not None else open(os.devnull, "w")  # noqa: SIM115
    logging.basicConfig(level=level, stream=stream)


def _has_valid_stdio_handle(kernel32: Any) -> bool:
    """True if GetStdHandle(STD_OUTPUT_HANDLE) already returns something
    real. A console is one such thing, but so -- exactly as valid, and
    handed to a GUI-subsystem process (console=False) the same as a console
    one -- is a redirected pipe or file, which is what release.yml's smoke
    tests give us (`& caseclerk-tray.exe --smoke 2>&1 | Out-String`). Pulled
    out as its own function so the decision itself is unit-testable via an
    injected double, independent of the real Win32 call below."""
    handle = kernel32.GetStdHandle(_STD_OUTPUT_HANDLE)
    return handle not in _INVALID_STDIO_HANDLES


def _attach_console_for_smoke() -> None:
    """caseclerk-tray.exe is built console=False (a normal double-click
    launch must never pop up a console window) -- but `--smoke` is meant to
    be run from CI's own terminal/pipeline, which needs to actually see its
    one status line.

    Only touches stdio when there is genuinely nowhere valid to write yet
    (see _has_valid_stdio_handle) -- a real, confirmed release-run bug, twice
    over: an earlier version of this function called
    AttachConsole(ATTACH_PARENT_PROCESS) + rebound sys.stdout/sys.stderr to
    CONOUT$ UNCONDITIONALLY whenever frozen+windowed on win32. In CI, the
    pwsh host running the step DOES have a console, even though it had
    redirected OUR stdio into a pipe for its own capture (`2>&1 |
    Out-String`) -- so AttachConsole "succeeded", and rebinding stdout/stderr
    to that unrelated console silently abandoned the pipe PowerShell was
    actually reading, with nothing arriving on the other end. (First
    surfaced as the status line missing while INFO log noise still arrived,
    via a since-fixed logging.StreamHandler quirk that had cached the
    original, still-valid stderr object before the rebind; then, once that
    noise was silenced, as the captured output being completely empty.)
    AttachConsole is now only attempted -- and stdout/stderr only rebound to
    CONOUT$ -- for a genuine double-click (no console, no redirection) or an
    interactive terminal launch with no redirection either (a real console
    exists but was never attached, since Windows never auto-attaches one to
    a GUI-subsystem process).

    `ctypes.WinDLL` is accessed via `importlib.import_module` (returning a
    plain module object, typed as ``Any``) rather than a direct
    `import ctypes` reference -- `ctypes.WinDLL`/`.windll` are typeshed-gated
    to `sys.platform == "win32"`, and mypy here runs on every OS in CI.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    ctypes_mod = importlib.import_module("ctypes")
    kernel32 = ctypes_mod.WinDLL("kernel32", use_last_error=True)
    # GetStdHandle returns a HANDLE (pointer-sized); without this, ctypes'
    # default c_int return type would truncate it on 64-bit Windows -- same
    # class of bug as CreateMutexW's in caseclerk_tray.singleinstance.
    kernel32.GetStdHandle.restype = ctypes_mod.c_void_p

    if _has_valid_stdio_handle(kernel32):
        return  # already have somewhere real to write -- leave it alone

    if not kernel32.AttachConsole(_ATTACH_PARENT_PROCESS):
        return  # no parent console either (e.g. a genuine double-click) -- stay silent
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

    status_line = (
        f"caseclerk-tray {core_update.current_version()}: "
        f"sharing={'on' if tray_state.sharing_on else 'off'} "
        f"processing_total={tray_state.processing_total} "
        f"failures={tray_state.failure_count} "
        f"menu_items={len(menu_model)}"
    )
    # Belt-and-braces: emitted on BOTH stdout and stderr. release.yml merges
    # them (`2>&1`) and only needs the line to appear somewhere in the
    # combined capture -- if some future Windows console/stream quirk ever
    # breaks one of the two streams again, the other still gets through.
    print(status_line)
    print(status_line, file=sys.stderr)
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
