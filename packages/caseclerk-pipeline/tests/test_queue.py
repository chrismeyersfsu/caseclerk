import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fpdf import FPDF

from caseclerk_core import db
from caseclerk_core.models import DocumentState
from caseclerk_pipeline import queue


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def _write_blank_pdf(path: Path) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.output(str(path))


def _write_text_pdf(path: Path, text: str) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, text=text)
    pdf.output(str(path))


def _seed_pending_document(conn: sqlite3.Connection, case_id: int, case_dir: Path, rel_path: str) -> int:
    path = case_dir / rel_path
    stat = path.stat()
    doc_id = db.upsert_document(
        conn,
        case_id=case_id,
        rel_path=rel_path,
        file_name=path.name,
        ext=path.suffix.lower(),
        size=stat.st_size,
        mtime_ms=int(stat.st_mtime * 1000),
        content_hash="x",
        state=DocumentState.PENDING,
    )
    db.enqueue_job(conn, doc_id)
    return doc_id


def _make_case(conn: sqlite3.Connection, tmp_path: Path) -> tuple[int, Path]:
    client_id = db.upsert_client(conn, "Alvarez, Maria")
    case_id = db.upsert_case(conn, client_id, "2026-0142", "Alvarez, Maria/2026-0142")
    case_dir = tmp_path / "documents" / "Alvarez, Maria" / "2026-0142"
    case_dir.mkdir(parents=True)
    return case_id, case_dir


def test_queue_processes_extract_chunk_and_dates_end_to_end(tmp_path: Path, conn: sqlite3.Connection) -> None:
    case_id, case_dir = _make_case(conn, tmp_path)
    (case_dir / "letter.txt").write_text("The deposition on April 21, 2026 revealed a scheduling conflict.")
    doc_id = _seed_pending_document(conn, case_id, case_dir, "letter.txt")

    processed = queue.run_queue(conn, lambda _case_id: case_dir, concurrency=1)
    assert processed == 1

    doc = db.get_document(conn, doc_id)
    assert doc is not None
    assert doc.state == DocumentState.INDEXED
    assert doc.words is not None and doc.words > 0

    hits = db.search_case(conn, case_id, ["deposition"])
    assert len(hits) == 1
    assert db.get_document_dates(conn, doc_id) == ["2026-04-21"]


def test_queue_records_failure_for_unreadable_pdf(tmp_path: Path, conn: sqlite3.Connection) -> None:
    case_id, case_dir = _make_case(conn, tmp_path)
    _write_blank_pdf(case_dir / "scanned.pdf")
    doc_id = _seed_pending_document(conn, case_id, case_dir, "scanned.pdf")

    processed = queue.run_queue(conn, lambda _case_id: case_dir, concurrency=1)
    assert processed == 1

    doc = db.get_document(conn, doc_id)
    assert doc is not None
    assert doc.state == DocumentState.FAILED
    assert doc.error is not None
    assert "OCR" in doc.error

    failures = db.list_failures(conn)
    assert len(failures) == 1
    assert failures[0].document_id == doc_id


def test_queue_retry_succeeds_after_the_underlying_file_is_fixed(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    case_id, case_dir = _make_case(conn, tmp_path)
    scanned = case_dir / "scanned.pdf"
    _write_blank_pdf(scanned)
    doc_id = _seed_pending_document(conn, case_id, case_dir, "scanned.pdf")

    queue.run_queue(conn, lambda _case_id: case_dir, concurrency=1)
    doc = db.get_document(conn, doc_id)
    assert doc is not None
    assert doc.state == DocumentState.FAILED

    # operator fixes the source (e.g. re-runs OCR upstream) and retries via reprocess_document
    _write_text_pdf(scanned, "The deposition on April 21, 2026 revealed a scheduling conflict.")
    db.requeue_document(conn, doc_id)
    processed = queue.run_queue(conn, lambda _case_id: case_dir, concurrency=1)
    assert processed == 1

    doc_after = db.get_document(conn, doc_id)
    assert doc_after is not None
    assert doc_after.state == DocumentState.INDEXED
    assert doc_after.error is None


def test_queue_marks_unsupported_extensions(tmp_path: Path, conn: sqlite3.Connection) -> None:
    case_id, case_dir = _make_case(conn, tmp_path)
    (case_dir / "voicemail.msg").write_bytes(b"not a real msg file")
    doc_id = _seed_pending_document(conn, case_id, case_dir, "voicemail.msg")

    queue.run_queue(conn, lambda _case_id: case_dir, concurrency=1)

    doc = db.get_document(conn, doc_id)
    assert doc is not None
    assert doc.state == DocumentState.UNSUPPORTED


def test_queue_reclaims_stale_claims_before_processing(tmp_path: Path, conn: sqlite3.Connection) -> None:
    case_id, case_dir = _make_case(conn, tmp_path)
    (case_dir / "letter.txt").write_text("hello")
    doc_id = _seed_pending_document(conn, case_id, case_dir, "letter.txt")

    job = db.claim_job(conn, "crashed-worker")
    assert job is not None
    stale_time = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    conn.execute("UPDATE jobs SET claimed_at = ? WHERE id = ?", (stale_time, job.id))
    conn.commit()

    processed = queue.run_queue(conn, lambda _case_id: case_dir, concurrency=1, reclaim_stale_minutes=30)
    assert processed == 1
    doc = db.get_document(conn, doc_id)
    assert doc is not None
    assert doc.state == DocumentState.INDEXED


def test_default_stale_minutes_is_short_enough_for_interactive_use() -> None:
    """Regression: a job stuck behind a claim from a crashed worker (e.g. the
    BrokenProcessPool bug on frozen Windows) used to take up to 30 minutes to
    become reprocessable -- too long for someone watching `caseclerk
    process`/the tray after a crash. A ceiling, not an exact value, so a
    reasonable future retune doesn't need to touch this test."""
    assert queue.DEFAULT_STALE_MINUTES <= 15


def test_queue_pooled_execution_processes_multiple_documents(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    case_id, case_dir = _make_case(conn, tmp_path)
    for i in range(4):
        (case_dir / f"doc{i}.txt").write_text(f"Document number {i} filed April {i + 1}, 2026.")
        _seed_pending_document(conn, case_id, case_dir, f"doc{i}.txt")

    processed = queue.run_queue(conn, lambda _case_id: case_dir, concurrency=2)
    assert processed == 4

    docs = db.list_documents(conn, case_id)
    assert len(docs) == 4
    assert all(doc.state == DocumentState.INDEXED for doc in docs)


def test_queue_rejects_a_path_escaping_the_case_dir(tmp_path: Path, conn: sqlite3.Connection) -> None:
    """A document row pointing outside its case dir (corrupt data, tampering) must not be read."""
    case_id, case_dir = _make_case(conn, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("should never be read")

    doc_id = db.upsert_document(
        conn,
        case_id=case_id,
        rel_path="../../outside.txt",
        file_name="outside.txt",
        ext=".txt",
        size=outside.stat().st_size,
        mtime_ms=1,
        content_hash="x",
        state=DocumentState.PENDING,
    )
    db.enqueue_job(conn, doc_id)

    with pytest.raises(Exception, match="escapes root"):
        queue.run_queue(conn, lambda _case_id: case_dir, concurrency=1)
