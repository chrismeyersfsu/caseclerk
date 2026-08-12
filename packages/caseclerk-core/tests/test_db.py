import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from caseclerk_core import db
from caseclerk_core.models import DocumentState


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def _seed_case(conn: sqlite3.Connection, client_name: str, case_number: str, text: str) -> tuple[int, int]:
    client_id = db.upsert_client(conn, client_name)
    case_id = db.upsert_case(conn, client_id, case_number, f"{client_name}/{case_number}")
    doc_id = db.upsert_document(
        conn,
        case_id=case_id,
        rel_path="depo.md",
        file_name="depo.md",
        ext=".md",
        size=100,
        mtime_ms=1000,
        content_hash="abc",
        state=DocumentState.INDEXED,
    )
    db.replace_chunks(conn, doc_id, [(0, text, 10)])
    return case_id, doc_id


def test_schema_version_recorded(conn: sqlite3.Connection) -> None:
    assert db.get_meta(conn, "schema_version") == "2"


def test_fts5_available(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('integrity-check')")


def test_search_is_scoped_to_case(conn: sqlite3.Connection) -> None:
    case_a, doc_a = _seed_case(
        conn, "Alvarez, Maria", "2026-0142", "The April 21 2026 deposition revealed a scheduling conflict."
    )
    case_b, doc_b = _seed_case(
        conn, "Barrett Holdings LLC", "2026-0201", "The April 21 2026 deposition revealed a conflict too."
    )

    hits_a = db.search_case(conn, case_a, ["deposition"])
    hits_b = db.search_case(conn, case_b, ["deposition"])

    assert [h.document_id for h in hits_a] == [doc_a]
    assert [h.document_id for h in hits_b] == [doc_b]
    assert doc_b not in {h.document_id for h in hits_a}
    assert doc_a not in {h.document_id for h in hits_b}


def test_search_case_returns_nothing_for_unmatched_terms(conn: sqlite3.Connection) -> None:
    case_a, _ = _seed_case(conn, "Alvarez, Maria", "2026-0142", "settlement conference notes")
    assert db.search_case(conn, case_a, ["nonexistentword"]) == []


def test_resolve_case_id_requires_matching_client(conn: sqlite3.Connection) -> None:
    case_a, _ = _seed_case(conn, "Alvarez, Maria", "2026-0142", "text")
    assert db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142") == case_a
    assert db.resolve_case_id(conn, "Barrett Holdings LLC", "2026-0142") is None
    assert db.resolve_case_id(conn, "Alvarez, Maria", "no-such-case") is None


def test_get_case_returns_rel_path(conn: sqlite3.Connection) -> None:
    case_id, _ = _seed_case(conn, "Alvarez, Maria", "2026-0142", "text")
    case = db.get_case(conn, case_id)
    assert case is not None
    assert case.case_number == "2026-0142"
    assert case.rel_path == "Alvarez, Maria/2026-0142"


def test_get_case_missing_returns_none(conn: sqlite3.Connection) -> None:
    assert db.get_case(conn, 999) is None


def test_list_chunks_ordered_by_seq(conn: sqlite3.Connection) -> None:
    _case_id, doc_id = _seed_case(conn, "Alvarez, Maria", "2026-0142", "first chunk")
    db.replace_chunks(conn, doc_id, [(1, "second", 5), (0, "first chunk", 10)])
    chunks = db.list_chunks(conn, doc_id)
    assert [c.seq for c in chunks] == [0, 1]
    assert chunks[0].text == "first chunk"


def test_get_summary_is_none_until_one_is_written(conn: sqlite3.Connection) -> None:
    _case_id, doc_id = _seed_case(conn, "Alvarez, Maria", "2026-0142", "text")
    assert db.get_summary(conn, doc_id) is None
    conn.execute(
        "INSERT INTO summaries(document_id, model, text, created_at) VALUES (?, ?, ?, ?)",
        (doc_id, "test-model", "A short summary.", "2026-08-12T00:00:00+00:00"),
    )
    conn.commit()
    assert db.get_summary(conn, doc_id) == "A short summary."


def test_upsert_document_and_status_counts(conn: sqlite3.Connection) -> None:
    client_id = db.upsert_client(conn, "Alvarez, Maria")
    case_id = db.upsert_case(conn, client_id, "2026-0142", "Alvarez, Maria/2026-0142")
    db.upsert_document(
        conn,
        case_id=case_id,
        rel_path="a.txt",
        file_name="a.txt",
        ext=".txt",
        size=1,
        mtime_ms=1,
        content_hash="x",
        state=DocumentState.PENDING,
    )
    db.upsert_document(
        conn,
        case_id=case_id,
        rel_path="b.txt",
        file_name="b.txt",
        ext=".txt",
        size=1,
        mtime_ms=1,
        content_hash="y",
        state=DocumentState.FAILED,
    )
    db.set_document_state(
        conn,
        db.get_document_by_rel_path(conn, case_id, "b.txt").id,  # type: ignore[union-attr]
        DocumentState.FAILED,
        error="needs OCR",
    )

    status = db.status_counts(conn)
    assert status.total == 2
    assert status.by_state[DocumentState.PENDING] == 1
    assert status.by_state[DocumentState.FAILED] == 1

    failures = db.list_failures(conn)
    assert len(failures) == 1
    assert failures[0].file_name == "b.txt"
    assert failures[0].error == "needs OCR"
    assert failures[0].client_name == "Alvarez, Maria"
    assert failures[0].case_number == "2026-0142"


def test_jobs_claim_complete_fail_and_requeue(conn: sqlite3.Connection) -> None:
    client_id = db.upsert_client(conn, "Alvarez, Maria")
    case_id = db.upsert_case(conn, client_id, "2026-0142", "Alvarez, Maria/2026-0142")
    doc_id = db.upsert_document(
        conn,
        case_id=case_id,
        rel_path="a.txt",
        file_name="a.txt",
        ext=".txt",
        size=1,
        mtime_ms=1,
        content_hash="x",
    )
    job_id = db.enqueue_job(conn, doc_id)

    claimed = db.claim_job(conn, "worker-1")
    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.state.value == "claimed"
    assert claimed.attempts == 1
    assert db.claim_job(conn, "worker-2") is None

    db.fail_job(conn, job_id, "boom")
    failed_job = db.get_job(conn, job_id)
    assert failed_job is not None
    assert failed_job.state.value == "failed"
    assert failed_job.error == "boom"

    new_job_id = db.requeue_document(conn, doc_id)
    doc = db.get_document(conn, doc_id)
    assert doc is not None
    assert doc.state == DocumentState.PENDING
    requeued_job = db.get_job(conn, new_job_id)
    assert requeued_job is not None
    assert requeued_job.state.value == "queued"


def test_complete_job(conn: sqlite3.Connection) -> None:
    client_id = db.upsert_client(conn, "Alvarez, Maria")
    case_id = db.upsert_case(conn, client_id, "2026-0142", "Alvarez, Maria/2026-0142")
    doc_id = db.upsert_document(
        conn,
        case_id=case_id,
        rel_path="a.txt",
        file_name="a.txt",
        ext=".txt",
        size=1,
        mtime_ms=1,
        content_hash="x",
    )
    job_id = db.enqueue_job(conn, doc_id)
    db.claim_job(conn, "worker-1")
    db.complete_job(conn, job_id)
    job = db.get_job(conn, job_id)
    assert job is not None
    assert job.state.value == "done"


def test_reclaim_stale_jobs(conn: sqlite3.Connection) -> None:
    client_id = db.upsert_client(conn, "Alvarez, Maria")
    case_id = db.upsert_case(conn, client_id, "2026-0142", "Alvarez, Maria/2026-0142")
    doc_id = db.upsert_document(
        conn,
        case_id=case_id,
        rel_path="a.txt",
        file_name="a.txt",
        ext=".txt",
        size=1,
        mtime_ms=1,
        content_hash="x",
    )
    job_id = db.enqueue_job(conn, doc_id)
    db.claim_job(conn, "worker-1")

    stale_time = (datetime.now(UTC) - timedelta(minutes=120)).isoformat()
    conn.execute("UPDATE jobs SET claimed_at = ? WHERE id = ?", (stale_time, job_id))
    conn.commit()

    reclaimed = db.reclaim_stale_jobs(conn, older_than_minutes=30)
    assert reclaimed == 1
    job = db.get_job(conn, job_id)
    assert job is not None
    assert job.state.value == "queued"
    assert job.claimed_by is None


def test_document_dates_roundtrip(conn: sqlite3.Connection) -> None:
    _case_id, doc_id = _seed_case(conn, "Alvarez, Maria", "2026-0142", "deposition text")
    db.replace_document_dates(conn, doc_id, ["2026-04-21", "2026-04-21", "2026-05-01"])
    assert db.get_document_dates(conn, doc_id) == ["2026-04-21", "2026-05-01"]


def test_list_clients_and_cases(conn: sqlite3.Connection) -> None:
    _seed_case(conn, "Alvarez, Maria", "2026-0142", "one")
    _seed_case(conn, "Barrett Holdings LLC", "2026-0201", "two")
    assert db.list_clients(conn) == ["Alvarez, Maria", "Barrett Holdings LLC"]

    cases = db.list_cases(conn, "Alvarez, Maria")
    assert len(cases) == 1
    assert cases[0].case_number == "2026-0142"
    assert cases[0].document_count == 1


def test_oauth_client_roundtrip(conn: sqlite3.Connection) -> None:
    assert db.get_oauth_client(conn, "client-1") is None
    db.upsert_oauth_client(conn, "client-1", '{"client_id": "client-1"}')
    assert db.get_oauth_client(conn, "client-1") == '{"client_id": "client-1"}'
    db.upsert_oauth_client(conn, "client-1", '{"client_id": "client-1", "v": 2}')
    assert db.get_oauth_client(conn, "client-1") == '{"client_id": "client-1", "v": 2}'


def test_oauth_auth_code_roundtrip_and_delete(conn: sqlite3.Connection) -> None:
    db.insert_oauth_auth_code(conn, "code-1", "client-1", '{"code": "code-1"}', expires_at=123.0)
    assert db.get_oauth_auth_code(conn, "code-1") == '{"code": "code-1"}'
    db.delete_oauth_auth_code(conn, "code-1")
    assert db.get_oauth_auth_code(conn, "code-1") is None


def test_oauth_access_token_roundtrip_and_delete(conn: sqlite3.Connection) -> None:
    db.insert_oauth_access_token(conn, "tok-1", "client-1", '{"token": "tok-1"}', expires_at=None)
    assert db.get_oauth_access_token(conn, "tok-1") == '{"token": "tok-1"}'
    db.delete_oauth_access_token(conn, "tok-1")
    assert db.get_oauth_access_token(conn, "tok-1") is None


def test_oauth_refresh_token_roundtrip_and_delete(conn: sqlite3.Connection) -> None:
    db.insert_oauth_refresh_token(conn, "rt-1", "client-1", '{"token": "rt-1"}', expires_at=None)
    assert db.get_oauth_refresh_token(conn, "rt-1") == '{"token": "rt-1"}'
    db.delete_oauth_refresh_token(conn, "rt-1")
    assert db.get_oauth_refresh_token(conn, "rt-1") is None


def test_delete_oauth_tokens_for_client_removes_both_kinds(conn: sqlite3.Connection) -> None:
    db.insert_oauth_access_token(conn, "tok-1", "client-1", "{}", expires_at=None)
    db.insert_oauth_refresh_token(conn, "rt-1", "client-1", "{}", expires_at=None)
    db.insert_oauth_access_token(conn, "tok-2", "client-2", "{}", expires_at=None)

    db.delete_oauth_tokens_for_client(conn, "client-1")

    assert db.get_oauth_access_token(conn, "tok-1") is None
    assert db.get_oauth_refresh_token(conn, "rt-1") is None
    assert db.get_oauth_access_token(conn, "tok-2") == "{}"  # a different client is untouched


def test_remote_requests_audit_log(conn: sqlite3.Connection) -> None:
    assert db.list_remote_requests(conn) == []

    db.insert_remote_request(conn, tool="list_clients", args_summary="{}", ok=True, error=None)
    db.insert_remote_request(
        conn, tool="search_case", args_summary="{'queries': ['x']}", ok=False, error="boom"
    )

    entries = db.list_remote_requests(conn)
    assert len(entries) == 2
    assert entries[0].tool == "search_case"  # most recent first
    assert entries[0].ok is False
    assert entries[0].error == "boom"
    assert entries[1].tool == "list_clients"
    assert entries[1].ok is True


def test_remote_requests_respects_limit(conn: sqlite3.Connection) -> None:
    for i in range(5):
        db.insert_remote_request(conn, tool=f"tool-{i}", args_summary=None, ok=True, error=None)
    assert len(db.list_remote_requests(conn, limit=2)) == 2
