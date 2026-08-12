"""Guardrail helpers shared by every tool module.

Every document tool resolves identifiers through the database before
doing anything else; a client/case that doesn't resolve raises a clean
ValueError, which the MCP layer turns into a plain-text tool error --
never a stack trace, never a filesystem path.
"""

from __future__ import annotations

import sqlite3

from caseclerk_core import db
from caseclerk_core.models import Document


def require_client(conn: sqlite3.Connection, client: str) -> None:
    if client not in db.list_clients(conn):
        raise ValueError(f"Unknown client '{client}'")


def require_case(conn: sqlite3.Connection, client: str, case_number: str) -> int:
    require_client(conn, client)
    case_id = db.resolve_case_id(conn, client, case_number)
    if case_id is None:
        raise ValueError(f"No case '{case_number}' for client '{client}'")
    return case_id


def require_document(
    conn: sqlite3.Connection, case_id: int, document_id: int, *, case_number: str, client: str
) -> Document:
    document = db.get_document(conn, document_id)
    if document is None or document.case_id != case_id:
        raise ValueError(f"No document {document_id} in case '{case_number}' for client '{client}'")
    return document
