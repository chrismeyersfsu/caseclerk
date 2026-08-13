"""Start-on-login via the per-user Run key
(``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``, value name
``CaseClerk``) -- the same mechanism the installer's opt-in "start
automatically" [Tasks] entry writes (see scripts/installer.iss), so toggling
the tray's "Start CaseClerk when Windows starts" checkbox and (re)running the
installer with that box checked converge on the same registry value.

``winreg`` only exists on Windows, so every function here takes an injectable
``winreg_module`` seam (mirroring caseclerk_core.binary_update's
``_update_windows_uninstall_metadata`` pattern) -- real callers never pass it
and get the platform check; tests on any OS inject a fake module-like double
and bypass that check on purpose.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CaseClerk"


class AutostartUnsupportedError(Exception):
    """Autostart via the registry Run key is Windows-only."""


def _target_command() -> str:
    """The command line to register: the frozen caseclerk-tray.exe itself, or
    (in dev) `python -m caseclerk_tray` via the current interpreter."""
    exe = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return f'"{exe}"'
    return f'"{exe}" -m caseclerk_tray'


def _resolve_winreg(winreg_module: Any) -> Any:
    if winreg_module is not None:
        return winreg_module
    if sys.platform != "win32":
        return None
    return importlib.import_module("winreg")


def is_enabled(*, winreg_module: Any = None) -> bool | None:
    """True/False if this is Windows (real or injected), None if autostart is
    simply unsupported on this platform -- callers (state.py's menu model)
    use None to omit the autostart menu item/checkbox entirely rather than
    show a permanently-disabled one."""
    module = _resolve_winreg(winreg_module)
    if module is None:
        return None
    try:
        key = module.OpenKey(module.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, module.KEY_READ)
    except FileNotFoundError:
        return False
    try:
        module.QueryValueEx(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    finally:
        module.CloseKey(key)


def enable(*, winreg_module: Any = None) -> None:
    """Write the Run key value. Raises AutostartUnsupportedError off-Windows."""
    module = _resolve_winreg(winreg_module)
    if module is None:
        raise AutostartUnsupportedError("autostart is only supported on Windows")
    key = module.OpenKey(module.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, module.KEY_SET_VALUE)
    try:
        module.SetValueEx(key, VALUE_NAME, 0, module.REG_SZ, _target_command())
    finally:
        module.CloseKey(key)
    logger.info("autostart enabled")


def disable(*, winreg_module: Any = None) -> None:
    """Remove the Run key value, if present. Raises AutostartUnsupportedError
    off-Windows; a missing value is not an error (already disabled)."""
    module = _resolve_winreg(winreg_module)
    if module is None:
        raise AutostartUnsupportedError("autostart is only supported on Windows")
    try:
        key = module.OpenKey(module.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, module.KEY_SET_VALUE)
    except FileNotFoundError:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            module.DeleteValue(key, VALUE_NAME)
    finally:
        module.CloseKey(key)
    logger.info("autostart disabled")
