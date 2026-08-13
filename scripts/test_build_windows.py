from __future__ import annotations

import sys

import build_windows
import pytest


def test_os_arch_label_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    assert build_windows._os_arch_label() == "windows-x64"


def test_os_arch_label_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert build_windows._os_arch_label() == "macos-x64"


def test_os_arch_label_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert build_windows._os_arch_label() == "linux-x64"


def test_entry_script_exists() -> None:
    assert build_windows.ENTRY_SCRIPT.is_file()


def test_tray_entry_script_exists() -> None:
    assert build_windows.TRAY_ENTRY_SCRIPT.is_file()


def test_spec_file_exists() -> None:
    assert build_windows.SPEC_FILE.is_file()


def test_collect_all_and_copy_metadata_include_our_own_distribution() -> None:
    # --version relies on importlib.metadata.version("caseclerk-cli"); a frozen
    # build without its metadata copied in would silently report 0.0.0 (see
    # caseclerk_core.update.current_version's PackageNotFoundError fallback).
    assert "caseclerk-cli" in build_windows.COPY_METADATA


def test_tray_collect_all_and_copy_metadata_extend_the_base_lists() -> None:
    # caseclerk-tray.exe needs everything caseclerk.exe does (it depends on
    # caseclerk-cli) plus its own GUI toolkits -- these must be strict
    # supersets, not a separately hand-maintained list that could drift.
    assert set(build_windows.COLLECT_ALL) < set(build_windows.TRAY_COLLECT_ALL)
    assert "pystray" in build_windows.TRAY_COLLECT_ALL
    assert "PIL" in build_windows.TRAY_COLLECT_ALL

    assert set(build_windows.COPY_METADATA) < set(build_windows.TRAY_COPY_METADATA)
    assert "caseclerk-tray" in build_windows.TRAY_COPY_METADATA
    assert "pystray" in build_windows.TRAY_COPY_METADATA
    assert "pillow" in build_windows.TRAY_COPY_METADATA
