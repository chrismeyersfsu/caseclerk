from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from caseclerk_cli import main as main_module
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


def test_doctor_skips_cloudflared_check_when_share_not_configured(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents_root = build_fixture_drive(isolated_env / "documents")
    runner.invoke(app, ["config", "set", "documentsRoot", str(documents_root)])
    monkeypatch.setattr(
        main_module.shutil, "which", lambda name: None if name == "cloudflared" else "/bin/true"
    )

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    # not a bare "cloudflared" substring check: pytest's tmp_path is named after this
    # test function, so it would contain "cloudflared" regardless of doctor's output
    assert "]   cloudflared" not in result.output
    assert "cloudflared is not on PATH" not in result.output


def test_doctor_checks_cloudflared_when_share_hostname_configured(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    documents_root = build_fixture_drive(isolated_env / "documents")
    runner.invoke(app, ["config", "set", "documentsRoot", str(documents_root)])
    runner.invoke(app, ["config", "set", "share.hostname", "caseclerk.example.com"])
    monkeypatch.setattr(
        main_module.shutil, "which", lambda name: None if name == "cloudflared" else "/bin/true"
    )

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "[FAIL] cloudflared is not on PATH" in result.output
