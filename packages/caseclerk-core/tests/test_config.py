import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from caseclerk_core.config import Config, config_dir, config_path, data_dir, load_config, save_config


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CASECLERK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CASECLERK_DATA_DIR", str(tmp_path / "data"))
    for key in list(os.environ):
        if key.startswith("CASECLERK_") and key not in ("CASECLERK_CONFIG_DIR", "CASECLERK_DATA_DIR"):
            monkeypatch.delenv(key, raising=False)
    yield


def test_defaults_when_no_file_present() -> None:
    cfg = load_config()
    assert cfg.documents_root is None
    assert cfg.emails_folder_name == "emails-generated"
    assert cfg.email_file_name_template == "{yyyy}-{mm}-{dd}-{slug}"
    assert cfg.processing.concurrency == 2
    assert cfg.processing.watch is True
    assert cfg.updates.auto is True
    assert cfg.updates.check_interval_hours == 24
    assert cfg.summarization.enabled is False


def test_file_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"documentsRoot": "/mnt/documents", "processing": {"concurrency": 4}}))

    cfg = load_config()
    assert cfg.documents_root == "/mnt/documents"
    assert cfg.processing.concurrency == 4
    assert cfg.processing.watch is True  # unspecified nested field keeps its default


def test_env_overrides_file(monkeypatch: pytest.MonkeyPatch) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"documentsRoot": "/mnt/documents", "processing": {"concurrency": 4}}))

    monkeypatch.setenv("CASECLERK_DOCUMENTS_ROOT", "/mnt/env-wins")
    monkeypatch.setenv("CASECLERK_PROCESSING_CONCURRENCY", "8")
    monkeypatch.setenv("CASECLERK_UPDATES_AUTO", "false")
    monkeypatch.setenv("CASECLERK_PROCESSING_IGNORE", "a/**, b/**")

    cfg = load_config()
    assert cfg.documents_root == "/mnt/env-wins"
    assert cfg.processing.concurrency == 8
    assert cfg.updates.auto is False
    assert cfg.processing.ignore == ["a/**", "b/**"]


def test_save_and_reload_roundtrip() -> None:
    cfg = Config(documents_root="/mnt/documents", emails_folder_name="drafts")
    saved_path = save_config(cfg)
    assert saved_path == config_path()

    on_disk = json.loads(config_path().read_text())
    assert on_disk["documentsRoot"] == "/mnt/documents"
    assert on_disk["emailsFolderName"] == "drafts"
    assert "checkIntervalHours" in on_disk["updates"]

    reloaded = load_config()
    assert reloaded.documents_root == "/mnt/documents"
    assert reloaded.emails_folder_name == "drafts"


def test_config_dir_and_data_dir_env_overrides_are_isolated(tmp_path: Path) -> None:
    assert config_dir() == tmp_path / "config"
    assert data_dir() == tmp_path / "data"
