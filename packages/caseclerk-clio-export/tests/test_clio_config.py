from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from caseclerk_clio_export.config import Credentials, credentials_path


def test_save_and_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLIO_EXPORT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLIO_EXPORT_BASE_URL", raising=False)
    creds = Credentials(
        client_id="cid",
        client_secret="sec",
        region="us",
        token={"access_token": "at", "refresh_token": "rt"},
    )
    path = creds.save()

    assert path == credentials_path() == tmp_path / "credentials.json"
    if os.name == "posix":  # Windows has no POSIX modes
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    loaded = Credentials.load()
    assert loaded.client_id == "cid"
    assert loaded.token["refresh_token"] == "rt"
    assert loaded.base_url == "https://app.clio.com"
