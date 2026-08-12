"""Walk ``<client>/<case-number>/**`` under clioRoot into the database.

Only the top two levels are treated as client/case; everything below is
a document's rel_path within its case. Every filesystem touch below
clio_root goes through :func:`caseclerk_core.paths.safe_join` so a
maliciously named client/case directory (or a symlink planted inside
one) can't walk the scanner outside the root.
"""

from __future__ import annotations

import fnmatch
import hashlib
import logging
import os
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from caseclerk_core import db
from caseclerk_core.models import DocumentState
from caseclerk_core.paths import PathContainmentError, safe_join

logger = logging.getLogger(__name__)

_HASH_CHUNK_SIZE = 1 << 20
_SYSTEM_DIR_NAMES = {"$RECYCLE.BIN", "System Volume Information", "__MACOSX"}


@dataclass(frozen=True)
class ScanResult:
    clients_seen: int
    cases_seen: int
    documents_new: int
    documents_changed: int
    documents_unchanged: int
    documents_removed: int


def _is_hidden_or_system(name: str) -> bool:
    return name.startswith(".") or name in _SYSTEM_DIR_NAMES


def _is_ignored(rel_posix: str, ignore_globs: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, pattern) for pattern in ignore_globs)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def scan(
    conn: sqlite3.Connection,
    clio_root: str | os.PathLike[str],
    *,
    emails_folder_name: str = "emails-generated",
    ignore_globs: Iterable[str] = (),
) -> ScanResult:
    """Scan clio_root, upserting clients/cases/documents and enqueueing jobs for new/changed files."""
    root = Path(os.path.realpath(clio_root))
    if not root.is_dir():
        raise FileNotFoundError(f"clioRoot is not a directory: {clio_root}")
    all_ignore_globs = [f"{emails_folder_name}/**", *ignore_globs]

    clients_seen = 0
    cases_seen = 0
    new_count = 0
    changed_count = 0
    unchanged_count = 0
    removed_count = 0

    for client_entry in sorted(root.iterdir()):
        if not client_entry.is_dir() or _is_hidden_or_system(client_entry.name):
            continue
        client_id = db.upsert_client(conn, client_entry.name)
        clients_seen += 1

        for case_entry in sorted(client_entry.iterdir()):
            if not case_entry.is_dir() or _is_hidden_or_system(case_entry.name):
                continue
            case_rel = f"{client_entry.name}/{case_entry.name}"
            case_id = db.upsert_case(conn, client_id, case_entry.name, case_rel)
            cases_seen += 1

            existing = db.documents_by_rel_path(conn, case_id)
            seen_rel_paths: set[str] = set()

            for dirpath, dirnames, filenames in os.walk(case_entry):
                dirnames[:] = sorted(
                    d for d in dirnames if not _is_hidden_or_system(d) and d != emails_folder_name
                )
                dir_rel = os.path.relpath(dirpath, case_entry)

                for filename in sorted(filenames):
                    if _is_hidden_or_system(filename):
                        continue
                    file_rel = filename if dir_rel == "." else f"{dir_rel}/{filename}"
                    file_rel = file_rel.replace(os.sep, "/")
                    if _is_ignored(file_rel, all_ignore_globs):
                        continue

                    try:
                        file_path = safe_join(case_entry, file_rel)
                    except PathContainmentError:
                        logger.warning("skipping path outside case dir: %s", file_rel)
                        continue

                    try:
                        stat = file_path.stat()
                    except OSError as exc:
                        logger.warning("skipping unreadable file %s: %s", file_path, exc)
                        continue

                    seen_rel_paths.add(file_rel)
                    size = stat.st_size
                    mtime_ms = int(stat.st_mtime * 1000)
                    prior = existing.get(file_rel)

                    if prior is not None and prior.size == size and prior.mtime_ms == mtime_ms:
                        unchanged_count += 1
                        continue

                    content_hash = _hash_file(file_path)
                    if prior is not None and prior.content_hash == content_hash:
                        db.touch_document_stat(conn, prior.id, size=size, mtime_ms=mtime_ms)
                        unchanged_count += 1
                        continue

                    document_id = db.upsert_document(
                        conn,
                        case_id=case_id,
                        rel_path=file_rel,
                        file_name=file_path.name,
                        ext=file_path.suffix.lower(),
                        size=size,
                        mtime_ms=mtime_ms,
                        content_hash=content_hash,
                        state=DocumentState.PENDING,
                    )
                    db.enqueue_job(conn, document_id, kind="process")
                    if prior is None:
                        new_count += 1
                    else:
                        changed_count += 1

            for rel_path in set(existing) - seen_rel_paths:
                db.delete_document(conn, existing[rel_path].id)
                removed_count += 1

    return ScanResult(
        clients_seen=clients_seen,
        cases_seen=cases_seen,
        documents_new=new_count,
        documents_changed=changed_count,
        documents_unchanged=unchanged_count,
        documents_removed=removed_count,
    )
