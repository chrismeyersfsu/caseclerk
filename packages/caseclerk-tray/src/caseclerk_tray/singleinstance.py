"""Single-instance guard: a second `caseclerk-tray` launch (e.g. the
installer's "Launch CaseClerk" finish-page checkbox firing while a previous
session is still in the tray) must log and exit 0, not open a second icon.

Windows: a named mutex via `CreateMutexW` + `GetLastError() ==
ERROR_ALREADY_EXISTS` -- the standard Win32 single-instance pattern; the
handle is intentionally never closed (Windows releases it when the process
exits), since closing it would release the mutex while still running.

Everywhere else (and as a Windows fallback if ctypes access ever fails): a
pidfile in the data dir, holding the owning PID, checked for liveness.
"""

from __future__ import annotations

import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MUTEX_NAME = "Global\\CaseClerkTraySingleInstance"
PIDFILE_NAME = "tray.pid"
_ERROR_ALREADY_EXISTS = 183

# Kept alive for the process lifetime -- see the module docstring on why this
# is deliberately never closed.
_windows_mutex_handle: int | None = None


def _default_pidfile_path() -> Path:
    from caseclerk_core.config import data_dir

    return data_dir() / PIDFILE_NAME


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    except AttributeError:
        # os.kill doesn't exist at all on some minimal platforms; treat as
        # "can't tell", which errs toward allowing a new instance rather than
        # wedging forever on a stale pidfile.
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    """`os.kill(pid, 0)` is not a safe existence probe on Windows: signal 0
    is literally CTRL_C_EVENT there, so it delivers a real Ctrl+C to the
    target instead of merely checking it exists -- if the target happens to
    be this very process (e.g. re-checking our own just-written pidfile),
    that Ctrl+C lands on ourselves. `tasklist` is a stock Windows tool that
    answers the question without touching the process at all. (Same fix as
    caseclerk_cli.share._is_alive_windows, which hit this exact pitfall
    first.)"""
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


def _acquire_pidfile(pidfile_path: Path | None) -> bool:
    path = pidfile_path if pidfile_path is not None else _default_pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file():
        try:
            existing_pid = int(path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            existing_pid = None
        if existing_pid is not None and _pid_alive(existing_pid):
            return False

    path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _acquire_windows(*, kernel32_module: Any = None) -> bool:
    """CreateMutexW + GetLastError() == ERROR_ALREADY_EXISTS. Imported via
    importlib rather than a bare `import ctypes; ctypes.WinDLL(...)` for two
    reasons: `ctypes.WinDLL`/`ctypes.windll` are typeshed-gated to
    `sys.platform == "win32"` (mypy here runs on every OS in CI), and this
    doubles as the injectable seam tests use to exercise this path without a
    real Windows mutex."""
    ctypes_mod = importlib.import_module("ctypes")
    if kernel32_module is not None:
        kernel32 = kernel32_module
    else:
        kernel32 = ctypes_mod.WinDLL("kernel32", use_last_error=True)
        # Without explicit types, ctypes defaults a function's return type to
        # c_int, which would silently truncate the 64-bit HANDLE
        # CreateMutexW actually returns on x64 Windows -- harmless in
        # practice (kernel handles are small values) but not correct, and
        # CloseHandle below deserves the real value, not a truncated one.
        kernel32.CreateMutexW.restype = ctypes_mod.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes_mod.c_void_p, ctypes_mod.c_int, ctypes_mod.c_wchar_p]

    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        raise OSError("CreateMutexW failed")

    already_running = ctypes_mod.get_last_error() == _ERROR_ALREADY_EXISTS
    if already_running:
        kernel32.CloseHandle(handle)
        return False

    global _windows_mutex_handle
    _windows_mutex_handle = handle
    return True


def acquire(*, pidfile_path: Path | None = None) -> bool:
    """True if this process now holds the single-instance lock; False if
    another instance already holds it (caller should log and exit 0)."""
    if sys.platform == "win32":
        try:
            return _acquire_windows()
        except OSError as exc:
            logger.warning("Windows mutex single-instance check failed (%s), falling back to pidfile", exc)
    return _acquire_pidfile(pidfile_path)
