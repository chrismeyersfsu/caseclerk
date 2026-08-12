"""search_case: full-text search, always scoped to exactly one case."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from caseclerk_core import db
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.schemas import SearchHitOut
from caseclerk_mcp.tools._common import require_case
from caseclerk_pipeline.dates import date_query_variants

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)


def register_search_tools(server: MCPServer[None], deps: Deps) -> None:
    @server.tool(annotations=_READ_ONLY)
    def search_case(
        client: str,
        case_number: str,
        queries: list[str],
        date: str | None = None,
        limit: int = 20,
    ) -> list[SearchHitOut]:
        """Full-text search within one case's documents. `date` is an ISO date (YYYY-MM-DD)
        and is expanded into the written forms a document might actually contain
        (e.g. "April 21, 2026", "4/21/26"), added alongside `queries`."""
        conn = deps.open_db()
        try:
            case_id = require_case(conn, client, case_number)
            all_queries = [*queries]
            if date:
                all_queries.extend(date_query_variants(date))
            if not all_queries:
                raise ValueError("provide at least one query in `queries` or a `date`")
            hits = db.search_case(conn, case_id, all_queries, limit=limit)
            return [
                SearchHitOut(
                    document_id=hit.document_id,
                    chunk_id=hit.chunk_id,
                    file_name=hit.file_name,
                    seq=hit.seq,
                    snippet=hit.snippet,
                )
                for hit in hits
            ]
        finally:
            conn.close()
