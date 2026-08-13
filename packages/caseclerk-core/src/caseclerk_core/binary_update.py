"""Binary-install self-update: when running as a frozen (PyInstaller) executable
there is no Python/uv on the machine at all, so `uv tool install` (the normal
update mechanism in ``update.py``) cannot run. This module downloads the
release's platform zip instead, and swaps it into the running install directory.

Windows will not let you overwrite a file the running process has mapped, but
it *will* let you rename it -- this is the standard self-update trick: rename
every entry in the install directory aside (an ``.old`` suffix), then move the
freshly-downloaded entries into their place. The *running* process keeps
executing fine off the old (now renamed) files, so nothing crashes mid-update;
the swap takes visible effect the next time the executable is started. A
cleanup pass at every startup (`cleanup_stale_files`) removes `.old` leftovers
once they're actually safe to delete -- i.e. once nothing still has them open.

Every network/filesystem step is wrapped so a failure degrades to returning
the manual-download URL rather than raising or leaving a half-applied install;
the caller (`caseclerk update`) is expected to print that URL as a fallback.
"""

from __future__ import annotations

import importlib
import logging
import shutil
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from caseclerk_core.config import data_dir

logger = logging.getLogger(__name__)

REPO = "chrismeyersfsu/caseclerk"
WINDOWS_ASSET_NAME = "caseclerk-windows-x64.zip"
EXE_NAME = "caseclerk.exe"
REQUEST_TIMEOUT_SECONDS = 120.0
_OLD_SUFFIX = ".old"
_UPDATES_DIRNAME = "updates"

# Must match scripts/installer.iss's MyAppId/MyAppName exactly -- this is the
# HKCU uninstall registry key (and display name) Inno Setup's installer
# creates on install, that _update_windows_uninstall_metadata refreshes here.
_INNO_APP_ID = "6A3B09B5-E95C-4D9F-AEC6-67AB9A91414C"
_INNO_APP_NAME = "CaseClerk"


class BinaryUpdateError(Exception):
    """A step in the binary self-update failed; callers should treat this the
    same as any other failure -- fall back to the manual download URL."""


@dataclass(frozen=True)
class BinaryUpdateResult:
    ok: bool
    detail: str


def is_frozen() -> bool:
    """True when running as a PyInstaller-frozen executable (set by PyInstaller
    itself at runtime; never true for `uv run caseclerk` / an installed console
    script)."""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """The directory containing the currently-running frozen executable -- the
    onedir bundle's root, where caseclerk.exe and its support files live."""
    return Path(sys.executable).resolve().parent


def release_asset_name() -> str | None:
    """The zip asset name published for this platform, or None if no packaged
    build exists for it yet (currently: Windows only)."""
    return WINDOWS_ASSET_NAME if sys.platform == "win32" else None


def manual_download_url(version_tag: str) -> str:
    return f"https://github.com/{REPO}/releases/tag/{version_tag}"


def _staging_dir(version_tag: str) -> Path:
    return data_dir() / _UPDATES_DIRNAME / version_tag


def _download_zip(version_tag: str, asset_name: str, dest: Path, *, client: httpx.Client) -> None:
    url = f"https://github.com/{REPO}/releases/download/{version_tag}/{asset_name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in response.iter_bytes():
                fh.write(chunk)


def _extract_zip(zip_path: Path, extract_to: Path) -> Path:
    """Extract the release zip and return the directory that directly holds
    caseclerk.exe -- the zip may or may not wrap its contents in one top-level
    folder, so detect which shape it used rather than assume."""
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_to)

    direct = extract_to / EXE_NAME
    if direct.is_file():
        return extract_to
    for child in extract_to.iterdir():
        if child.is_dir() and (child / EXE_NAME).is_file():
            return child
    raise BinaryUpdateError(f"{zip_path.name} did not contain {EXE_NAME}")


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _swap_in(staged_dir: Path, target_dir: Path) -> None:
    """Rename every current entry in target_dir aside, then move every staged
    entry into its place. Entries already carrying the .old suffix (a prior
    swap's leftovers not yet cleaned up) are left alone.

    Deliberately operates on target_dir's TOP-LEVEL entries only -- a
    directory like PyInstaller's `_internal` is renamed aside and moved back
    in as one atomic unit, the same as a plain file, never merged file-by-file
    with the staged one. A per-file merge would leave anything the new
    release doesn't ship (e.g. a stale, version-suffixed .dist-info
    directory from the old build) sitting there unnoticed, which is exactly
    the installer-side bug scripts/installer.iss's [InstallDelete] entry
    exists to prevent for the Inno-driven install path -- see
    test_swap_in_replaces_a_support_directory_wholesale_not_merged.
    """
    for entry in target_dir.iterdir():
        if entry.name.endswith(_OLD_SUFFIX):
            continue
        old_path = entry.with_name(entry.name + _OLD_SUFFIX)
        if old_path.exists():
            _remove(old_path)
        entry.rename(old_path)

    for entry in staged_dir.iterdir():
        entry.rename(target_dir / entry.name)


