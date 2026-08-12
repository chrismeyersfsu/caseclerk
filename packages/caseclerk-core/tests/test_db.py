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
    assert db.get_meta(conn, "schema_version") == "1"


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
