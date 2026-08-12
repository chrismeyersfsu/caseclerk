"""list_clients, list_cases, get_case_overview."""

from __future__ import annotations

from pathlib import PurePosixPath

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from caseclerk_core import db
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.schemas import CaseOverviewOut, CaseSummaryOut, DocumentOverviewOut
from caseclerk_mcp.tools._common import require_case, require_client

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def _folder_of(rel_path: str) -> str:
    parent = PurePosixPath(rel_path).parent
    return "" if str(parent) == "." else str(parent)


def register_browse_tools(server: MCPServer[None], deps: Deps) -> None:
    @server.tool(annotations=_READ_ONLY)
    def list_clients() -> list[str]:
        """List every client with at least one scanned case. Names only, no case-level detail."""
        conn = deps.open_db()
        try:
            return db.list_clients(conn)
        finally:
            conn.close()

    @server.tool(annotations=_READ_ONLY)
    def list_cases(client: str) -> list[CaseSummaryOut]:
        """List every case for one client: case number, document count, last activity."""
        conn = deps.open_db()
        try:
            require_client(conn, client)
            return [
                CaseSummaryOut(
                    case_number=summary.case_number,
                    document_count=summary.document_count,
                    last_activity=summary.last_activity.isoformat() if summary.last_activity else None,
                )
                for summary in db.list_cases(conn, client)
            ]
        finally:
            conn.close()

    @server.tool(annotations=_READ_ONLY)
    def get_case_overview(client: str, case_number: str) -> CaseOverviewOut:
        """Per-document overview for one case: id, name, folder, type, state, words/pages, dates
        mentioned, summary if available, and the error note for anything unreadable."""
        conn = deps.open_db()
        try:
            case_id = require_case(conn, client, case_number)
            documents = [
                DocumentOverviewOut(
                    id=document.id,
                    file_name=document.file_name,
                    folder=_folder_of(document.rel_path),
                    ext=document.ext,
                    state=document.state.value,
                    words=document.words,
                    pages=document.pages,
                    dates_mentioned=db.get_document_dates(conn, document.id),
                    summary=db.get_summary(conn, document.id),
                    error=document.error,
                )
                for document in db.list_documents(conn, case_id)
            ]
            return CaseOverviewOut(client=client, case_number=case_number, documents=documents)
        finally:
            conn.close()
