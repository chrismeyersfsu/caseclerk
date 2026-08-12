from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from caseclerk_cli.main import app
from caseclerk_fixtures import build_fixture_drive


def test_doctor_all_ok(runner: CliRunner, isolated_env: Path) -> None:
    documents_root = build_fixture_drive(isolated_env / "documents")
    runner.invoke(app, ["config", "set", "documentsRoot", str(documents_root)])

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "[FAIL]" not in result.output
    assert "All checks passed." in result.output


def test_doctor_reports_missing_documents_root(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] documentsRoot is not set" in result.output
