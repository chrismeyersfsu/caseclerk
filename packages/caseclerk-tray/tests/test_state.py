from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from caseclerk_cli import share as share_module
from caseclerk_core import binary_update, db
from caseclerk_core.config import load_config
from caseclerk_core.models import DocumentState
from caseclerk_core.update import META_AVAILABLE_VERSION
from caseclerk_tray import state


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    # Deliberately a local fixture, not a shared tests/conftest.py: every
    # package's tests/ dir lacks __init__.py by design, which makes a
    # same-named "conftest" module collide across packages under a
    # whole-tree `mypy packages` sweep (mypy's exclude list only stops it
    # from being a duplicate ROOT target, not from being resolved as an
    # indirect import elsewhere -- a 7th same-named conftest.py flips which
    # package's "conftest" ambiently wins that resolution). Defining this
    # fixture per file that needs it avoids the collision entirely.
    monkeypatch.setenv("CASECLERK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("CASECLERK_DATA_DIR", str(tmp_path / "data"))
    for key in list(os.environ):
        if key.startswith("CASECLERK_") and key not in ("CASECLERK_CONFIG_DIR", "CASECLERK_DATA_DIR"):
            monkeypatch.delenv(key, raising=False)
    yield tmp_path


def _make_state(**overrides: object) -> state.TrayState:
    defaults: dict[str, object] = {
        "sharing_on": False,
        "share_hostname": None,
        "share_port": 8787,
        "processing_total": 0,
        "processing_by_state": {},
        "failure_count": 0,
        "current_version": "0.4.0",
        "update_available_version": None,
        "update_staged": False,
        "autostart_enabled": None,
    }
    defaults.update(overrides)
    return state.TrayState(**defaults)  # type: ignore[arg-type]


# --- build_menu_model: fabricated inputs, no IO ------------------------------


def test_menu_model_sharing_off_shows_start_action() -> None:
    model = state.build_menu_model(_make_state(sharing_on=False))
    actions = {item.action_id for item in model}
    assert state.ACTION_TOGGLE_SHARING in actions
    toggle = next(item for item in model if item.action_id == state.ACTION_TOGGLE_SHARING)
    assert toggle.label == "Start Sharing"
    sharing_line = model[0]
    assert sharing_line.label == "Sharing: OFF"
    assert sharing_line.enabled is False


def test_menu_model_sharing_on_shows_stop_action_and_hostname() -> None:
    model = state.build_menu_model(_make_state(sharing_on=True, share_hostname="caseclerk.example.com"))
    toggle = next(item for item in model if item.action_id == state.ACTION_TOGGLE_SHARING)
    assert toggle.label == "Stop Sharing"
    assert "caseclerk.example.com" in model[0].label


def test_menu_model_shows_failure_count() -> None:
    model = state.build_menu_model(_make_state(processing_total=5, failure_count=2))
    processing_line = model[1]
    assert "5 document(s)" in processing_line.label
    assert "2 failed" in processing_line.label


def test_menu_model_omits_failure_count_when_zero() -> None:
    model = state.build_menu_model(_make_state(processing_total=5, failure_count=0))
    assert "failed" not in model[1].label


def test_menu_model_autostart_none_omits_item() -> None:
    model = state.build_menu_model(_make_state(autostart_enabled=None))
    assert all(item.action_id != state.ACTION_TOGGLE_AUTOSTART for item in model)


def test_menu_model_autostart_present_and_checked_matches_state() -> None:
    model = state.build_menu_model(_make_state(autostart_enabled=True))
    item = next(i for i in model if i.action_id == state.ACTION_TOGGLE_AUTOSTART)
    assert item.is_checkbox is True
    assert item.checked is True

    model_off = state.build_menu_model(_make_state(autostart_enabled=False))
    item_off = next(i for i in model_off if i.action_id == state.ACTION_TOGGLE_AUTOSTART)
    assert item_off.checked is False


def test_menu_model_no_update_available_omits_restart_item() -> None:
    model = state.build_menu_model(_make_state(update_available_version=None, update_staged=False))
    assert all(item.action_id != state.ACTION_RESTART_TO_UPDATE for item in model)
    check_item = next(i for i in model if i.action_id == state.ACTION_CHECK_UPDATES)
    assert check_item.label == "Check for Updates..."


def test_menu_model_update_available_not_staged_shows_version_in_label() -> None:
    model = state.build_menu_model(_make_state(update_available_version="v9.9.9", update_staged=False))
    check_item = next(i for i in model if i.action_id == state.ACTION_CHECK_UPDATES)
    assert "v9.9.9" in check_item.label
    assert all(item.action_id != state.ACTION_RESTART_TO_UPDATE for item in model)


def test_menu_model_update_staged_shows_restart_item() -> None:
    model = state.build_menu_model(_make_state(update_available_version="v9.9.9", update_staged=True))
    assert any(item.action_id == state.ACTION_RESTART_TO_UPDATE for item in model)


def test_menu_model_ends_with_quit() -> None:
    model = state.build_menu_model(_make_state())
    assert model[-1].action_id == state.ACTION_QUIT
    assert model[-1].label == "Quit"


def test_menu_model_only_autostart_item_is_a_checkbox() -> None:
    model = state.build_menu_model(_make_state(autostart_enabled=True))
    checkbox_items = [item for item in model if item.is_checkbox]
    assert len(checkbox_items) == 1
    assert checkbox_items[0].action_id == state.ACTION_TOGGLE_AUTOSTART


# --- collect_state: real (tmp-dir) IO ----------------------------------------


def test_collect_state_reflects_db_and_config(isolated_env: Path) -> None:
    cfg = load_config()
    conn = db.connect()
    try:
        client_id = db.upsert_client(conn, "Acme")
        case_id = db.upsert_case(conn, client_id, "24-001", "Acme/24-001")
        doc_id = db.upsert_document(
            conn,
            case_id=case_id,
            rel_path="a.txt",
            file_name="a.txt",
            ext=".txt",
            size=1,
            mtime_ms=1,
            content_hash="h",
            state=DocumentState.FAILED,
        )
        db.set_document_state(conn, doc_id, DocumentState.FAILED, error="boom")
        db.set_meta(conn, META_AVAILABLE_VERSION, "v9.9.9")

        result = state.collect_state(conn, cfg, autostart_enabled=True)
    finally:
        conn.close()

    assert result.processing_total == 1
    assert result.failure_count == 1
    assert result.update_available_version == "v9.9.9"
    assert result.sharing_on is False
    assert result.autostart_enabled is True
    assert result.share_port == cfg.share.port


def test_collect_state_sharing_on_reflects_share_is_running(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(share_module, "is_running", lambda: True)
    cfg = load_config()
    conn = db.connect()
    try:
        result = state.collect_state(conn, cfg, autostart_enabled=None)
    finally:
        conn.close()
    assert result.sharing_on is True


def test_collect_state_update_staged_only_when_frozen(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(binary_update, "is_frozen", lambda: False)
    monkeypatch.setattr(binary_update, "has_staged_update", lambda: True)
    cfg = load_config()
    conn = db.connect()
    try:
        result = state.collect_state(conn, cfg, autostart_enabled=None)
    finally:
        conn.close()
    assert result.update_staged is False

    monkeypatch.setattr(binary_update, "is_frozen", lambda: True)
    conn = db.connect()
    try:
        result = state.collect_state(conn, cfg, autostart_enabled=None)
    finally:
        conn.close()
    assert result.update_staged is True
