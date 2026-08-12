"""HTTP-transport-only audit logging: one remote_requests row per tools/call.

Implemented as a ServerMiddleware so tool implementations are never touched;
stdio serving simply never constructs this middleware, so it never logs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.context import CallNext, HandlerResult, ServerRequestContext

from caseclerk_core import db

logger = logging.getLogger(__name__)

ARGS_SUMMARY_MAX_LENGTH = 500
_AUDITED_METHOD = "tools/call"


def _summarize_args(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    arguments = params.get("arguments")
    if arguments is None:
        return None
    try:
        text = json.dumps(arguments, default=str)
    except (TypeError, ValueError):
        text = str(arguments)
    if len(text) > ARGS_SUMMARY_MAX_LENGTH:
        text = text[:ARGS_SUMMARY_MAX_LENGTH] + "…"
    return text


def _error_text(result: object) -> str:
    content = getattr(result, "content", None) or []
    joined = "; ".join(getattr(block, "text", "") for block in content if getattr(block, "text", ""))
    return joined or "tool reported an error"


@dataclass(frozen=True)
class AuditMiddleware:
    """Pass to `MCPServer(middleware=[AuditMiddleware(db_path)])`. Audits tools/call only."""

    db_path: Path | None = None

    async def __call__(self, ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        if ctx.method != _AUDITED_METHOD:
            return await call_next(ctx)

        params = ctx.params
        tool_name = params.get("name", "?") if isinstance(params, dict) else "?"
        args_summary = _summarize_args(params)

        try:
            result = await call_next(ctx)
        except Exception as exc:
            self._record(
                tool=tool_name, args_summary=args_summary, ok=False, error=f"{type(exc).__name__}: {exc}"
            )
            raise

        is_error = bool(getattr(result, "is_error", False))
        error = _error_text(result) if is_error else None
        self._record(tool=tool_name, args_summary=args_summary, ok=not is_error, error=error)
        return result

    def _record(self, *, tool: str, args_summary: str | None, ok: bool, error: str | None) -> None:
        try:
            conn = db.connect(self.db_path)
            try:
                db.insert_remote_request(conn, tool=tool, args_summary=args_summary, ok=ok, error=error)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 - a broken audit write must never break the real request
            logger.exception("failed to write audit log entry for tool %s", tool)
