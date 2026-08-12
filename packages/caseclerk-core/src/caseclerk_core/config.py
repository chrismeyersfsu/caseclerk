"""CaseClerk configuration: JSON file on disk, pydantic model in memory.

Precedence, low to high: model defaults < config file < ``CASECLERK_*``
env vars. ``CASECLERK_CONFIG_DIR`` and ``CASECLERK_DATA_DIR`` are a
separate mechanism (they relocate the file/db, not override a config
value) so tests never touch the real user config/data directories.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import platformdirs
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

APP_NAME = "caseclerk"
CONFIG_DIR_ENV = "CASECLERK_CONFIG_DIR"
DATA_DIR_ENV = "CASECLERK_DATA_DIR"
CONFIG_FILE_NAME = "config.json"


def config_dir() -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    return Path(override) if override else Path(platformdirs.user_config_dir(APP_NAME))


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override) if override else Path(platformdirs.user_data_dir(APP_NAME))


def config_path() -> Path:
    return config_dir() / CONFIG_FILE_NAME


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class ProcessingConfig(_CamelModel):
    concurrency: int = 2
    watch: bool = True
    ignore: list[str] = Field(default_factory=lambda: ["emails-generated/**"])


class UpdatesConfig(_CamelModel):
    auto: bool = True
    check_interval_hours: int = 24


class SummarizationConfig(_CamelModel):
    enabled: bool = False
    provider: str = "anthropic"
    base_url: str = ""
    api_key_env: str = ""
    model: str = ""


class ShareConfig(_CamelModel):
    """Remote access over HTTP + a cloudflared named tunnel; see `caseclerk share`."""

    hostname: str | None = None
    port: int = 8787
    tunnel_name: str = "caseclerk"


class Config(_CamelModel):
    documents_root: str | None = None
    emails_folder_name: str = "emails-generated"
    email_file_name_template: str = "{yyyy}-{mm}-{dd}-{slug}"
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    share: ShareConfig = Field(default_factory=ShareConfig)
    prompts_dir: str | None = None


# env var name -> dotted field path (snake_case, matching model field names)
_ENV_FIELDS: dict[str, tuple[str, ...]] = {
    "CASECLERK_DOCUMENTS_ROOT": ("documents_root",),
    "CASECLERK_EMAILS_FOLDER_NAME": ("emails_folder_name",),
    "CASECLERK_EMAIL_FILE_NAME_TEMPLATE": ("email_file_name_template",),
    "CASECLERK_PROCESSING_CONCURRENCY": ("processing", "concurrency"),
    "CASECLERK_PROCESSING_WATCH": ("processing", "watch"),
    "CASECLERK_PROCESSING_IGNORE": ("processing", "ignore"),
    "CASECLERK_UPDATES_AUTO": ("updates", "auto"),
    "CASECLERK_UPDATES_CHECK_INTERVAL_HOURS": ("updates", "check_interval_hours"),
    "CASECLERK_SUMMARIZATION_ENABLED": ("summarization", "enabled"),
    "CASECLERK_SUMMARIZATION_PROVIDER": ("summarization", "provider"),
    "CASECLERK_SUMMARIZATION_BASE_URL": ("summarization", "base_url"),
    "CASECLERK_SUMMARIZATION_API_KEY_ENV": ("summarization", "api_key_env"),
    "CASECLERK_SUMMARIZATION_MODEL": ("summarization", "model"),
    "CASECLERK_SHARE_HOSTNAME": ("share", "hostname"),
    "CASECLERK_SHARE_PORT": ("share", "port"),
    "CASECLERK_SHARE_TUNNEL_NAME": ("share", "tunnel_name"),
    "CASECLERK_PROMPTS_DIR": ("prompts_dir",),
}


def _set_path(node: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value


def _env_overrides() -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for env_name, path in _ENV_FIELDS.items():
        raw = os.environ.get(env_name)
        if raw is None:
            continue
        value: Any = raw
        if path[-1] == "ignore":
            value = [item.strip() for item in raw.split(",") if item.strip()]
        _set_path(overrides, path, value)
    return overrides


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config() -> Config:
    """Load config applying file < env precedence; missing file is not an error."""
    path = config_path()
    file_data: dict[str, Any] = {}
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"Config file {path} must contain a JSON object")
        file_data = loaded

    with_file_layer = Config.model_validate(file_data).model_dump()
    merged = _deep_merge(with_file_layer, _env_overrides())
    return Config.model_validate(merged)


def save_config(config: Config) -> Path:
    """Write config back to disk as camelCase JSON, creating parent dirs as needed."""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = config.model_dump(mode="json", by_alias=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
