from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_smoke_flag_exits_zero_and_prints_one_status_line(tmp_path: Path) -> None:
    env = dict(os.environ)
    env["CASECLERK_CONFIG_DIR"] = str(tmp_path / "config")
    env["CASECLERK_DATA_DIR"] = str(tmp_path / "data")

    result = subprocess.run(
        [sys.executable, "-m", "caseclerk_tray", "--smoke"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    status_lines = [line for line in result.stdout.splitlines() if line.startswith("caseclerk-tray ")]
    assert len(status_lines) == 1
    assert "sharing=off" in status_lines[0]
    assert "menu_items=" in status_lines[0]


def test_smoke_flag_never_prints_to_stdout_via_bare_print_elsewhere(tmp_path: Path) -> None:
    """Library code must never print to stdout outside the tray's one
    --smoke status line (typer/CLI output is separately exempted) -- this is
    a lightweight guard: --smoke mode runs at WARNING level specifically so
    routine INFO noise (e.g. db.py's migration log lines) can't land here or
    interleave with the status line, so stdout should be exactly one line,
    never stray print() calls or logging output."""
    env = dict(os.environ)
    env["CASECLERK_CONFIG_DIR"] = str(tmp_path / "config")
    env["CASECLERK_DATA_DIR"] = str(tmp_path / "data")

    result = subprocess.run(
        [sys.executable, "-m", "caseclerk_tray", "--smoke"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    stdout_lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(stdout_lines) == 1


def test_smoke_flag_also_emits_the_status_line_on_stderr(tmp_path: Path) -> None:
    """Belt-and-braces (see __main__._run_smoke): the status line is emitted
    on both stdout and stderr, so release.yml's merged-stream (`2>&1`) CI
    check still finds it even if some future Windows console/stream quirk
    ever breaks one of the two streams again. This is the cross-platform-
    testable half of that fix; the Windows-specific console/handle
    detection in _attach_console_for_smoke can only really be proven on real
    Windows CI (see test_attach_console_for_smoke.py's unit-tested seam and
    the module's own docstring)."""
    env = dict(os.environ)
    env["CASECLERK_CONFIG_DIR"] = str(tmp_path / "config")
    env["CASECLERK_DATA_DIR"] = str(tmp_path / "data")

    result = subprocess.run(
        [sys.executable, "-m", "caseclerk_tray", "--smoke"],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    stdout_status = next(line for line in result.stdout.splitlines() if line.startswith("caseclerk-tray "))
    stderr_status = next(line for line in result.stderr.splitlines() if line.startswith("caseclerk-tray "))
    assert stdout_status == stderr_status
