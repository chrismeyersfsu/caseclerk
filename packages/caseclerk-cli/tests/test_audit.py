from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from caseclerk_cli.main import app
from caseclerk_core import db


def test_audit_empty(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0
    assert "No audit entries yet." in result.output


def test_audit_lists_entries_most_recent_first(runner: CliRunner, isolated_env: Path) -> None:
    conn = db.connect()
    try:
        db.insert_remote_request(conn, tool="list_clients", args_summary="{}", ok=True, error=None)
        db.insert_remote_request(conn, tool="search_case", args_summary="{}", ok=False, error="boom")
    finally:
        conn.close()

    result = runner.invoke(app, ["audit"])
    assert result.exit_code == 0
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert "search_case" in lines[0]
    assert "FAIL" in lines[0]
    assert "boom" in lines[0]
    assert "list_clients" in lines[1]
    assert "ok" in lines[1]


def test_audit_respects_limit(runner: CliRunner, isolated_env: Path) -> None:
    conn = db.connect()
    try:
        for i in range(5):
            db.insert_remote_request(conn, tool=f"tool-{i}", args_summary=None, ok=True, error=None)
    finally:
        conn.close()

    result = runner.invoke(app, ["audit", "--limit", "2"])
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 2
