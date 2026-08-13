from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from caseclerk_tray import singleinstance


def test_pidfile_fallback_first_caller_acquires(tmp_path: Path) -> None:
    pidfile = tmp_path / "tray.pid"
    assert singleinstance._acquire_pidfile(pidfile) is True
    assert pidfile.is_file()
    assert int(pidfile.read_text(encoding="utf-8").strip()) == os.getpid()


def test_pidfile_fallback_second_caller_blocked_while_owner_alive(tmp_path: Path) -> None:
    pidfile = tmp_path / "tray.pid"
    # A pidfile recording our OWN pid simulates "the owning process is still
    # alive" without needing to spawn a real second process.
    pidfile.write_text(str(os.getpid()), encoding="utf-8")

    assert singleinstance._acquire_pidfile(pidfile) is False
    # unchanged -- acquire() must not steal the lock from a live owner
    assert int(pidfile.read_text(encoding="utf-8").strip()) == os.getpid()


def test_pidfile_fallback_reclaims_after_stale_pid(tmp_path: Path) -> None:
    pidfile = tmp_path / "tray.pid"
    # A pid essentially guaranteed not to be alive.
    stale_pid = 2**30
    pidfile.write_text(str(stale_pid), encoding="utf-8")

    assert singleinstance._acquire_pidfile(pidfile) is True
    assert int(pidfile.read_text(encoding="utf-8").strip()) == os.getpid()


def test_pidfile_fallback_handles_corrupt_contents(tmp_path: Path) -> None:
    pidfile = tmp_path / "tray.pid"
    pidfile.write_text("not-a-pid", encoding="utf-8")

    assert singleinstance._acquire_pidfile(pidfile) is True
    assert int(pidfile.read_text(encoding="utf-8").strip()) == os.getpid()


def test_pidfile_fallback_creates_parent_dirs(tmp_path: Path) -> None:
    pidfile = tmp_path / "nested" / "dir" / "tray.pid"
    assert singleinstance._acquire_pidfile(pidfile) is True
    assert pidfile.is_file()


def test_pid_alive_false_for_a_pid_that_does_not_exist() -> None:
    assert singleinstance._pid_alive(2**30) is False


def test_pid_alive_true_for_our_own_process() -> None:
    assert singleinstance._pid_alive(os.getpid()) is True


def test_acquire_uses_pidfile_fallback_on_non_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    pidfile = tmp_path / "tray.pid"
    assert singleinstance.acquire(pidfile_path=pidfile) is True
    assert pidfile.is_file()


# --- Windows mutex path, via an injected fake kernel32 (bypasses the
# platform check on purpose -- see _acquire_windows's docstring) ----------


class _FakeKernel32:
    """A minimal stand-in for kernel32.dll's two functions this module
    calls. Real `ctypes.WinDLL("kernel32")` only works on Windows."""

    def __init__(self, *, already_exists: bool = False) -> None:
        self.already_exists = already_exists
        self.created_names: list[str] = []
        self.closed_handles: list[int] = []
        self._next_handle = 100

    def CreateMutexW(self, attrs: object, initial_owner: object, name: str) -> int:  # noqa: N802
        self.created_names.append(name)
        self._next_handle += 1
        return self._next_handle

    def CloseHandle(self, handle: int) -> None:  # noqa: N802
        self.closed_handles.append(handle)


def test_acquire_windows_first_caller_gets_the_mutex(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeKernel32()
    monkeypatch.setattr("ctypes.get_last_error", lambda: 0, raising=False)
    assert singleinstance._acquire_windows(kernel32_module=fake) is True
    assert fake.created_names == [singleinstance.MUTEX_NAME]
    assert fake.closed_handles == []


def test_acquire_windows_second_caller_is_blocked_and_closes_its_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeKernel32()
    monkeypatch.setattr("ctypes.get_last_error", lambda: singleinstance._ERROR_ALREADY_EXISTS, raising=False)
    assert singleinstance._acquire_windows(kernel32_module=fake) is False
    # the second caller must not leak its own handle to the (not-owned) mutex
    assert len(fake.closed_handles) == 1
