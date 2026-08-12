"""The caseclerk MCP server: stdio transport, tools + one prompt.

All logging goes through the stdlib `logging` module (which the mcp SDK
and every caseclerk_* module route to stderr) -- stdout is reserved for
the JSON-RPC stdio transport and nothing here ever calls print().
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from caseclerk_core import db, scan
from caseclerk_core.config import Config, load_config
from caseclerk_core.paths import PathContainmentError, safe_join
from caseclerk_core.update import current_version
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.prompts import register_prompts, resolve_prompts_dir
from caseclerk_mcp.tools.admin import register_admin_tools
from caseclerk_mcp.tools.browse import register_browse_tools
from caseclerk_mcp.tools.email import register_email_tools
from caseclerk_mcp.tools.read import register_read_tools
from caseclerk_mcp.tools.search import register_search_tools
from caseclerk_pipeline import queue

logger = logging.getLogger(__name__)

SERVER_NAME = "caseclerk"

INSTRUCTIONS = (
    "Every request concerns exactly one client. If the client or case is ambiguous, ask "
    "the user which client and case before calling any tool -- never guess. Typical "
    "workflow: get_case_overview, then search_case, then read_document, then "
    "save_email_draft for anything that produces a draft. Never combine, compare, or "
    "reference documents from more than one client in a single response."
)


def _startup_scan_and_queue(deps: Deps) -> None:
    """Runs off the main thread so a fresh drive gets indexed without blocking startup."""
    if deps.clio_root is None:
        logger.warning("startup scan skipped: clioRoot is not configured")
        return
    try:
        conn = deps.open_db()
    except Exception:
        logger.exception("startup scan: could not open the database")
        return
    try:
        scan.scan(
            conn,
            deps.clio_root,
            emails_folder_name=deps.config.emails_folder_name,
            ignore_globs=deps.config.processing.ignore,
        )

        clio_root = deps.clio_root

        def case_dir_for(case_id: int) -> Path:
            case = db.get_case(conn, case_id)
            if case is None:
                raise PathContainmentError(f"unknown case_id {case_id}")
            return safe_join(clio_root, case.rel_path)

        queue.run_queue(conn, case_dir_for, concurrency=deps.config.processing.concurrency)
    except Exception:
        logger.exception("startup scan/queue run failed")
    finally:
        conn.close()


def _make_lifespan(
    deps: Deps, *, run_startup_scan: bool
) -> Callable[[MCPServer[None]], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def _lifespan(_app: MCPServer[None]) -> AsyncIterator[None]:
        if run_startup_scan:
            threading.Thread(
                target=_startup_scan_and_queue, args=(deps,), daemon=True, name="caseclerk-startup-scan"
            ).start()
        yield None

    return _lifespan


def build_server(
    config: Config | None = None,
    *,
    clio_root: Path | str | None = None,
    db_path: Path | None = None,
    run_startup_scan: bool = True,
) -> MCPServer[None]:
    """Construct the caseclerk MCPServer with every tool and the draft-email prompt registered."""
    cfg = config or load_config()
    root_str = str(clio_root) if clio_root is not None else cfg.clio_root
    root = Path(root_str) if root_str else None
    deps = Deps(config=cfg, clio_root=root, prompts_dir=resolve_prompts_dir(cfg), db_path=db_path)

    server: MCPServer[None] = MCPServer(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=current_version(),
        lifespan=_make_lifespan(deps, run_startup_scan=run_startup_scan),
    )

    register_browse_tools(server, deps)
    register_search_tools(server, deps)
    register_read_tools(server, deps)
    register_email_tools(server, deps)
    register_admin_tools(server, deps)
    register_prompts(server, deps)

    return server


def serve(config: Config | None = None) -> None:
    """Entry point for `caseclerk serve`: run the stdio server until the client disconnects."""
    server = build_server(config)
    server.run(transport="stdio")
