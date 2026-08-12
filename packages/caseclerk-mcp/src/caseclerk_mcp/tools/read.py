"""read_document: paged markdown chunks for one document."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from caseclerk_core import db
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.schemas import ChunkOut, ReadDocumentOut
from caseclerk_mcp.tools._common import require_case, require_document

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
DEFAULT_MAX_CHUNKS = 5


def register_read_tools(server: MCPServer[None], deps: Deps) -> None:
    @server.tool(annotations=_READ_ONLY)
    def read_document(
        client: str,
        case_number: str,
        document_id: int,
        start_seq: int = 0,
        max_chunks: int = DEFAULT_MAX_CHUNKS,
    ) -> ReadDocumentOut:
        """Read a document's extracted markdown, paged in chunks starting at start_seq.
        While has_more is true, call again with start_seq=next_start_seq to continue."""
        conn = deps.open_db()
        try:
            case_id = require_case(conn, client, case_number)
            document = require_document(conn, case_id, document_id, case_number=case_number, client=client)

            all_chunks = [c for c in db.list_chunks(conn, document_id) if c.seq >= start_seq]
            window = all_chunks[:max_chunks]
            has_more = len(all_chunks) > len(window)
            next_start_seq = window[-1].seq + 1 if has_more and window else None

            return ReadDocumentOut(
                document_id=document.id,
                file_name=document.file_name,
                state=document.state.value,
                chunks=[ChunkOut(seq=c.seq, text=c.text) for c in window],
                has_more=has_more,
                next_start_seq=next_start_seq,
            )
        finally:
            conn.close()
