from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from caseclerk_core.config import load_config
from caseclerk_tray import windows


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Local fixture rather than a shared tests/conftest.py -- see the longer
    # explanation in test_state.py's copy of this fixture (a same-named
    # conftest.py collides across packages under a whole-tree mypy sweep).
    monkeypatch.setenv("CASECLERK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CASECLERK_DATA_DIR", str(tmp_path / "data"))
    for key in list(os.environ):
        if key.startswith("CASECLERK_") and key not in ("CASECLERK_CONFIG_DIR", "CASECLERK_DATA_DIR"):
            monkeypatch.delenv(key, raising=False)
    yield tmp_path


def _values(**overrides: object) -> windows.SettingsFormValues:
    defaults: dict[str, object] = {
        "documents_root": "",
        "emails_folder_name": "emails-generated",
        "email_file_name_template": "{yyyy}-{mm}-{dd}-{slug}",
        "processing_concurrency": "2",
        "autostart_enabled": False,
    }
    defaults.update(overrides)
    return windows.SettingsFormValues(**defaults)  # type: ignore[arg-type]


def test_validate_settings_requires_documents_root(isolated_env: Path) -> None:
    cfg, errors = windows.validate_settings(_values(documents_root=""))
    assert cfg is None
    assert any("Documents folder is required" in e for e in errors)


def test_validate_settings_rejects_nonexistent_documents_root(isolated_env: Path) -> None:
    missing = isolated_env / "does-not-exist"
    cfg, errors = windows.validate_settings(_values(documents_root=str(missing)))
    assert cfg is None
    assert any("does not exist" in e for e in errors)


def test_validate_settings_rejects_empty_emails_folder_name(isolated_env: Path) -> None:
    docs = isolated_env / "docs"
    docs.mkdir()
    cfg, errors = windows.validate_settings(_values(documents_root=str(docs), emails_folder_name="   "))
    assert cfg is None
    assert any("Emails folder name is required" in e for e in errors)


def test_validate_settings_rejects_empty_email_template(isolated_env: Path) -> None:
    docs = isolated_env / "docs"
    docs.mkdir()
    cfg, errors = windows.validate_settings(_values(documents_root=str(docs), email_file_name_template=""))
    assert cfg is None
    assert any("template is required" in e for e in errors)


def test_validate_settings_rejects_non_numeric_concurrency(isolated_env: Path) -> None:
    docs = isolated_env / "docs"
    docs.mkdir()
    cfg, errors = windows.validate_settings(_values(documents_root=str(docs), processing_concurrency="abc"))
    assert cfg is None
    assert any("whole number" in e for e in errors)


def test_validate_settings_rejects_zero_or_negative_concurrency(isolated_env: Path) -> None:
    docs = isolated_env / "docs"
    docs.mkdir()
    cfg, errors = windows.validate_settings(_values(documents_root=str(docs), processing_concurrency="0"))
    assert cfg is None
    assert any("at least 1" in e for e in errors)


def test_validate_settings_success_returns_updated_config(isolated_env: Path) -> None:
    docs = isolated_env / "docs"
    docs.mkdir()
    cfg, errors = windows.validate_settings(
        _values(
            documents_root=str(docs),
            emails_folder_name="emails-out",
            email_file_name_template="{yyyy}-{slug}",
            processing_concurrency="4",
        )
    )
    assert errors == []
    assert cfg is not None
    assert cfg.documents_root == str(docs)
    assert cfg.emails_folder_name == "emails-out"
    assert cfg.email_file_name_template == "{yyyy}-{slug}"
    assert cfg.processing.concurrency == 4


def test_validate_settings_reports_multiple_errors_at_once(isolated_env: Path) -> None:
    cfg, errors = windows.validate_settings(
        _values(
            documents_root="", emails_folder_name="", email_file_name_template="", processing_concurrency="x"
        )
    )
    assert cfg is None
    assert len(errors) == 4


def test_save_settings_persists_config_on_success(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs = isolated_env / "docs"
    docs.mkdir()
    autostart_calls: list[bool] = []
    monkeypatch.setattr(windows.actions, "set_autostart", lambda enabled: autostart_calls.append(enabled))

    errors = windows.save_settings(_values(documents_root=str(docs), autostart_enabled=True))

    assert errors == []
    assert autostart_calls == [True]
    assert load_config().documents_root == str(docs)


def test_save_settings_does_not_touch_autostart_on_validation_failure(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail_if_called(enabled: bool) -> bool:
        raise AssertionError("autostart must not be touched when validation fails")

    monkeypatch.setattr(windows.actions, "set_autostart", _fail_if_called)

    errors = windows.save_settings(_values(documents_root=""))
    assert errors != []
