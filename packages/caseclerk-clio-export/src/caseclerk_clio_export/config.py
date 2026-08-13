"""Configuration and credential storage for clio-export."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REGION_BASE_URLS = {
    "us": "https://app.clio.com",
    "eu": "https://eu.app.clio.com",
    "ca": "https://ca.app.clio.com",
    "au": "https://au.app.clio.com",
}

DEFAULT_CALLBACK_PORT = 8788


def config_dir() -> Path:
    override = os.environ.get("CLIO_EXPORT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path("~/.config/clio-export").expanduser()


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


@dataclass
class Credentials:
    client_id: str
    client_secret: str
    region: str = "us"
    token: dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        # CLIO_EXPORT_BASE_URL lets tests point at a mock Clio server.
        return os.environ.get("CLIO_EXPORT_BASE_URL") or REGION_BASE_URLS[self.region]

    @classmethod
    def load(cls, path: Path | None = None) -> Credentials:
        path = path or credentials_path()
        if not path.exists():
            raise FileNotFoundError(f"No credentials at {path}. Run `clio-export auth` first.")
        data = json.loads(path.read_text())
        return cls(
            client_id=data["client_id"],
            client_secret=data["client_secret"],
            region=data.get("region", "us"),
            token=data.get("token", {}),
        )

    def save(self, path: Path | None = None) -> Path:
        path = path or credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "region": self.region,
            "token": self.token,
        }
        path.write_text(json.dumps(payload, indent=2) + "\n")
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # contains OAuth secrets
        return path
