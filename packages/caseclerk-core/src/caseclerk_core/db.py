"""SQLite state store: schema, migrations, and typed query helpers.

Every document tool the MCP server will expose resolves a ``client`` +
``case_number`` pair to a ``case_id`` (see :func:`resolve_case_id`) and
every downstream query is filtered by that id in SQL — this is the
enforcement point for "never mix clients" alongside the filesystem
guardrails in :mod:`caseclerk_core.paths`.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from caseclerk_core.config import data_dir
from caseclerk_core.models import (
    CaseSummary,
    Document,
    DocumentState,
    Job,
    JobState,
    ProcessingFailure,
    ProcessingStatus,
    SearchHit,
)

logger = logging.getLogger(__name__)

DB_FILE_NAME = "caseclerk.db"

_SCHEMA_V1 = """
CREATE TABLE clients (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE cases (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    case_number TEXT NOT NULL UNIQUE,
    rel_path TEXT NOT NULL
);

CREATE INDEX idx_cases_client ON cases(client_id);

CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    case_id INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    rel_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    ext TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ms INTEGER NOT NULL,
    content_hash TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    words INTEGER,
    pages INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE (case_id, rel_path)
);

CREATE INDEX idx_documents_case ON documents(case_id);
CREATE INDEX idx_documents_state ON documents(state);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    text TEXT NOT NULL,
    token_estimate INTEGER NOT NULL
);

CREATE INDEX idx_chunks_document ON chunks(document_id);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    text, content='chunks', content_rowid='id'
);

CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE document_dates (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    iso_date TEXT NOT NULL,
    PRIMARY KEY (document_id, iso_date)
);

CREATE TABLE summaries (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_by TEXT,
    claimed_at TEXT,
    error TEXT
);

CREATE INDEX idx_jobs_state ON jobs(state);
CREATE INDEX idx_jobs_document ON jobs(document_id);
"""
# `meta` itself is created by _migrate() before any migration script runs, since it's
# what tracks schema_version in the first place.

_MIGRATIONS: list[tuple[int, str]] = [
    (1, _SCHEMA_V1),
]


def db_path() -> Path:
    return data_dir() / DB_FILE_NAME


def _migrate(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    current = int(row[0]) if row else 0
    for version, script in _MIGRATIONS:
        if version <= current:
            continue
        logger.info("migrating caseclerk db to schema version %d", version)
        conn.executescript(script)
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        current = version
    conn.commit()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open (creating and migrating if needed) the caseclerk database."""
    target = Path(path) if path is not None else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    _migrate(conn)
    return conn


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        case_id=row["case_id"],
        rel_path=row["rel_path"],
        file_name=row["file_name"],
        ext=row["ext"],
        size=row["size"],
        mtime_ms=row["mtime_ms"],
        content_hash=row["content_hash"],
        state=DocumentState(row["state"]),
        error=row["error"],
        words=row["words"],
        pages=row["pages"],
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        document_id=row["document_id"],
        kind=row["kind"],
        state=JobState(row["state"]),
        attempts=row["attempts"],
        claimed_by=row["claimed_by"],
        claimed_at=datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None,
        error=row["error"],
    )


# --- clients / cases -------------------------------------------------------


def upsert_client(conn: sqlite3.Connection, name: str) -> int:
    conn.execute("INSERT INTO clients(name) VALUES (?) ON CONFLICT(name) DO NOTHING", (name,))
    row = conn.execute("SELECT id FROM clients WHERE name = ?", (name,)).fetchone()
    conn.commit()
    return int(row["id"])


def upsert_case(conn: sqlite3.Connection, client_id: int, case_number: str, rel_path: str) -> int:
    conn.execute(
        "INSERT INTO cases(client_id, case_number, rel_path) VALUES (?, ?, ?) "
        "ON CONFLICT(case_number) DO UPDATE SET client_id = excluded.client_id, rel_path = excluded.rel_path",
        (client_id, case_number, rel_path),
    )
    row = conn.execute("SELECT id FROM cases WHERE case_number = ?", (case_number,)).fetchone()
    conn.commit()
    return int(row["id"])


