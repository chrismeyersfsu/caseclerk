"""Helpers for offering to wire caseclerk into Claude Desktop's config."""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any

SERVER_ENTRY: dict[str, Any] = {"mcpServers": {"caseclerk": {"command": "caseclerk", "args": ["serve"]}}}


def claude_desktop_config_path(system: str | None = None) -> Path:
    system = system or platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def write_claude_desktop_entry(path: Path | None = None) -> Path:
    """Merge the caseclerk mcpServers entry into an existing config, preserving the rest."""
    target = path or claude_desktop_config_path()
    existing: dict[str, Any] = {}
    if target.is_file():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, json.JSONDecodeError):
            existing = {}

    servers = existing.setdefault("mcpServers", {})
    servers.update(SERVER_ENTRY["mcpServers"])

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    return target
