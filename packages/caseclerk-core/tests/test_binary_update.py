from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from caseclerk_core import binary_update


def _client(handler: object) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


def _make_zip_bytes(*, wrap_in_folder: bool = False, include_exe: bool = True) -> bytes:
    buf = io.BytesIO()
    prefix = "caseclerk/" if wrap_in_folder else ""
    with zipfile.ZipFile(buf, "w") as zf:
        if include_exe:
            zf.writestr(f"{prefix}{binary_update.EXE_NAME}", "new exe bytes")
        zf.writestr(f"{prefix}support.dll", "new support bytes")
    return buf.getvalue()


def test_is_frozen_reflects_sys_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert binary_update.is_frozen() is False
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert binary_update.is_frozen() is True


def test_install_dir_is_executable_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # install_dir() resolves the path (fills in a drive letter on Windows, etc.),
    # so compare against an equally-resolved tmp_path rather than a hand-written
    # POSIX-style string, which would only round-trip unchanged on POSIX.
    exe_path = tmp_path / "caseclerk.exe"
    monkeypatch.setattr(sys, "executable", str(exe_path))
    assert binary_update.install_dir() == tmp_path.resolve()


def test_release_asset_name_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert binary_update.release_asset_name() == binary_update.WINDOWS_ASSET_NAME
    monkeypatch.setattr(sys, "platform", "linux")
    assert binary_update.release_asset_name() is None
    monkeypatch.setattr(sys, "platform", "darwin")
    assert binary_update.release_asset_name() is None


def test_manual_download_url() -> None:
    url = binary_update.manual_download_url("v0.3.0")
    assert url == "https://github.com/chrismeyersfsu/caseclerk/releases/tag/v0.3.0"


def _make_install_dir(tmp_path: Path) -> Path:
    target = tmp_path / "install"
    target.mkdir()
    (target / binary_update.EXE_NAME).write_text("old exe bytes")
    (target / "support.dll").write_text("old support bytes")
    return target


def test_swap_in_renames_old_and_moves_new(tmp_path: Path) -> None:
    target = _make_install_dir(tmp_path)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / binary_update.EXE_NAME).write_text("new exe bytes")
    (staged / "support.dll").write_text("new support bytes")

    binary_update._swap_in(staged, target)

    assert (target / binary_update.EXE_NAME).read_text() == "new exe bytes"
    assert (target / "support.dll").read_text() == "new support bytes"
    assert (target / f"{binary_update.EXE_NAME}.old").read_text() == "old exe bytes"
    assert (target / "support.dll.old").read_text() == "old support bytes"


def test_swap_in_replaces_a_stale_old_leftover(tmp_path: Path) -> None:
    target = _make_install_dir(tmp_path)
    (target / f"{binary_update.EXE_NAME}.old").write_text("ancient leftover")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / binary_update.EXE_NAME).write_text("new exe bytes")
    (staged / "support.dll").write_text("new support bytes")

    binary_update._swap_in(staged, target)

    # the fresh .old (this swap's own backup) wins over the ancient leftover
    assert (target / f"{binary_update.EXE_NAME}.old").read_text() == "old exe bytes"
    assert (target / binary_update.EXE_NAME).read_text() == "new exe bytes"


def test_cleanup_stale_files_removes_old_suffixed_entries(tmp_path: Path) -> None:
    target = _make_install_dir(tmp_path)
    (target / f"{binary_update.EXE_NAME}.old").write_text("leftover")
    (target / "old_support_dir.old").mkdir()
    (target / "old_support_dir.old" / "nested.txt").write_text("x")

    removed = binary_update.cleanup_stale_files(target)

    assert removed == 2
    assert not (target / f"{binary_update.EXE_NAME}.old").exists()
    assert not (target / "old_support_dir.old").exists()
    assert (target / binary_update.EXE_NAME).exists()  # untouched, non-.old files survive


def test_cleanup_stale_files_on_missing_dir_is_a_noop(tmp_path: Path) -> None:
    assert binary_update.cleanup_stale_files(tmp_path / "does-not-exist") == 0


def test_extract_zip_direct_layout(tmp_path: Path) -> None:
    zip_path = tmp_path / "release.zip"
    zip_path.write_bytes(_make_zip_bytes(wrap_in_folder=False))

    result_dir = binary_update._extract_zip(zip_path, tmp_path / "extracted")

    assert (result_dir / binary_update.EXE_NAME).is_file()


def test_extract_zip_wrapped_layout(tmp_path: Path) -> None:
    zip_path = tmp_path / "release.zip"
    zip_path.write_bytes(_make_zip_bytes(wrap_in_folder=True))

    result_dir = binary_update._extract_zip(zip_path, tmp_path / "extracted")

    assert result_dir.name == "caseclerk"
    assert (result_dir / binary_update.EXE_NAME).is_file()


def test_extract_zip_missing_exe_raises(tmp_path: Path) -> None:
    zip_path = tmp_path / "release.zip"
    zip_path.write_bytes(_make_zip_bytes(include_exe=False))

    with pytest.raises(binary_update.BinaryUpdateError, match="did not contain"):
        binary_update._extract_zip(zip_path, tmp_path / "extracted")


def test_apply_binary_update_unsupported_platform_returns_manual_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    result = binary_update.apply_binary_update("v0.3.0")
    assert result.ok is False
    assert "no packaged build" in result.detail
    assert binary_update.manual_download_url("v0.3.0") in result.detail


def test_apply_binary_update_full_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(binary_update, "data_dir", lambda: tmp_path / "data")
    target = _make_install_dir(tmp_path)
    monkeypatch.setattr(binary_update, "install_dir", lambda: target)

    zip_bytes = _make_zip_bytes(wrap_in_folder=False)
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(200, content=zip_bytes)

    with _client(handler) as client:
        result = binary_update.apply_binary_update("v0.3.0", client=client)

    assert result.ok is True
    assert "v0.3.0" in result.detail
    assert requested_urls == [
        "https://github.com/chrismeyersfsu/caseclerk/releases/download/v0.3.0/caseclerk-windows-x64.zip"
    ]
    assert (target / binary_update.EXE_NAME).read_text() == "new exe bytes"
    assert (target / f"{binary_update.EXE_NAME}.old").read_text() == "old exe bytes"
    # staging is cleaned up after a successful swap
    assert not (tmp_path / "data" / "updates" / "v0.3.0").exists()


def test_apply_binary_update_download_failure_falls_back_to_manual_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(binary_update, "data_dir", lambda: tmp_path / "data")
    target = _make_install_dir(tmp_path)
    monkeypatch.setattr(binary_update, "install_dir", lambda: target)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    with _client(handler) as client:
        result = binary_update.apply_binary_update("v0.3.0", client=client)

    assert result.ok is False
    assert "v0.3.0" in result.detail
    assert binary_update.manual_download_url("v0.3.0") in result.detail
    # the install dir was never touched -- download failed before any swap
    assert (target / binary_update.EXE_NAME).read_text() == "old exe bytes"
    assert not (target / f"{binary_update.EXE_NAME}.old").exists()


def test_apply_binary_update_bad_zip_falls_back_to_manual_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(binary_update, "data_dir", lambda: tmp_path / "data")
    target = _make_install_dir(tmp_path)
    monkeypatch.setattr(binary_update, "install_dir", lambda: target)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not actually a zip file")

    with _client(handler) as client:
        result = binary_update.apply_binary_update("v0.3.0", client=client)

    assert result.ok is False
    assert binary_update.manual_download_url("v0.3.0") in result.detail
    assert (target / binary_update.EXE_NAME).read_text() == "old exe bytes"
