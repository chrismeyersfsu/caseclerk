from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from caseclerk_cli.main import app
from caseclerk_core import binary_update
from caseclerk_core import update as core_update


def test_update_reports_none_available(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_update, "fetch_latest_release_tag", lambda client=None: None)
    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "No update available." in result.output


def test_update_applies_when_a_newer_release_exists(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_update, "fetch_latest_release_tag", lambda client=None: "v99.0.0")
    monkeypatch.setattr(core_update, "current_version", lambda distribution=None: "1.0.0")
    captured: dict[str, str] = {}
    monkeypatch.setattr(core_update, "apply_update", lambda tag, **kwargs: captured.setdefault("tag", tag))

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "Update available: v99.0.0" in result.output
    assert captured["tag"] == "v99.0.0"


def test_update_never_applies_when_already_current(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_update, "fetch_latest_release_tag", lambda client=None: "v1.0.0")
    monkeypatch.setattr(core_update, "current_version", lambda distribution=None: "1.0.0")

    def _fail_apply(tag: str, **kwargs: object) -> None:
        raise AssertionError("apply_update must not run when already current")

    monkeypatch.setattr(core_update, "apply_update", _fail_apply)

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "No update available." in result.output


def test_update_reports_a_successful_binary_swap(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_update, "fetch_latest_release_tag", lambda client=None: "v99.0.0")
    monkeypatch.setattr(core_update, "current_version", lambda distribution=None: "1.0.0")
    monkeypatch.setattr(
        core_update,
        "apply_update",
        lambda tag, **kwargs: binary_update.BinaryUpdateResult(
            ok=True, detail=f"Updated to {tag}. Restart caseclerk to use it."
        ),
    )

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 0
    assert "Updated to v99.0.0. Restart caseclerk to use it." in result.output


def test_update_reports_a_failed_binary_swap_with_nonzero_exit(
    runner: CliRunner, isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(core_update, "fetch_latest_release_tag", lambda client=None: "v99.0.0")
    monkeypatch.setattr(core_update, "current_version", lambda distribution=None: "1.0.0")
    monkeypatch.setattr(
        core_update,
        "apply_update",
        lambda tag, **kwargs: binary_update.BinaryUpdateResult(
            ok=False, detail="update failed; download manually: https://example.com"
        ),
    )

    result = runner.invoke(app, ["update"])
    assert result.exit_code == 1
    assert "download manually" in result.output
