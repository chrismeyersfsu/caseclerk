from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from caseclerk_cli.main import app


def test_config_path_get_set_roundtrip(runner: CliRunner, isolated_env: Path) -> None:
    path_result = runner.invoke(app, ["config", "path"])
    assert path_result.exit_code == 0
    config_file = Path(path_result.output.strip())
    assert config_file.parent == isolated_env / "config"

    get_result = runner.invoke(app, ["config", "get", "processing.concurrency"])
    assert get_result.exit_code == 0
    assert get_result.output.strip() == "2"

    set_result = runner.invoke(app, ["config", "set", "processing.concurrency", "5"])
    assert set_result.exit_code == 0

    get_after = runner.invoke(app, ["config", "get", "processing.concurrency"])
    assert get_after.output.strip() == "5"

    on_disk = json.loads(config_file.read_text())
    assert on_disk["processing"]["concurrency"] == 5


def test_config_set_top_level_string_key(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["config", "set", "documentsRoot", "/mnt/documents"])
    assert result.exit_code == 0
    get_result = runner.invoke(app, ["config", "get", "documentsRoot"])
    assert get_result.output.strip() == "/mnt/documents"


def test_config_set_bool_key(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["config", "set", "updates.auto", "false"])
    assert result.exit_code == 0
    get_result = runner.invoke(app, ["config", "get", "updates.auto"])
    assert get_result.output.strip() == "false"


def test_config_set_list_key(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["config", "set", "processing.ignore", "a/**, b/**"])
    assert result.exit_code == 0
    get_result = runner.invoke(app, ["config", "get", "processing.ignore"])
    assert json.loads(get_result.output.strip()) == ["a/**", "b/**"]


def test_config_get_unknown_key_errors_cleanly(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["config", "get", "nope.nope"])
    assert result.exit_code == 1
    assert "Unknown config key" in result.output


def test_config_set_unknown_key_errors_cleanly(runner: CliRunner, isolated_env: Path) -> None:
    result = runner.invoke(app, ["config", "set", "nope.nope", "x"])
    assert result.exit_code == 1
    assert "Unknown config key" in result.output
