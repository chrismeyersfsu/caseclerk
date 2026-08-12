from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from caseclerk_cli.main import app


def test_serve_rejects_unknown_transport(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["serve", "--transport", "carrier-pigeon"])
    assert result.exit_code == 1
    assert "Unknown --transport" in result.output


def test_serve_has_no_host_option(runner: CliRunner, isolated_env: Path) -> None:
    """--host must not exist at all -- the HTTP transport is 127.0.0.1-only, hard-coded."""
    result = runner.invoke(app, ["serve", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()
