from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from caseclerk_cli.main import app
from caseclerk_fixtures import build_fixture_drive


def test_process_without_documents_root_fails_cleanly(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["process"])
    assert result.exit_code == 1
    assert "documentsRoot is not configured" in result.output


def test_process_status_failures_retry_workflow(runner: CliRunner, isolated_env: Path) -> None:
    documents_root = build_fixture_drive(isolated_env / "documents")
    set_result = runner.invoke(app, ["config", "set", "documentsRoot", str(documents_root)])
    assert set_result.exit_code == 0

    process_result = runner.invoke(app, ["process"])
    assert process_result.exit_code == 0, process_result.output
    assert "12 new" in process_result.output
    assert "Processed 12 job(s)." in process_result.output

    status_result = runner.invoke(app, ["status"])
    assert status_result.exit_code == 0
    assert "Total documents: 12" in status_result.output
    assert "indexed: 10" in status_result.output
    assert "failed: 1" in status_result.output
    assert "unsupported: 1" in status_result.output

    failures_result = runner.invoke(app, ["failures"])
    assert failures_result.exit_code == 0
    assert "scanned-exhibit.pdf" in failures_result.output
    assert "needs OCR" in failures_result.output

    failure_line = next(line for line in failures_result.output.splitlines() if "scanned-exhibit" in line)
    document_id = int(failure_line.split("]")[0].lstrip("["))

    retry_result = runner.invoke(app, ["retry", str(document_id)])
    assert retry_result.exit_code == 0
    assert f"Requeued document {document_id}." in retry_result.output

    status_after_retry = runner.invoke(app, ["status"])
    assert "pending: 1" in status_after_retry.output

    no_args_result = runner.invoke(app, ["retry"])
    assert no_args_result.exit_code == 1

    both_args_result = runner.invoke(app, ["retry", str(document_id), "--all-failed"])
    assert both_args_result.exit_code == 1

    all_failed_result = runner.invoke(app, ["retry", "--all-failed"])
    assert all_failed_result.exit_code == 0
    assert "Requeued 0 failed document(s)." in all_failed_result.output


def test_process_is_safe_to_run_twice(runner: CliRunner, isolated_env: Path) -> None:
    documents_root = build_fixture_drive(isolated_env / "documents")
    runner.invoke(app, ["config", "set", "documentsRoot", str(documents_root)])

    first = runner.invoke(app, ["process"])
    assert "12 new" in first.output

    second = runner.invoke(app, ["process"])
    assert second.exit_code == 0
    assert "12 new" not in second.output
    assert "0 new" in second.output
