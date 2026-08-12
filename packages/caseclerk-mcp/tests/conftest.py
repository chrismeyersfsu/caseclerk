import asyncio
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, GetPromptResult

from caseclerk_core import db
from caseclerk_core.config import Config
from caseclerk_core.models import DocumentState
from caseclerk_mcp.server import build_server


@dataclass
class Env:
    tmp_path: Path
    clio_root: Path
    db_path: Path
    server: MCPServer[None]

    def call(self, name: str, **arguments: Any) -> CallToolResult:
        result = asyncio.run(self.server.call_tool(name, arguments))
        assert isinstance(result, CallToolResult)
        return result

    def get_prompt(self, name: str, **arguments: str) -> GetPromptResult:
        result = asyncio.run(self.server.get_prompt(name, arguments))
        assert isinstance(result, GetPromptResult)
        return result

    def open_db(self) -> sqlite3.Connection:
        return db.connect(self.db_path)

    def seed_case(
        self, client: str, case_number: str, documents: Iterable[tuple[str, str]] = ()
    ) -> tuple[int, list[int]]:
        conn = self.open_db()
        try:
            client_id = db.upsert_client(conn, client)
            case_id = db.upsert_case(conn, client_id, case_number, f"{client}/{case_number}")
            case_dir = self.clio_root / client / case_number
            case_dir.mkdir(parents=True, exist_ok=True)
            doc_ids = []
            for rel_path, text in documents:
                doc_id = db.upsert_document(
                    conn,
                    case_id=case_id,
                    rel_path=rel_path,
                    file_name=Path(rel_path).name,
                    ext=Path(rel_path).suffix.lower(),
                    size=len(text),
                    mtime_ms=1,
                    content_hash="seed",
                    state=DocumentState.INDEXED,
                    words=len(text.split()),
                )
                db.replace_chunks(conn, doc_id, [(0, text, max(1, len(text) // 4))])
                doc_ids.append(doc_id)
            return case_id, doc_ids
        finally:
            conn.close()


@pytest.fixture
def make_env(tmp_path: Path) -> Callable[..., Env]:
    def _make(*, config: Config | None = None, run_startup_scan: bool = False) -> Env:
        clio_root = tmp_path / "clio"
        clio_root.mkdir(parents=True, exist_ok=True)
        db_path = tmp_path / "caseclerk.db"
        cfg = config or Config(clio_root=str(clio_root))
        server = build_server(cfg, clio_root=clio_root, db_path=db_path, run_startup_scan=run_startup_scan)
        return Env(tmp_path=tmp_path, clio_root=clio_root, db_path=db_path, server=server)

    return _make


@pytest.fixture
def env(make_env: Callable[..., Env]) -> Env:
    return make_env()
