"""Plain text / markdown passthrough extraction."""

from __future__ import annotations

from pathlib import Path


def extract_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")
