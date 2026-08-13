from __future__ import annotations

import sys

import pytest

from caseclerk_tray import autostart


class _FakeWinreg:
    """A minimal stand-in for the stdlib `winreg` module -- real `winreg`
    only exists on Windows, so tests on any OS inject this instead (same
    pattern as caseclerk_core.binary_update's own tests)."""

    HKEY_CURRENT_USER = "HKEY_CURRENT_USER"
    KEY_READ = 0x1
    KEY_SET_VALUE = 0x2
    REG_SZ = 1

    def __init__(self, *, key_missing: bool = False) -> None:
        self.key_missing = key_missing
        self.values: dict[str, str] = {}
        self.closed_count = 0

    def OpenKey(self, hive: object, path: str, reserved: int, access: int) -> object:  # noqa: N802
        if self.key_missing:
            raise FileNotFoundError(f"no such key: {path}")
        return "fake-key-handle"

    def QueryValueEx(self, key: object, name: str) -> tuple[str, int]:  # noqa: N802
        assert key == "fake-key-handle"
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key: object, name: str, reserved: int, value_type: int, value: str) -> None:  # noqa: N802
        assert key == "fake-key-handle"
        assert value_type == self.REG_SZ
        self.values[name] = value

    def DeleteValue(self, key: object, name: str) -> None:  # noqa: N802
        assert key == "fake-key-handle"
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]

    def CloseKey(self, key: object) -> None:  # noqa: N802
        assert key == "fake-key-handle"
        self.closed_count += 1


def test_is_enabled_false_when_value_absent() -> None:
    fake = _FakeWinreg()
    assert autostart.is_enabled(winreg_module=fake) is False


def test_is_enabled_false_when_key_absent() -> None:
    fake = _FakeWinreg(key_missing=True)
    assert autostart.is_enabled(winreg_module=fake) is False


def test_enable_then_is_enabled_true() -> None:
    fake = _FakeWinreg()
    autostart.enable(winreg_module=fake)
    assert autostart.VALUE_NAME in fake.values
    assert autostart.is_enabled(winreg_module=fake) is True


def test_enable_writes_command_for_current_interpreter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    fake = _FakeWinreg()
    autostart.enable(winreg_module=fake)
    assert "-m caseclerk_tray" in fake.values[autostart.VALUE_NAME]
    assert "python3" in fake.values[autostart.VALUE_NAME]


def test_enable_frozen_writes_bare_exe_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/caseclerk/caseclerk-tray.exe")
    fake = _FakeWinreg()
    autostart.enable(winreg_module=fake)
    value = fake.values[autostart.VALUE_NAME]
    assert "-m caseclerk_tray" not in value
    assert "caseclerk-tray.exe" in value


def test_disable_removes_value() -> None:
    fake = _FakeWinreg()
    autostart.enable(winreg_module=fake)
    assert autostart.is_enabled(winreg_module=fake) is True
    autostart.disable(winreg_module=fake)
    assert autostart.is_enabled(winreg_module=fake) is False


def test_disable_when_never_enabled_is_a_noop() -> None:
    fake = _FakeWinreg()
    autostart.disable(winreg_module=fake)  # must not raise
    assert autostart.is_enabled(winreg_module=fake) is False


def test_disable_when_key_missing_is_a_noop() -> None:
    fake = _FakeWinreg(key_missing=True)
    autostart.disable(winreg_module=fake)  # must not raise


def test_enable_raises_unsupported_on_non_windows_without_injected_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(autostart.AutostartUnsupportedError):
        autostart.enable()


def test_disable_raises_unsupported_on_non_windows_without_injected_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(autostart.AutostartUnsupportedError):
        autostart.disable()


def test_is_enabled_none_on_non_windows_without_injected_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert autostart.is_enabled() is None
