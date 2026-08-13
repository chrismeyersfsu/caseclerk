"""Pure(-ish) tray state: a `TrayState` snapshot built from core/cli data, and
a `MenuItem` list (the menu MODEL) built from that snapshot. No pystray or
tkinter import anywhere in this module -- `collect_state`/`build_menu_model`
are plain functions of their inputs, so they're exercised directly in unit
tests (fabricated ``TrayState``s, a real sqlite3 connection against a tmp
dir) without any GUI toolkit involved. `ui_tray.py`'s polling loop is the
only caller that feeds real IO (a live db connection, the real autostart
check) into `collect_state`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from caseclerk_cli import share as share_module
from caseclerk_core import binary_update, db
from caseclerk_core import update as core_update
from caseclerk_core.config import Config


@dataclass(frozen=True)
class TrayState:
    sharing_on: bool
    share_hostname: str | None
    share_port: int
    processing_total: int
    processing_by_state: dict[str, int]
    failure_count: int
    current_version: str
    update_available_version: str | None
    update_staged: bool
    # None means "autostart is unsupported on this platform" (see
    # autostart.is_enabled); the menu/settings UI omits the control entirely
    # rather than show a control that can never do anything.
    autostart_enabled: bool | None = None


@dataclass(frozen=True)
class MenuItem:
    """One row of the tray icon's context menu. `separator=True` items ignore
    every other field; a non-separator item with `enabled=False` renders as a
    plain (unclickable) label -- used for the "Sharing: ON/OFF" status line.
    `is_checkbox=True` is what makes `checked` meaningful -- e.g. the
    "Start CaseClerk when Windows starts" item; every other item ignores
    `checked` and renders as a plain (non-checkable) action."""

    action_id: str
    label: str
    checked: bool = False
    is_checkbox: bool = False
    enabled: bool = True
    separator: bool = False


ACTION_TOGGLE_SHARING = "toggle_sharing"
ACTION_OPEN_STATUS = "open_status"
ACTION_OPEN_SETTINGS = "open_settings"
ACTION_OPEN_AUDIT = "open_audit"
ACTION_TOGGLE_AUTOSTART = "toggle_autostart"
ACTION_CHECK_UPDATES = "check_updates"
ACTION_RESTART_TO_UPDATE = "restart_to_update"
ACTION_QUIT = "quit"


def collect_state(conn: sqlite3.Connection, cfg: Config, *, autostart_enabled: bool | None) -> TrayState:
    """Gather everything the tray needs to display from already-open
    resources: a db connection, the loaded config, and a pre-computed
    autostart flag (autostart.is_enabled() is an OS/registry call, so the
    caller supplies its result rather than this module importing winreg).
    """
    counts = db.status_counts(conn)
    failures = db.list_failures(conn)
    cached_update = db.get_meta(conn, core_update.META_AVAILABLE_VERSION)
    staged = binary_update.is_frozen() and binary_update.has_staged_update()

    return TrayState(
        sharing_on=share_module.is_running(),
        share_hostname=cfg.share.hostname,
        share_port=cfg.share.port,
        processing_total=counts.total,
        processing_by_state={state.value: count for state, count in counts.by_state.items()},
        failure_count=len(failures),
        current_version=core_update.current_version(),
        update_available_version=cached_update or None,
        update_staged=staged,
        autostart_enabled=autostart_enabled,
    )


def _sharing_line(state: TrayState) -> MenuItem:
    if not state.sharing_on:
        label = "Sharing: OFF"
    elif state.share_hostname:
        label = f"Sharing: ON ({state.share_hostname})"
    else:
        label = "Sharing: ON"
    return MenuItem(action_id="", label=label, enabled=False)


def _processing_line(state: TrayState) -> MenuItem:
    label = f"Processing: {state.processing_total} document(s)"
    if state.failure_count:
        label += f", {state.failure_count} failed"
    return MenuItem(action_id="", label=label, enabled=False)


def build_menu_model(state: TrayState) -> list[MenuItem]:
    """The tray icon's context menu, entirely determined by `state` -- same
    inputs, same menu, every time."""
    items: list[MenuItem] = [
        _sharing_line(state),
        _processing_line(state),
        MenuItem(action_id="", label="", separator=True),
        MenuItem(
            action_id=ACTION_TOGGLE_SHARING,
            label="Stop Sharing" if state.sharing_on else "Start Sharing",
        ),
        MenuItem(action_id=ACTION_OPEN_STATUS, label="Status..."),
        MenuItem(action_id=ACTION_OPEN_SETTINGS, label="Settings..."),
        MenuItem(action_id=ACTION_OPEN_AUDIT, label="Audit Log..."),
        MenuItem(action_id="", label="", separator=True),
    ]

    if state.autostart_enabled is not None:
        items.append(
            MenuItem(
                action_id=ACTION_TOGGLE_AUTOSTART,
                label="Start CaseClerk when Windows starts",
                checked=state.autostart_enabled,
                is_checkbox=True,
            )
        )

    update_label = "Check for Updates..."
    if state.update_available_version and not state.update_staged:
        update_label = f"Check for Updates... ({state.update_available_version} available)"
    items.append(MenuItem(action_id=ACTION_CHECK_UPDATES, label=update_label))

    if state.update_staged:
        items.append(MenuItem(action_id=ACTION_RESTART_TO_UPDATE, label="Restart to Apply Update"))

    items.append(MenuItem(action_id="", label="", separator=True))
    items.append(MenuItem(action_id=ACTION_QUIT, label="Quit"))
    return items