def cleanup_stale_files(target_dir: Path | None = None) -> int:
    """Delete any `.old`-suffixed leftovers from a previous swap. Safe to call
    on every startup: a fresh process has nothing open in the old files, so a
    removal that would have failed mid-update (still locked by the process
    that just swapped itself out) now succeeds. Best-effort -- failures are
    logged and swallowed, never allowed to block startup. Returns the number
    of entries removed."""
    directory = target_dir if target_dir is not None else install_dir()
    if not directory.is_dir():
        return 0
    removed = 0
    for entry in directory.iterdir():
        if not entry.name.endswith(_OLD_SUFFIX):
            continue
        try:
            _remove(entry)
            removed += 1
        except OSError as exc:
            logger.info("could not remove stale update file %s yet: %s", entry, exc)
    return removed


def has_staged_update(target_dir: Path | None = None) -> bool:
    """True if a previous swap left `.old`-suffixed entries behind that
    `cleanup_stale_files` hasn't removed yet -- i.e. a newer build is already
    in place in `target_dir` and merely needs the process restarted to pick
    it up. This is the signal caseclerk-tray's polling loop uses to show
    "Restart to apply update": the swap in `apply_binary_update` already
    happened by the time it returns `ok=True`, so there is no separate
    "staged but not yet swapped" state to track -- presence of `.old` files
    IS "staged"."""
    directory = target_dir if target_dir is not None else install_dir()
    if not directory.is_dir():
        return False
    return any(entry.name.endswith(_OLD_SUFFIX) for entry in directory.iterdir())


def _update_windows_uninstall_metadata(version_tag: str, *, winreg_module: Any = None) -> None:
    """Best-effort: after a successful swap, refresh the DisplayVersion (and
    DisplayName, which embeds it -- Inno Setup's default AppVerName is "Name
    Version") in the Inno-registered uninstall key, so Settings > Apps shows
    the version that's actually running rather than the one from initial
    install. Windows-only; any failure here -- including there being no such
    key at all, e.g. a non-installer (plain zip) deployment -- must never
    fail the update itself, so every error is swallowed and logged.

    ``winreg_module`` is an injectable seam for tests: the real `winreg`
    module only exists on Windows, so tests on any OS supply a fake
    module-like double instead, bypassing the platform check that guards
    real (non-test) callers.
    """
    if sys.platform != "win32" and winreg_module is None:
        return
    try:
        module = winreg_module if winreg_module is not None else importlib.import_module("winreg")
    except ImportError:
        return

    version = version_tag[1:] if version_tag.startswith("v") else version_tag
    key_path = f"Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{{{_INNO_APP_ID}}}_is1"
    try:
        key = module.OpenKey(module.HKEY_CURRENT_USER, key_path, 0, module.KEY_SET_VALUE)
        try:
            module.SetValueEx(key, "DisplayVersion", 0, module.REG_SZ, version)
            module.SetValueEx(key, "DisplayName", 0, module.REG_SZ, f"{_INNO_APP_NAME} {version}")
        finally:
            module.CloseKey(key)
    except Exception as exc:  # noqa: BLE001 - best-effort; must never fail the update
        logger.info("could not update Windows uninstall metadata (non-fatal): %s", exc)


def apply_binary_update(version_tag: str, *, client: httpx.Client | None = None) -> BinaryUpdateResult:
    """Download, extract, and swap in version_tag's packaged build. Always
    returns a result rather than raising -- a failure is reported as
    `ok=False` with the manual download URL in `detail`."""
    asset_name = release_asset_name()
    if asset_name is None:
        return BinaryUpdateResult(
            ok=False,
            detail=(
                f"no packaged build for this platform; download manually: {manual_download_url(version_tag)}"
            ),
        )

    staging = _staging_dir(version_tag)
    owns_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        zip_path = staging / asset_name
        _download_zip(version_tag, asset_name, zip_path, client=http)
        extracted_dir = _extract_zip(zip_path, staging / "extracted")
        _swap_in(extracted_dir, install_dir())
        _update_windows_uninstall_metadata(version_tag)
        return BinaryUpdateResult(ok=True, detail=f"Updated to {version_tag}. Restart caseclerk to use it.")
    except (httpx.HTTPError, OSError, BinaryUpdateError, zipfile.BadZipFile) as exc:
        logger.warning("binary update to %s failed: %s", version_tag, exc)
        return BinaryUpdateResult(
            ok=False,
            detail=f"update to {version_tag} failed ({exc}); download manually: "
            f"{manual_download_url(version_tag)}",
        )
    finally:
        if owns_client:
            http.close()
        shutil.rmtree(staging, ignore_errors=True)
