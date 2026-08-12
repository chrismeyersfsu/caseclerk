"""`caseclerk config get|set|path` -- dotted access into the JSON config file.

Dotted keys use the on-disk camelCase names (e.g. `processing.concurrency`,
`updates.checkIntervalHours`) so they match what a user editing config.json
by hand would see.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from caseclerk_core.config import Config, config_path, load_config, save_config

app = typer.Typer(help="Read or update the caseclerk config file.")


def _get_dotted(data: dict[str, Any], dotted_key: str) -> Any:
    node: Any = data
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted_key)
        node = node[part]
    return node


def _set_dotted(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = dotted_key.split(".")
    node = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(dotted_key)
        node = node[part]
    if parts[-1] not in node:
        raise KeyError(dotted_key)
    node[parts[-1]] = value


def _coerce_like(current: Any, raw: str) -> Any:
    if isinstance(current, bool):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, list):
        return [item.strip() for item in raw.split(",") if item.strip()]
    return raw


@app.command("path")
def config_path_cmd() -> None:
    """Print the config file's path (it may not exist yet)."""
    typer.echo(str(config_path()))


@app.command("get")
def config_get(key: str) -> None:
    """Print one dotted config key's effective value, e.g. `processing.concurrency`."""
    data = load_config().model_dump(mode="json", by_alias=True)
    try:
        value = _get_dotted(data, key)
    except KeyError:
        typer.echo(f"Unknown config key '{key}'.", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(value if isinstance(value, str) else json.dumps(value))


@app.command("set")
def config_set(key: str, value: str) -> None:
    """Set one dotted config key and save. Existing effective config fills in the rest."""
    data = load_config().model_dump(mode="json", by_alias=True)
    try:
        current = _get_dotted(data, key)
    except KeyError:
        typer.echo(f"Unknown config key '{key}'.", err=True)
        raise typer.Exit(code=1) from None
    coerced = _coerce_like(current, value)
    _set_dotted(data, key, coerced)
    saved_path = save_config(Config.model_validate(data))
    typer.echo(f"Set {key} = {coerced!r} in {saved_path}")
