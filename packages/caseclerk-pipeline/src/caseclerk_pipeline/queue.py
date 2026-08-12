"""Drain the jobs queue: claim -> extract (worker) -> chunk/dates/write (coordinator).

Extraction runs in a ProcessPoolExecutor so a crashing/hanging parser
can't take the coordinator down with it. Every database write happens
back on the coordinating thread's own connection -- sqlite3 connections
aren't safe to share across processes -- and every filesystem read goes
through caseclerk_core.paths.safe_join, the one place path containment
is enforced.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from caseclerk_core import db
from caseclerk_core.models import Document, DocumentState
from caseclerk_core.paths import safe_join
from caseclerk_pipeline.chunker import chunk_markdown
from caseclerk_pipeline.dates import find_dates
from caseclerk_pipeline.extractors.pdf import NeedsOcrError
from caseclerk_pipeline.extractors.registry import get_extractor, is_unsupported

logger = logging.getLogger(__name__)

DEFAULT_STALE_MINUTES = 30
DEFAULT_JOB_KIND = "process"


@dataclass(frozen=True)
class ExtractionResult:
    ok: bool
    markdown: str | None = None
    error: str | None = None
    unsupported: bool = False


def _extract_worker(path_str: str, ext: str) -> ExtractionResult:
    """Runs in a worker process: pure extraction, no db or shared state."""
    if is_unsupported(ext):
        return ExtractionResult(ok=False, unsupported=True)
    extractor = get_extractor(ext)
    if extractor is None:
        return ExtractionResult(ok=False, unsupported=True)
    try:
        markdown = extractor(Path(path_str))
    except NeedsOcrError as exc:
        return ExtractionResult(ok=False, error=str(exc))
    except Exception as exc:  # noqa: BLE001 - any extractor failure becomes a recorded error, not a crash
        return ExtractionResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    return ExtractionResult(ok=True, markdown=markdown)


def _apply_result(conn: sqlite3.Connection, document_id: int, job_id: int, result: ExtractionResult) -> None:
    if result.unsupported:
        db.set_document_state(conn, document_id, DocumentState.UNSUPPORTED)
        db.complete_job(conn, job_id)
        return
    if not result.ok or result.markdown is None:
        error = result.error or "extraction failed"
        db.set_document_state(conn, document_id, DocumentState.FAILED, error=error)
        db.fail_job(conn, job_id, error)
        return

    chunks = chunk_markdown(result.markdown)
    db.replace_chunks(conn, document_id, [(c.seq, c.text, c.token_estimate) for c in chunks])
    db.replace_document_dates(conn, document_id, find_dates(result.markdown))
    db.set_document_state(conn, document_id, DocumentState.INDEXED, words=len(result.markdown.split()))
    db.complete_job(conn, job_id)


CaseDirResolver = Callable[[int], Path]


def _resolve_document_path(case_dir_for: CaseDirResolver, document: Document) -> Path:
    return safe_join(case_dir_for(document.case_id), document.rel_path)


def process_one(
    conn: sqlite3.Connection, case_dir_for: CaseDirResolver, document: Document, job_id: int
) -> None:
    """Process a single already-claimed job synchronously (no worker pool)."""
    path = _resolve_document_path(case_dir_for, document)
    result = _extract_worker(str(path), document.ext)
    _apply_result(conn, document.id, job_id, result)


def run_queue(
    conn: sqlite3.Connection,
    case_dir_for: CaseDirResolver,
    *,
    concurrency: int = 2,
    claimed_by: str = "queue-worker",
    reclaim_stale_minutes: int = DEFAULT_STALE_MINUTES,
) -> int:
    """Drain every currently-queued job once. Returns the number processed."""
    reclaimed = db.reclaim_stale_jobs(conn, reclaim_stale_minutes)
    if reclaimed:
        logger.info("reclaimed %d stale job claim(s)", reclaimed)

    if concurrency <= 1:
        return _run_queue_sequential(conn, case_dir_for, claimed_by)
    return _run_queue_pooled(conn, case_dir_for, concurrency, claimed_by)


def _run_queue_sequential(conn: sqlite3.Connection, case_dir_for: CaseDirResolver, claimed_by: str) -> int:
    processed = 0
    while True:
        job = db.claim_job(conn, claimed_by)
        if job is None:
            break
        document = db.get_document(conn, job.document_id)
        if document is None:
            db.fail_job(conn, job.id, "document no longer exists")
            continue
        process_one(conn, case_dir_for, document, job.id)
        processed += 1
    return processed


def _run_queue_pooled(
    conn: sqlite3.Connection, case_dir_for: CaseDirResolver, concurrency: int, claimed_by: str
) -> int:
    processed = 0
    with ProcessPoolExecutor(max_workers=concurrency) as executor:
        in_flight: dict[Future[ExtractionResult], tuple[int, int]] = {}
        while True:
            while len(in_flight) < concurrency:
                job = db.claim_job(conn, claimed_by)
                if job is None:
                    break
                document = db.get_document(conn, job.document_id)
                if document is None:
                    db.fail_job(conn, job.id, "document no longer exists")
                    continue
                path = _resolve_document_path(case_dir_for, document)
                future = executor.submit(_extract_worker, str(path), document.ext)
                in_flight[future] = (document.id, job.id)

            if not in_flight:
                break

            done, _pending = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                document_id, job_id = in_flight.pop(future)
                _apply_result(conn, document_id, job_id, future.result())
                processed += 1
    return processed


def enqueue_and_run(
    conn: sqlite3.Connection,
    case_dir_for: CaseDirResolver,
    document_id: int,
    *,
    concurrency: int = 1,
) -> None:
    """Convenience: enqueue one document (e.g. for reprocess_document) and drain it."""
    db.enqueue_job(conn, document_id, kind=DEFAULT_JOB_KIND)
    run_queue(conn, case_dir_for, concurrency=concurrency)
