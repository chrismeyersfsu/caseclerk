"""Typed shapes shared across the CaseClerk packages.

These mirror the SQLite schema in :mod:`caseclerk_core.db` but are the
vocabulary other packages (pipeline, artifacts, and eventually the MCP
server) import instead of touching raw sqlite3.Row objects.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class DocumentState(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class JobState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    DONE = "done"
    FAILED = "failed"


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True)


class Client(_Base):
    id: int
    name: str


class Case(_Base):
    id: int
    client_id: int
    case_number: str
    rel_path: str


class CaseSummary(_Base):
    case_number: str
    document_count: int
    last_activity: datetime | None = None


class Document(_Base):
    id: int
    case_id: int
    rel_path: str
    file_name: str
    ext: str
    size: int
    mtime_ms: int
    content_hash: str | None = None
    state: DocumentState
    error: str | None = None
    words: int | None = None
    pages: int | None = None
    updated_at: datetime


class Chunk(_Base):
    id: int
    document_id: int
    seq: int
    text: str
    token_estimate: int


class SearchHit(_Base):
    document_id: int
    chunk_id: int
    file_name: str
    seq: int
    snippet: str


class Job(_Base):
    id: int
    document_id: int
    kind: str
    state: JobState
    attempts: int
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    error: str | None = None


class ProcessingFailure(_Base):
    document_id: int
    file_name: str
    case_number: str
    client_name: str
    error: str
    attempts: int


class ProcessingStatus(_Base):
    total: int
    by_state: dict[DocumentState, int]
    update_available: str | None = None
