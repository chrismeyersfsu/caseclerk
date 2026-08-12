from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import Env

from caseclerk_core import db
from caseclerk_core.config import Config
from caseclerk_core.models import DocumentState
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.prompts import resolve_prompts_dir
from caseclerk_mcp.server import _startup_scan_and_queue, build_server

EXPECTED_TOOL_NAMES = {
    "list_clients",
    "list_cases",
    "get_case_overview",
    "search_case",
    "read_document",
    "save_email_draft",
    "processing_status",
    "list_processing_failures",
    "reprocess_document",
    "get_settings",
}


def test_build_server_registers_every_tool(env: Env) -> None:
    tools = asyncio.run(env.server.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOL_NAMES


def test_build_server_registers_the_draft_email_prompt(env: Env) -> None:
    prompts = asyncio.run(env.server.list_prompts())
    assert [p.name for p in prompts] == ["draft-email"]


def test_read_only_tools_are_marked_read_only(env: Env) -> None:
    tools = {t.name: t for t in asyncio.run(env.server.list_tools())}
    for name in EXPECTED_TOOL_NAMES - {"save_email_draft", "reprocess_document"}:
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
    write_annotations = tools["save_email_draft"].annotations
    assert write_annotations is not None
    assert write_annotations.read_only_hint is False


def test_resolve_prompts_dir_defaults_to_packaged_template() -> None:
    default_dir = resolve_prompts_dir(Config())
    assert (default_dir / "email-draft.md").is_file()


def test_startup_scan_and_queue_indexes_a_fresh_drive(env: Env) -> None:
    case_dir = env.clio_root / "Alvarez, Maria" / "2026-0142"
    case_dir.mkdir(parents=True)
    (case_dir / "letter.txt").write_text("The deposition on April 21, 2026 revealed a conflict.")

    deps = Deps(
        config=Config(clio_root=str(env.clio_root)),
        clio_root=env.clio_root,
        prompts_dir=env.clio_root,
        db_path=env.db_path,
    )
    _startup_scan_and_queue(deps)

    conn = env.open_db()
    try:
        case_id = db.resolve_case_id(conn, "Alvarez, Maria", "2026-0142")
        assert case_id is not None
        documents = db.list_documents(conn, case_id)
        assert len(documents) == 1
        assert documents[0].state == DocumentState.INDEXED
    finally:
        conn.close()


def test_startup_scan_skipped_when_clio_root_unconfigured(tmp_path: Path) -> None:
    db_path = tmp_path / "caseclerk.db"
    deps = Deps(config=Config(), clio_root=None, prompts_dir=tmp_path, db_path=db_path)
    _startup_scan_and_queue(deps)  # must not raise

    conn = db.connect(db_path)
    try:
        assert db.list_clients(conn) == []
    finally:
        conn.close()


def test_build_server_reads_clio_root_from_config_when_not_overridden(tmp_path: Path) -> None:
    clio_root = tmp_path / "clio"
    clio_root.mkdir()
    cfg = Config(clio_root=str(clio_root))

    server = build_server(cfg, db_path=tmp_path / "caseclerk.db", run_startup_scan=False)

    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == EXPECTED_TOOL_NAMES
