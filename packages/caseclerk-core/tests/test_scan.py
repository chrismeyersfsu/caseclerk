import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from caseclerk_core import db, scan
from caseclerk_core.models import DocumentState


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_scan_discovers_new_documents_and_skips_emails_folder(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    documents = tmp_path / "documents"
    _write(documents / "Alvarez, Maria" / "2026-0142" / "letter.txt", "hello")
    _write(documents / "Alvarez, Maria" / "2026-0142" / "emails-generated" / "ignored.eml", "skip me")

    result = scan.scan(conn, documents)
    assert result.clients_seen == 1
    assert result.cases_seen == 1
    assert result.documents_new == 1

    case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
    assert case_id is not None
    docs = db.list_documents(conn, case_id)
    assert [d.rel_path for d in docs] == ["letter.txt"]
    assert docs[0].state == DocumentState.PENDING


def test_scan_enqueues_a_job_for_new_documents(tmp_path: Path, conn: sqlite3.Connection) -> None:
    documents = tmp_path / "documents"
    _write(documents / "Alvarez, Maria" / "2026-0142" / "letter.txt", "hello")
    scan.scan(conn, documents)

    job = db.claim_job(conn, "worker-1")
    assert job is not None
    assert job.kind == "process"


def test_scan_ignores_a_noop_touch(tmp_path: Path, conn: sqlite3.Connection) -> None:
    documents = tmp_path / "documents"
    path = documents / "Alvarez, Maria" / "2026-0142" / "letter.txt"
    _write(path, "hello")
    scan.scan(conn, documents)

    case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
    assert case_id is not None
    doc = db.list_documents(conn, case_id)[0]
    db.set_document_state(conn, doc.id, DocumentState.INDEXED)

    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))  # touch without content change

    result = scan.scan(conn, documents)
    assert result.documents_unchanged == 1
    assert result.documents_changed == 0
    after = db.get_document(conn, doc.id)
    assert after is not None
    assert after.state == DocumentState.INDEXED  # untouched by the no-op change


def test_scan_detects_real_content_change(tmp_path: Path, conn: sqlite3.Connection) -> None:
    documents = tmp_path / "documents"
    path = documents / "Alvarez, Maria" / "2026-0142" / "letter.txt"
    _write(path, "hello")
    scan.scan(conn, documents)

    case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
    assert case_id is not None
    doc = db.list_documents(conn, case_id)[0]
    db.set_document_state(conn, doc.id, DocumentState.INDEXED)

    stat = path.stat()
    _write(path, "hello, changed")
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))

    result = scan.scan(conn, documents)
    assert result.documents_changed == 1
    after = db.get_document(conn, doc.id)
    assert after is not None
    assert after.state == DocumentState.PENDING


def test_scan_deletes_vanished_documents(tmp_path: Path, conn: sqlite3.Connection) -> None:
    documents = tmp_path / "documents"
    path = documents / "Alvarez, Maria" / "2026-0142" / "letter.txt"
    _write(path, "hello")
    scan.scan(conn, documents)

    case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
    assert case_id is not None
    assert len(db.list_documents(conn, case_id)) == 1

    path.unlink()
    result = scan.scan(conn, documents)
    assert result.documents_removed == 1
    assert db.list_documents(conn, case_id) == []


def test_scan_respects_configured_ignore_globs(tmp_path: Path, conn: sqlite3.Connection) -> None:
    documents = tmp_path / "documents"
    _write(documents / "Alvarez, Maria" / "2026-0142" / "keep.txt", "keep me")
    _write(documents / "Alvarez, Maria" / "2026-0142" / "scratch.tmp", "throwaway")

    scan.scan(conn, documents, ignore_globs=["*.tmp"])

    case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
    assert case_id is not None
    docs = db.list_documents(conn, case_id)
    assert [d.rel_path for d in docs] == ["keep.txt"]


def test_scan_skips_hidden_dirs_and_files(tmp_path: Path, conn: sqlite3.Connection) -> None:
    documents = tmp_path / "documents"
    _write(documents / "Alvarez, Maria" / "2026-0142" / "keep.txt", "keep me")
    _write(documents / "Alvarez, Maria" / "2026-0142" / ".DS_Store", "junk")
    _write(documents / "Alvarez, Maria" / "2026-0142" / ".hidden" / "nested.txt", "junk")
    _write(documents / ".git" / "config", "junk")

    scan.scan(conn, documents)

    assert db.list_clients(conn) == ["Alvarez, Maria"]
    case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
    assert case_id is not None
    docs = db.list_documents(conn, case_id)
    assert [d.rel_path for d in docs] == ["keep.txt"]
