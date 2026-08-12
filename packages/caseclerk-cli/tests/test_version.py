from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from caseclerk_cli.main import app


def test_version_flag_prints_version_and_exits_zero(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip()
    # a bare version string, not e.g. a stack trace or usage text
    assert "Usage" not in result.output
    assert result.output.strip().count(".") >= 2
