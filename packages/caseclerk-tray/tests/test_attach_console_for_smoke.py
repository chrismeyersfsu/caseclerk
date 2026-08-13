"""Unit coverage for the decision seam in __main__._attach_console_for_smoke:
whether GetStdHandle already shows a valid target to write to. The real
Win32 calls (WinDLL, AttachConsole, the CONOUT$ open) are Windows-only and
can only really be proven on real Windows CI -- see the release-run
failures __main__.py's module docstring/comments describe -- but the
decision logic itself (given what GetStdHandle returns, do we conclude
"already have somewhere valid") is plain Python, testable everywhere via an
injected double standing in for kernel32.
"""

from __future__ import annotations

import io
import logging
import sys

import pytest

from caseclerk_tray import __main__ as tray_main


class _FakeKernel32:
    def __init__(self, *, std_handle: int) -> None:
        self._std_handle = std_handle
        self.calls: list[int] = []

    def GetStdHandle(self, which: int) -> int:  # noqa: N802
        self.calls.append(which)
        return self._std_handle


def test_valid_handle_reported_when_getstdhandle_returns_nonzero() -> None:
    """A real console, or (as in CI) a redirected pipe/file -- either way,
    GetStdHandle returning something nonzero means there's already
    somewhere real to write, so no attach/rebind should happen."""
    fake = _FakeKernel32(std_handle=1234)
    assert tray_main._has_valid_stdio_handle(fake) is True
    assert fake.calls == [tray_main._STD_OUTPUT_HANDLE]


def test_no_valid_handle_when_getstdhandle_returns_zero() -> None:
    """A genuine double-click launch (console=False, no redirection): no
    console, no redirected handle -- GetStdHandle correctly reports nothing,
    which is exactly when attaching a console (if a parent one exists) is
    appropriate."""
    fake = _FakeKernel32(std_handle=0)
    assert tray_main._has_valid_stdio_handle(fake) is False


def test_no_valid_handle_when_getstdhandle_returns_invalid_handle_value() -> None:
    """INVALID_HANDLE_VALUE (all bits set) is GetStdHandle's other "nothing
    here" answer, distinct from NULL (0) but equally not a real target to
    write to -- checked in both the signed (-1) and the unsigned 64-bit
    representation ctypes' c_void_p restype actually surfaces a non-null
    pointer as. A naive `bool(handle)` truthiness check would get this
    wrong (both values are nonzero, hence truthy) -- exactly the kind of
    latent stream assumption this module's whole rewrite was prompted by."""
    for invalid_value in (-1, 0xFFFFFFFFFFFFFFFF):
        fake = _FakeKernel32(std_handle=invalid_value)
        assert tray_main._has_valid_stdio_handle(fake) is False


def test_has_valid_stdio_handle_only_calls_getstdhandle_once() -> None:
    fake = _FakeKernel32(std_handle=42)
    tray_main._has_valid_stdio_handle(fake)
    assert len(fake.calls) == 1


# --- _configure_logging: must never hand logging.basicConfig a None stream -
# a console=False frozen build launched with no console and no redirection
# can have sys.stderr as literally None (older PyInstaller bootloaders), and
# logging.StreamHandler crashes on its first emit() against a None stream.


def test_configure_logging_falls_back_when_stderr_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stderr", None)
    captured: dict[str, object] = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))

    tray_main._configure_logging(level=logging.WARNING)

    assert captured["stream"] is not None
    assert captured["level"] == logging.WARNING


def test_configure_logging_uses_the_real_stderr_when_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", fake_stderr)
    captured: dict[str, object] = {}
    monkeypatch.setattr(logging, "basicConfig", lambda **kwargs: captured.update(kwargs))

    tray_main._configure_logging(level=logging.INFO)

    assert captured["stream"] is fake_stderr