def list_clients(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM clients ORDER BY name").fetchall()
    return [str(row["name"]) for row in rows]


def list_cases(conn: sqlite3.Connection, client_name: str) -> list[CaseSummary]:
    rows = conn.execute(
        """
        SELECT c.case_number AS case_number,
               COUNT(d.id) AS document_count,
               MAX(d.updated_at) AS last_activity
        FROM cases c
        JOIN clients cl ON cl.id = c.client_id
        LEFT JOIN documents d ON d.case_id = c.id
        WHERE cl.name = ?
        GROUP BY c.id
        ORDER BY c.case_number
        """,
        (client_name,),
    ).fetchall()
    return [
        CaseSummary(
            case_number=row["case_number"],
            document_count=row["document_count"],
            last_activity=datetime.fromisoformat(row["last_activity"]) if row["last_activity"] else None,
        )
        for row in rows
    ]


def resolve_case_id(conn: sqlite3.Connection, client_name: str, case_number: str) -> int | None:
    """Look up a case id, requiring both the client and case number to match.

    Deliberately checks both even though case_number is globally unique: a
    caller supplying the wrong client for a real case_number must not
    succeed, so cross-client requests fail here rather than by accident.
    """
    row = conn.execute(
        """
        SELECT c.id FROM cases c
        JOIN clients cl ON cl.id = c.client_id
        WHERE cl.name = ? AND c.case_number = ?
        """,
        (client_name, case_number),
    ).fetchone()
    return int(row["id"]) if row else None


# --- documents ---------------------------------------------------------


def get_document_by_rel_path(conn: sqlite3.Connection, case_id: int, rel_path: str) -> Document | None:
    row = conn.execute(
        "SELECT * FROM documents WHERE case_id = ? AND rel_path = ?", (case_id, rel_path)
    ).fetchone()
    return _row_to_document(row) if row else None


def documents_by_rel_path(conn: sqlite3.Connection, case_id: int) -> dict[str, Document]:
    return {doc.rel_path: doc for doc in list_documents(conn, case_id)}


def upsert_document(
    conn: sqlite3.Connection,
    *,
    case_id: int,
    rel_path: str,
    file_name: str,
    ext: str,
    size: int,
    mtime_ms: int,
    content_hash: str | None,
    state: DocumentState = DocumentState.PENDING,
    words: int | None = None,
    pages: int | None = None,
) -> int:
    now = _now_iso()
    conn.execute(
        """
        INSERT INTO documents(
            case_id, rel_path, file_name, ext, size, mtime_ms, content_hash,
            state, words, pages, updated_at
        ) VALUES (
            :case_id, :rel_path, :file_name, :ext, :size, :mtime_ms, :content_hash,
            :state, :words, :pages, :updated_at
        )
        ON CONFLICT(case_id, rel_path) DO UPDATE SET
            file_name = excluded.file_name,
            ext = excluded.ext,
            size = excluded.size,
            mtime_ms = excluded.mtime_ms,
            content_hash = excluded.content_hash,
            state = excluded.state,
            error = NULL,
            words = excluded.words,
            pages = excluded.pages,
            updated_at = excluded.updated_at
        """,
        {
            "case_id": case_id,
            "rel_path": rel_path,
            "file_name": file_name,
            "ext": ext,
            "size": size,
            "mtime_ms": mtime_ms,
            "content_hash": content_hash,
            "state": state.value,
            "words": words,
            "pages": pages,
            "updated_at": now,
        },
    )
    row = conn.execute(
        "SELECT id FROM documents WHERE case_id = ? AND rel_path = ?", (case_id, rel_path)
    ).fetchone()
    conn.commit()
    return int(row["id"])


def delete_document(conn: sqlite3.Connection, document_id: int) -> None:
    conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    conn.commit()


def touch_document_stat(conn: sqlite3.Connection, document_id: int, *, size: int, mtime_ms: int) -> None:
    """Update size/mtime only, e.g. after a hash confirms content didn't actually change."""
    conn.execute(
        "UPDATE documents SET size = ?, mtime_ms = ?, updated_at = ? WHERE id = ?",
        (size, mtime_ms, _now_iso(), document_id),
    )
    conn.commit()


def set_document_state(
    conn: sqlite3.Connection,
    document_id: int,
    state: DocumentState,
    *,
    error: str | None = None,
    words: int | None = None,
    pages: int | None = None,
) -> None:
    conn.execute(
        """
        UPDATE documents
        SET state = ?, error = ?, words = COALESCE(?, words), pages = COALESCE(?, pages), updated_at = ?
        WHERE id = ?
        """,
        (state.value, error, words, pages, _now_iso(), document_id),
    )
    conn.commit()


def list_documents(conn: sqlite3.Connection, case_id: int) -> list[Document]:
    rows = conn.execute("SELECT * FROM documents WHERE case_id = ? ORDER BY file_name", (case_id,)).fetchall()
    return [_row_to_document(row) for row in rows]


def get_document(conn: sqlite3.Connection, document_id: int) -> Document | None:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return _row_to_document(row) if row else None


# --- chunks / dates / search --------------------------------------------


def replace_chunks(
    conn: sqlite3.Connection, document_id: int, chunks: Sequence[tuple[int, str, int]]
) -> None:
    """Replace all chunks for a document. ``chunks`` is (seq, text, token_estimate) tuples."""
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    conn.executemany(
        "INSERT INTO chunks(document_id, seq, text, token_estimate) VALUES (?, ?, ?, ?)",
        [(document_id, seq, text, tok) for seq, text, tok in chunks],
    )
    conn.commit()


def replace_document_dates(conn: sqlite3.Connection, document_id: int, iso_dates: Iterable[str]) -> None:
    conn.execute("DELETE FROM document_dates WHERE document_id = ?", (document_id,))
    conn.executemany(
        "INSERT OR IGNORE INTO document_dates(document_id, iso_date) VALUES (?, ?)",
        [(document_id, d) for d in dict.fromkeys(iso_dates)],
    )
    conn.commit()


def get_document_dates(conn: sqlite3.Connection, document_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT iso_date FROM document_dates WHERE document_id = ? ORDER BY iso_date", (document_id,)
    ).fetchall()
    return [str(row["iso_date"]) for row in rows]


def _fts_match_query(queries: Sequence[str]) -> str:
    terms = [q.strip() for q in queries if q.strip()]
    if not terms:
        raise ValueError("at least one non-empty query term is required")
    return " OR ".join('"{}"'.format(term.replace('"', '""')) for term in terms)


def search_case(
    conn: sqlite3.Connection, case_id: int, queries: Sequence[str], limit: int = 20
) -> list[SearchHit]:
    """Full-text search always scoped to one case: the join and WHERE both filter on case_id."""
    match = _fts_match_query(queries)
    rows = conn.execute(
        """
        SELECT d.id AS document_id, c.id AS chunk_id, d.file_name AS file_name, c.seq AS seq,
               snippet(chunks_fts, 0, '[', ']', ' ... ', 10) AS snippet
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN documents d ON d.id = c.document_id
        WHERE chunks_fts MATCH ? AND d.case_id = ?
        ORDER BY rank
        LIMIT ?
        """,
        (match, case_id, limit),
    ).fetchall()
    return [
        SearchHit(
            document_id=row["document_id"],
            chunk_id=row["chunk_id"],
            file_name=row["file_name"],
            seq=row["seq"],
            snippet=row["snippet"],
        )
        for row in rows
    ]


# --- processing status / failures ---------------------------------------


def status_counts(conn: sqlite3.Connection) -> ProcessingStatus:
    rows = conn.execute("SELECT state, COUNT(*) AS n FROM documents GROUP BY state").fetchall()
    by_state: dict[DocumentState, int] = dict.fromkeys(DocumentState, 0)
    total = 0
    for row in rows:
        state = DocumentState(row["state"])
        by_state[state] = int(row["n"])
        total += int(row["n"])
    return ProcessingStatus(total=total, by_state=by_state)


def list_failures(conn: sqlite3.Connection) -> list[ProcessingFailure]:
    rows = conn.execute(
        """
        SELECT d.id AS document_id, d.file_name AS file_name, d.error AS error,
               c.case_number AS case_number, cl.name AS client_name,
               COALESCE(j.attempts, 0) AS attempts
        FROM documents d
        JOIN cases c ON c.id = d.case_id
        JOIN clients cl ON cl.id = c.client_id
        LEFT JOIN (
            SELECT document_id, MAX(attempts) AS attempts FROM jobs GROUP BY document_id
        ) j ON j.document_id = d.id
        WHERE d.state = 'failed'
        ORDER BY d.updated_at DESC
        """
    ).fetchall()
    return [
        ProcessingFailure(
            document_id=row["document_id"],
            file_name=row["file_name"],
            case_number=row["case_number"],
            client_name=row["client_name"],
            error=row["error"] or "",
            attempts=row["attempts"],
        )
        for row in rows
    ]


# --- jobs -----------------------------------------------------------------


def _enqueue_job_nocommit(conn: sqlite3.Connection, document_id: int, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO jobs(document_id, kind, state, attempts) VALUES (?, ?, 'queued', 0)",
        (document_id, kind),
    )
    return int(cur.lastrowid)  # type: ignore[arg-type]


def enqueue_job(conn: sqlite3.Connection, document_id: int, kind: str = "process") -> int:
    job_id = _enqueue_job_nocommit(conn, document_id, kind)
    conn.commit()
    return job_id


def requeue_document(conn: sqlite3.Connection, document_id: int, kind: str = "process") -> int:
    """Reset a document to pending and enqueue a fresh job for it. Returns the job id."""
    conn.execute(
        "UPDATE documents SET state = ?, error = NULL, updated_at = ? WHERE id = ?",
        (DocumentState.PENDING.value, _now_iso(), document_id),
    )
    job_id = _enqueue_job_nocommit(conn, document_id, kind)
    conn.commit()
    return job_id


def get_job(conn: sqlite3.Connection, job_id: int) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def claim_job(conn: sqlite3.Connection, claimed_by: str) -> Job | None:
    """Atomically claim the oldest queued job, or None if the queue is empty."""
    row = conn.execute(
        "SELECT id FROM jobs WHERE state = ? ORDER BY id LIMIT 1", (JobState.QUEUED.value,)
    ).fetchone()
    if row is None:
        return None
    job_id = int(row["id"])
    cur = conn.execute(
        "UPDATE jobs SET state = ?, claimed_by = ?, claimed_at = ?, attempts = attempts + 1 "
        "WHERE id = ? AND state = ?",
        (JobState.CLAIMED.value, claimed_by, _now_iso(), job_id, JobState.QUEUED.value),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return get_job(conn, job_id)


def complete_job(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("UPDATE jobs SET state = ?, error = NULL WHERE id = ?", (JobState.DONE.value, job_id))
    conn.commit()


def fail_job(conn: sqlite3.Connection, job_id: int, error: str) -> None:
    conn.execute("UPDATE jobs SET state = ?, error = ? WHERE id = ?", (JobState.FAILED.value, error, job_id))
    conn.commit()


def reclaim_stale_jobs(conn: sqlite3.Connection, older_than_minutes: int) -> int:
    """Reset claims older than the threshold back to queued (recovers from a crashed worker)."""
    cutoff = (datetime.now(UTC) - timedelta(minutes=older_than_minutes)).isoformat()
    cur = conn.execute(
        "UPDATE jobs SET state = ?, claimed_by = NULL, claimed_at = NULL WHERE state = ? AND claimed_at < ?",
        (JobState.QUEUED.value, JobState.CLAIMED.value, cutoff),
    )
    conn.commit()
    return cur.rowcount


# --- meta -------------------------------------------------------------------


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
