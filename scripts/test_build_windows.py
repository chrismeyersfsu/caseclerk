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


def test_collect_all_and_copy_metadata_include_our_own_distribution() -> None:
    # --version relies on importlib.metadata.version("caseclerk-cli"); a frozen
    # build without its metadata copied in would silently report 0.0.0 (see
    # caseclerk_core.update.current_version's PackageNotFoundError fallback).
    assert "caseclerk-cli" in build_windows.COPY_METADATA
