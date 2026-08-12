from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from caseclerk_cli import claude_desktop, main
from caseclerk_cli.main import app
from caseclerk_core.discovery import DiscoveryCandidate
from caseclerk_fixtures import build_fixture_drive


def _fake_discover(candidate: Path, score: int = 4) -> Callable[..., list[DiscoveryCandidate]]:
    def _discover(*_args: object, **_kwargs: object) -> list[DiscoveryCandidate]:
        return [DiscoveryCandidate(path=candidate, score=score)]

    return _discover


def test_init_yes_writes_config_from_top_candidate(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clio_root = build_fixture_drive(isolated_env / "clio")
    monkeypatch.setattr(main, "discover", _fake_discover(clio_root))

    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 0, result.output
    assert "claude mcp add caseclerk -- caseclerk serve" in result.output

    config_file = Path(runner.invoke(app, ["config", "path"]).output.strip())
    data = json.loads(config_file.read_text())
    assert data["clioRoot"] == str(clio_root)


def test_init_no_candidates_fails_cleanly(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "discover", lambda *_a, **_k: [])
    result = runner.invoke(app, ["init", "--yes"])
    assert result.exit_code == 1
    assert "No Clio Drive candidates found" in result.output


def test_init_declines_without_yes_when_not_confirmed(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clio_root = build_fixture_drive(isolated_env / "clio")
    monkeypatch.setattr(main, "discover", _fake_discover(clio_root))

    result = runner.invoke(app, ["init"], input="n\n")
    assert result.exit_code == 1

    config_file = Path(runner.invoke(app, ["config", "path"]).output.strip())
    assert not config_file.exists()


def test_init_write_claude_config_flag_writes_and_merges(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    clio_root = build_fixture_drive(isolated_env / "clio")
    monkeypatch.setattr(main, "discover", _fake_discover(clio_root))

    fake_claude_config = tmp_path / "claude" / "claude_desktop_config.json"
    fake_claude_config.parent.mkdir(parents=True)
    fake_claude_config.write_text(json.dumps({"mcpServers": {"other-server": {"command": "other"}}}))
    monkeypatch.setattr(claude_desktop, "claude_desktop_config_path", lambda *_a, **_k: fake_claude_config)

    result = runner.invoke(app, ["init", "--yes", "--write-claude-config"])
    assert result.exit_code == 0, result.output

    data = json.loads(fake_claude_config.read_text())
    assert data["mcpServers"]["caseclerk"]["command"] == "caseclerk"
    assert data["mcpServers"]["caseclerk"]["args"] == ["serve"]
    assert "other-server" in data["mcpServers"]  # existing entries are preserved, not clobbered
