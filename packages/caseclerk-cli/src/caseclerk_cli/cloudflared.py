"""A managed cloudflared binary: `caseclerk share` never asks anyone to install
cloudflared, and never touches PATH or needs admin. Resolution order:

  1. bundled next to a frozen (PyInstaller) executable -- the packaged Windows
     build ships this so the very first `share start` needs no network access.
  2. a previously downloaded copy in data_dir()/bin -- cached across runs.
  3. download the pinned, checksum-verified release asset from
     cloudflare/cloudflared's GitHub releases into data_dir()/bin.

cloudflare/cloudflared does not publish a separate machine-readable checksums
file; each release's notes carry a "SHA256 Checksums" text block instead, so
CLOUDFLARED_VERSION and _ASSET_SHA256 below are a manual, deliberate pin --
bumping the version means re-copying that block from the new release's notes.
"""

from __future__ import annotations

import hashlib
import logging
import platform
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from pathlib import Path

import httpx

from caseclerk_core.binary_update import is_frozen
from caseclerk_core.config import data_dir

logger = logging.getLogger(__name__)

# Pinned 2026-08-12 from https://github.com/cloudflare/cloudflared/releases/tag/2026.7.3
CLOUDFLARED_VERSION = "2026.7.3"
_RELEASE_BASE_URL = f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}"
_BIN_DIRNAME = "bin"
REQUEST_TIMEOUT_SECONDS = 120.0

# Published SHA256 checksums for CLOUDFLARED_VERSION's assets, copied verbatim
# from that release's GitHub notes.
_ASSET_SHA256: dict[str, str] = {
    "cloudflared-windows-amd64.exe": "8635da433b6df8194746e88ed9d2589566c20e38bfc2a80e431a348b7c765841",
    "cloudflared-linux-amd64": "9d71c677db00134c1bd4144b7783486b654ad281b1ea62b4972098d19f770f17",
    "cloudflared-linux-arm64": "65259e652a7bea08bf5df603233ab22b8bf3116af8df9f9206209af6a1b955c0",
    "cloudflared-darwin-amd64.tgz": "e88fe5874d42a94f49a7ea59cabc3722d2962d0449232b0f3b1a426a712e275c",
    "cloudflared-darwin-arm64.tgz": "f35c50089cd25f77a4cb5a2152036bc26db15aa31fbe11f7995d2e42a4ed6257",
}

ProgressFn = Callable[[str], None]


class CloudflaredError(Exception):
    """No working cloudflared binary could be found or downloaded."""


def _binary_name() -> str:
    return "cloudflared.exe" if sys.platform == "win32" else "cloudflared"


def _asset_name() -> str:
    machine = platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "cloudflared-windows-amd64.exe"
    if sys.platform == "darwin":
        return "cloudflared-darwin-arm64.tgz" if is_arm else "cloudflared-darwin-amd64.tgz"
    return "cloudflared-linux-arm64" if is_arm else "cloudflared-linux-amd64"


def _frozen_dir() -> Path | None:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return None


def _bin_dir() -> Path:
    path = data_dir() / _BIN_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cached_path() -> Path:
    return _bin_dir() / _binary_name()


def find_bundled() -> Path | None:
    """A cloudflared binary shipped next to a frozen executable, if any."""
    frozen_dir = _frozen_dir()
    if frozen_dir is None:
        return None
    candidate = frozen_dir / _binary_name()
    return candidate if candidate.is_file() else None


def find_cached() -> Path | None:
    """A previously downloaded copy in data_dir()/bin, if any."""
    candidate = _cached_path()
    return candidate if candidate.is_file() else None


