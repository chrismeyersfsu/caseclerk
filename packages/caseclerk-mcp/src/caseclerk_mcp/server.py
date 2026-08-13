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

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from caseclerk_core import db, scan
from caseclerk_core.config import Config, load_config
from caseclerk_core.paths import PathContainmentError, safe_join
from caseclerk_core.update import current_version
from caseclerk_mcp.audit import AuditMiddleware
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.oauth import OAUTH_SCOPE, CaseClerkOAuthProvider
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
    if deps.documents_root is None:
        logger.warning("startup scan skipped: documentsRoot is not configured")
        return
    try:
        conn = deps.open_db()
    except Exception:
        logger.exception("startup scan: could not open the database")
        return
    try:
        scan.scan(
            conn,
            deps.documents_root,
            emails_folder_name=deps.config.emails_folder_name,
            ignore_globs=deps.config.processing.ignore,
        )

        documents_root = deps.documents_root

        def case_dir_for(case_id: int) -> Path:
            case = db.get_case(conn, case_id)
            if case is None:
                raise PathContainmentError(f"unknown case_id {case_id}")
            return safe_join(documents_root, case.rel_path)

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


def _resource_base_url(cfg: Config, *, port: int) -> AnyHttpUrl:
    """The MCP server's own canonical URL: the OAuth issuer AND resource_server_url (this
    process is both the resource server and its own minimal authorization server). Prefers
    the public tunnel hostname; falls back to a local http:// URL for dev/testing when no
    hostname is configured yet."""
    if cfg.share.hostname:
        return AnyHttpUrl(f"https://{cfg.share.hostname}/")
    return AnyHttpUrl(f"http://127.0.0.1:{port}/")


def build_server(
    config: Config | None = None,
    *,
    documents_root: Path | str | None = None,
    db_path: Path | None = None,
    run_startup_scan: bool = True,
    http_auth: bool = False,
    http_port: int | None = None,
) -> MCPServer[None]:
    """Construct the caseclerk MCPServer with every tool and the draft-email prompt registered.

    http_auth=True adds the OAuth authorization/resource server and the audit-log
    middleware -- used only for the HTTP transport; stdio (the default) has neither.
    http_port is the actual port the HTTP transport will bind (falls back to
    cfg.share.port if not given); it must match the real bound port exactly, since it
    feeds the OAuth issuer/resource URL whenever no public hostname is configured yet.
    """
    cfg = config or load_config()
    root_str = str(documents_root) if documents_root is not None else cfg.documents_root
    root = Path(root_str) if root_str else None
    deps = Deps(config=cfg, documents_root=root, prompts_dir=resolve_prompts_dir(cfg), db_path=db_path)

    extra_kwargs: dict[str, object] = {}
    if http_auth:
        base_url = _resource_base_url(cfg, port=http_port if http_port is not None else cfg.share.port)
        extra_kwargs["middleware"] = [AuditMiddleware(db_path=db_path)]
        extra_kwargs["auth_server_provider"] = CaseClerkOAuthProvider(db_path=db_path)
        extra_kwargs["auth"] = AuthSettings(
            issuer_url=base_url,
            resource_server_url=base_url,
            client_registration_options=ClientRegistrationOptions(
                enabled=True, default_scopes=[OAUTH_SCOPE], valid_scopes=[OAUTH_SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[OAUTH_SCOPE],
        )

    server: MCPServer[None] = MCPServer(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        version=current_version(),
        lifespan=_make_lifespan(deps, run_startup_scan=run_startup_scan),
        **extra_kwargs,  # type: ignore[arg-type]
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


def transport_security_settings(cfg: Config) -> TransportSecuritySettings:
    """DNS-rebinding protection that also admits the public share hostname.

    Left to its own devices the SDK auto-allows only localhost Host headers when
    bound to 127.0.0.1, so requests arriving through the cloudflared tunnel
    (Host: <share.hostname>) get 421 Misdirected Request AFTER passing OAuth --
    which a connector UI surfaces as a generic connection failure.
    """
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    if cfg.share.hostname:
        allowed_hosts += [cfg.share.hostname, f"{cfg.share.hostname}:443"]
        allowed_origins.append(f"https://{cfg.share.hostname}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def serve_http(config: Config | None = None, *, port: int | None = None) -> None:
    """Entry point for `caseclerk serve --transport http`: streamable HTTP, bound to
    127.0.0.1 ONLY -- there is no host parameter, deliberately; a cloudflared tunnel
    (`caseclerk share start`) is the only supported way to expose this beyond localhost."""
    cfg = config or load_config()
    effective_port = port if port is not None else cfg.share.port
    server = build_server(cfg, http_auth=True, http_port=effective_port)
    server.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=effective_port,
        transport_security=transport_security_settings(cfg),
    )
