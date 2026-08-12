"""Structured return shapes for every MCP tool. Kept separate from tools/ so the
tool modules read as pure orchestration over caseclerk-core/pipeline/artifacts."""

from __future__ import annotations

from pydantic import BaseModel


class CaseSummaryOut(BaseModel):
    case_number: str
    document_count: int
    last_activity: str | None = None


class DocumentOverviewOut(BaseModel):
    id: int
    file_name: str
    folder: str
    ext: str
    state: str
    words: int | None = None
    pages: int | None = None
    dates_mentioned: list[str]
    summary: str | None = None
    error: str | None = None


class CaseOverviewOut(BaseModel):
    client: str
    case_number: str
    documents: list[DocumentOverviewOut]


class SearchHitOut(BaseModel):
    document_id: int
    chunk_id: int
    file_name: str
    seq: int
    snippet: str


class ChunkOut(BaseModel):
    seq: int
    text: str


class ReadDocumentOut(BaseModel):
    document_id: int
    file_name: str
    state: str
    chunks: list[ChunkOut]
    has_more: bool
    next_start_seq: int | None = None


class SaveEmailDraftOut(BaseModel):
    eml_path: str
    txt_path: str


class ProcessingStatusOut(BaseModel):
    total: int
    by_state: dict[str, int]
    update_available: str | None = None


class ProcessingFailureOut(BaseModel):
    document_id: int
    file_name: str
    case_number: str
    client_name: str
    error: str
    attempts: int


class ReprocessOut(BaseModel):
    document_id: int
    job_id: int
    state: str


class SettingsOut(BaseModel):
    version: str
    documents_root: str | None
    emails_folder_name: str
    email_file_name_template: str
    processing_concurrency: int
    processing_watch: bool
    updates_auto: bool
    updates_check_interval_hours: int
    summarization_enabled: bool
    summarization_provider: str