def resolve(
    *,
    allow_download: bool = True,
    progress: ProgressFn | None = None,
    client: httpx.Client | None = None,
) -> Path:
    """Return a path to a working cloudflared binary, downloading it if needed
    (and allowed). Never touches PATH, never requires admin.

    Raises CloudflaredError if no binary can be found or downloaded (e.g.
    offline with nothing cached and nothing bundled).
    """
    bundled = find_bundled()
    if bundled is not None:
        return bundled
    cached = find_cached()
    if cached is not None:
        return cached
    if not allow_download:
        raise CloudflaredError(
            "no cloudflared binary found (not bundled, nothing cached) and downloads are disabled"
        )
    return download(progress=progress, client=client)


def download(*, progress: ProgressFn | None = None, client: httpx.Client | None = None) -> Path:
    """Download, checksum-verify, and cache the pinned cloudflared release asset.

    ``client`` is injectable (as elsewhere in this codebase) so tests exercise
    the real download/verify/extract logic against a `httpx.MockTransport`
    without ever making a real network call.
    """
    asset = _asset_name()
    expected_sha256 = _ASSET_SHA256.get(asset)
    if expected_sha256 is None:
        raise CloudflaredError(f"no pinned checksum for asset {asset!r} (unsupported platform/arch)")

    url = f"{_RELEASE_BASE_URL}/{asset}"
    _emit(progress, f"Downloading cloudflared {CLOUDFLARED_VERSION} ({asset})...")
    bin_dir = _bin_dir()
    owns_client = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        with tempfile.TemporaryDirectory(dir=bin_dir) as tmp:
            tmp_path = Path(tmp) / asset
            _download_to(url, tmp_path, client=http, progress=progress)
            _verify_sha256(tmp_path, expected_sha256)
            binary_path = _extract_binary(tmp_path, asset)
            final_path = _cached_path()
            binary_path.replace(final_path)
    finally:
        if owns_client:
            http.close()
    _make_executable(final_path)
    _emit(progress, f"cloudflared {CLOUDFLARED_VERSION} ready at {final_path}")
    return final_path


def _download_to(url: str, dest: Path, *, client: httpx.Client, progress: ProgressFn | None) -> None:
    try:
        with client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            last_pct = -1
            with dest.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if total and progress is not None:
                        pct = int(downloaded * 100 / total)
                        if pct != last_pct and pct % 10 == 0:
                            _emit(progress, f"  {pct}%")
                            last_pct = pct
    except httpx.HTTPError as exc:
        raise CloudflaredError(f"failed to download cloudflared from {url}: {exc}") from exc


def _verify_sha256(path: Path, expected: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual != expected:
        path.unlink(missing_ok=True)
        raise CloudflaredError(f"checksum mismatch for {path.name}: expected {expected}, got {actual}")


def _extract_binary(downloaded_path: Path, asset: str) -> Path:
    """tgz assets (macOS) wrap the binary in a tarball; everything else IS the binary."""
    if not asset.endswith(".tgz"):
        return downloaded_path
    extract_dir = downloaded_path.parent / "extracted"
    extract_dir.mkdir(exist_ok=True)
    with tarfile.open(downloaded_path, "r:gz") as tar:
        member = next(
            (m for m in tar.getmembers() if m.isfile() and Path(m.name).name == "cloudflared"), None
        )
        if member is None:
            raise CloudflaredError(f"{asset} did not contain a cloudflared binary")
        tar.extract(member, path=extract_dir, filter="data")
    return extract_dir / member.name


def _make_executable(path: Path) -> None:
    if sys.platform == "win32":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _emit(progress: ProgressFn | None, message: str) -> None:
    logger.info(message)
    if progress is not None:
        progress(message)


def installed_version(binary: Path) -> str | None:
    """Best-effort `cloudflared --version` parse, for `doctor`/`share status`."""
    try:
        result = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = (result.stdout or result.stderr or "").strip()
    return output or None


def source_label(binary: Path) -> str:
    """Human-readable origin for `doctor`: bundled / downloaded / unknown."""
    frozen_dir = _frozen_dir()
    if frozen_dir is not None and binary.parent == frozen_dir:
        return "bundled"
    if binary.parent == _bin_dir():
        return "downloaded"
    return "unknown"
