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
    a lightweight guard: everything else on stdout should be the logging
    module's own INFO lines (which pytest/logging.basicConfig sends to
    stderr by default), not stray print() calls."""
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
