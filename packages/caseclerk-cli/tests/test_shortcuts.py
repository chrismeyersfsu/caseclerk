from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from caseclerk_cli import shortcuts


def _fake_run_capturing(calls: list[list[str]]) -> shortcuts.RunFn:
    def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    return _run


def test_desktop_dir_is_under_home() -> None:
    assert shortcuts.desktop_dir() == Path.home() / "Desktop"


def test_escape_ps_doubles_single_quotes() -> None:
    assert shortcuts._escape_ps("O'Brien") == "O''Brien"
    assert shortcuts._escape_ps("plain") == "plain"


def test_build_script_contains_both_shortcuts_with_correct_args(tmp_path: Path) -> None:
    caseclerk_bin = tmp_path / "caseclerk.exe"
    target_dir = tmp_path / "Desktop"

    script = shortcuts.build_script(caseclerk_bin, target_dir)

    assert "New-Object -ComObject WScript.Shell" in script
    assert str(target_dir / shortcuts.ON_SHORTCUT_NAME) in script
    assert str(target_dir / shortcuts.OFF_SHORTCUT_NAME) in script
    assert "share start" in script
    assert "share stop" in script
    assert str(caseclerk_bin) in script
    assert script.count("$s.Save()") == 2


def test_build_script_escapes_quotes_in_paths(tmp_path: Path) -> None:
    weird_dir = tmp_path / "O'Brien's Folder"
    caseclerk_bin = weird_dir / "caseclerk.exe"
    target_dir = weird_dir / "Desktop"

    script = shortcuts.build_script(caseclerk_bin, target_dir)

    assert "O''Brien''s Folder" in script  # escaped form present
    assert "O'Brien's Folder" not in script  # raw (unescaped) form never is


def test_create_shortcuts_raises_on_non_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(shortcuts.ShortcutsUnsupportedError, match="only supported on Windows"):
        shortcuts.create_shortcuts(caseclerk_bin=tmp_path / "caseclerk.exe")


def test_create_shortcuts_runs_powershell_and_returns_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    caseclerk_bin = tmp_path / "caseclerk.exe"
    target_dir = tmp_path / "Desktop"
    calls: list[list[str]] = []

    result = shortcuts.create_shortcuts(
        caseclerk_bin=caseclerk_bin, target_dir=target_dir, run=_fake_run_capturing(calls)
    )

    assert result == [
        target_dir / shortcuts.ON_SHORTCUT_NAME,
        target_dir / shortcuts.OFF_SHORTCUT_NAME,
    ]
    assert target_dir.is_dir()  # created even though nothing was actually written to it (run is faked)
    assert len(calls) == 1
    invocation = calls[0]
    assert invocation[0] == "powershell"
    assert "-File" in invocation
    script_path = Path(invocation[invocation.index("-File") + 1])
    # the temp script is cleaned up after running (TemporaryDirectory context exits)
    assert not script_path.exists()


def test_create_shortcuts_defaults_to_desktop_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shortcuts, "desktop_dir", lambda: tmp_path / "Desktop")
    calls: list[list[str]] = []

    result = shortcuts.create_shortcuts(
        caseclerk_bin=tmp_path / "caseclerk.exe", run=_fake_run_capturing(calls)
    )

    assert result[0].parent == tmp_path / "Desktop"
