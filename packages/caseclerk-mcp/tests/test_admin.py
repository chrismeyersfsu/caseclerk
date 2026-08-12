from __future__ import annotations

from collections.abc import Callable

import pytest
from conftest import Env
from mcp.server.mcpserver.exceptions import ToolError

from caseclerk_core import db
from caseclerk_core.config import Config, SummarizationConfig
from caseclerk_core.models import DocumentState


def test_processing_status_counts_by_state(env: Env) -> None:
    env.seed_case("Alvarez, Maria", "2026-0142", [("a.md", "hello")])
    conn = env.open_db()
    try:
        case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
        assert case_id is not None
        doc_id = db.upsert_document(
            conn,
            case_id=case_id,
            rel_path="b.pdf",
            file_name="b.pdf",
            ext=".pdf",
            size=1,
            mtime_ms=1,
            content_hash="y",
            state=DocumentState.FAILED,
        )
        db.set_document_state(conn, doc_id, DocumentState.FAILED, error="needs OCR")
    finally:
        conn.close()

    result = env.call("processing_status")
    payload = result.structured_content
    assert payload["total"] == 2
    assert payload["by_state"]["indexed"] == 1
    assert payload["by_state"]["failed"] == 1
    assert payload["update_available"] is None


def test_processing_status_never_hits_the_network(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("processing_status must not perform network I/O")

    monkeypatch.setattr("httpx.Client.get", _boom)
    env.call("processing_status")  # must not raise


def test_processing_status_surfaces_cached_update(env: Env) -> None:
    conn = env.open_db()
    try:
        db.set_meta(conn, "updates.available_version", "v9.9.9")
    finally:
        conn.close()
    result = env.call("processing_status")
    assert result.structured_content["update_available"] == "v9.9.9"


def test_list_processing_failures(env: Env) -> None:
    env.seed_case("Alvarez, Maria", "2026-0142")
    conn = env.open_db()
    try:
        case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
        assert case_id is not None
        doc_id = db.upsert_document(
            conn,
            case_id=case_id,
            rel_path="scanned.pdf",
            file_name="scanned.pdf",
            ext=".pdf",
            size=1,
            mtime_ms=1,
            content_hash="z",
            state=DocumentState.FAILED,
        )
        db.set_document_state(conn, doc_id, DocumentState.FAILED, error="scanned PDF — needs OCR")
    finally:
        conn.close()

    result = env.call("list_processing_failures")
    failures = result.structured_content["result"]
    assert len(failures) == 1
    assert failures[0]["file_name"] == "scanned.pdf"
    assert "needs OCR" in failures[0]["error"]


def test_reprocess_document_requeues(env: Env) -> None:
    _case_id, doc_ids = env.seed_case("Alvarez, Maria", "2026-0142", [("a.md", "text")])
    result = env.call(
        "reprocess_document", client="Alvarez, Maria", case_number="2026-0142", document_id=doc_ids[0]
    )
    assert result.structured_content["state"] == "pending"

    conn = env.open_db()
    try:
        document = db.get_document(conn, doc_ids[0])
        assert document is not None
        assert document.state == DocumentState.PENDING
    finally:
        conn.close()


def test_reprocess_unknown_document_raises_clean_message(env: Env) -> None:
    env.seed_case("Alvarez, Maria", "2026-0142")
    with pytest.raises(ToolError, match="No document"):
        env.call("reprocess_document", client="Alvarez, Maria", case_number="2026-0142", document_id=999999)


def test_get_settings_reflects_config(env: Env) -> None:
    result = env.call("get_settings")
    payload = result.structured_content
    assert payload["emails_folder_name"] == "emails-generated"
    assert payload["processing_concurrency"] == 2
    assert "version" in payload


def test_get_settings_never_echoes_secrets(
    make_env: Callable[..., Env], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CASECLERK_TEST_FAKE_API_KEY", "sk-should-never-appear-in-output")
    cfg = Config(
        summarization=SummarizationConfig(
            enabled=True,
            provider="anthropic",
            api_key_env="CASECLERK_TEST_FAKE_API_KEY",
            base_url="https://example.com",
        )
    )
    custom_env = make_env(config=cfg)
    result = custom_env.call("get_settings")

    dumped = repr(result.structured_content)
    assert "sk-should-never-appear-in-output" not in dumped
    assert "CASECLERK_TEST_FAKE_API_KEY" not in dumped
