"""`caseclerk share shortcuts`: Windows desktop shortcuts that toggle sharing
on/off with a double-click, for a daily user who shouldn't need a terminal.

Built via PowerShell's WScript.Shell COM object -- the standard, dependency-
free way to create a .lnk file on Windows -- rather than a third-party
shortcut library, so this doesn't need to vendor one more package.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

RunFn = Callable[[list[str]], "subprocess.CompletedProcess[str]"]

ON_SHORTCUT_NAME = "CaseClerk Sharing ON.lnk"
OFF_SHORTCUT_NAME = "CaseClerk Sharing OFF.lnk"


class ShortcutsUnsupportedError(Exception):
    """Desktop shortcuts are a Windows-only feature (WScript.Shell is a
    Windows COM API); there's no equivalent for macOS/Linux."""


@dataclass(frozen=True)
class ShortcutSpec:
    name: str
    args: tuple[str, ...]


SHORTCUT_SPECS: tuple[ShortcutSpec, ...] = (
    ShortcutSpec(ON_SHORTCUT_NAME, ("share", "start")),
    ShortcutSpec(OFF_SHORTCUT_NAME, ("share", "stop")),
)


def desktop_dir() -> Path:
    return Path.home() / "Desktop"


def _escape_ps(value: str) -> str:
    """Escape a value for embedding inside a PowerShell single-quoted string
    (the only special case is a literal single quote, doubled)."""
    return value.replace("'", "''")


def build_script(
    caseclerk_bin: Path, target_dir: Path, specs: tuple[ShortcutSpec, ...] = SHORTCUT_SPECS
) -> str:
    """The PowerShell script that creates every shortcut in `specs`, each
    pointing at caseclerk_bin with its own arguments, landing in target_dir."""
    lines = ["$WshShell = New-Object -ComObject WScript.Shell"]
    for spec in specs:
        lnk_path = target_dir / spec.name
        args = " ".join(spec.args)
        lines.append(f"$s = $WshShell.CreateShortcut('{_escape_ps(str(lnk_path))}')")
        lines.append(f"$s.TargetPath = '{_escape_ps(str(caseclerk_bin))}'")
        lines.append(f"$s.Arguments = '{_escape_ps(args)}'")
        lines.append(f"$s.WorkingDirectory = '{_escape_ps(str(caseclerk_bin.parent))}'")
        lines.append("$s.Save()")
    return "\n".join(lines) + "\n"


def _default_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, timeout=30, check=True)


def create_shortcuts(
    *,
    caseclerk_bin: Path,
    target_dir: Path | None = None,
    run: RunFn = _default_run,
) -> list[Path]:
    """Write the ON/OFF desktop shortcuts by generating and running a small
    PowerShell script. Raises ShortcutsUnsupportedError on any non-Windows
    platform. `run` is injectable so tests capture the invocation without
    actually running PowerShell."""
    if sys.platform != "win32":
        raise ShortcutsUnsupportedError("desktop shortcuts are only supported on Windows")

    directory = target_dir if target_dir is not None else desktop_dir()
    directory.mkdir(parents=True, exist_ok=True)
    script = build_script(caseclerk_bin, directory)

    with tempfile.TemporaryDirectory() as tmp:
        script_path = Path(tmp) / "create-shortcuts.ps1"
        script_path.write_text(script, encoding="utf-8")
        run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)])

    return [directory / spec.name for spec in SHORTCUT_SPECS]
