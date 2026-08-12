import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("CASECLERK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CASECLERK_DATA_DIR", str(tmp_path / "data"))
    for key in list(os.environ):
        if key.startswith("CASECLERK_") and key not in ("CASECLERK_CONFIG_DIR", "CASECLERK_DATA_DIR"):
            monkeypatch.delenv(key, raising=False)
    yield tmp_path
