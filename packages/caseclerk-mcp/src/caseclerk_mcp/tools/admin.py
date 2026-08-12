"""processing_status, list_processing_failures, reprocess_document, get_settings."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from caseclerk_core import db
from caseclerk_core.update import META_AVAILABLE_VERSION, current_version
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.schemas import ProcessingFailureOut, ProcessingStatusOut, ReprocessOut, SettingsOut
from caseclerk_mcp.tools._common import require_case, require_document

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def register_admin_tools(server: MCPServer[None], deps: Deps) -> None:
    @server.tool(annotations=_READ_ONLY)
    def processing_status() -> ProcessingStatusOut:
        """Queue/indexed/failed counts and the cached update-available version, if any.
        Never checks the network -- it only reads the last cached update check."""
        conn = deps.open_db()
        try:
            status = db.status_counts(conn)
            cached_update = db.get_meta(conn, META_AVAILABLE_VERSION)
        finally:
            conn.close()
        return ProcessingStatusOut(
            total=status.total,
            by_state={state.value: count for state, count in status.by_state.items()},
            update_available=cached_update or None,
        )

    @server.tool(annotations=_READ_ONLY)
    def list_processing_failures() -> list[ProcessingFailureOut]:
        """Every document currently in the failed state, with its error and attempt count."""
        conn = deps.open_db()
        try:
            return [
                ProcessingFailureOut(
                    document_id=failure.document_id,
                    file_name=failure.file_name,
                    case_number=failure.case_number,
                    client_name=failure.client_name,
                    error=failure.error,
                    attempts=failure.attempts,
                )
                for failure in db.list_failures(conn)
            ]
        finally:
            conn.close()

    @server.tool(annotations=_WRITE)
    def reprocess_document(client: str, case_number: str, document_id: int) -> ReprocessOut:
        """Requeue one document for processing (e.g. after fixing a needs-OCR PDF upstream).
        Does not process it immediately -- it becomes pending again for the next queue run."""
        conn = deps.open_db()
        try:
            case_id = require_case(conn, client, case_number)
            require_document(conn, case_id, document_id, case_number=case_number, client=client)
            job_id = db.requeue_document(conn, document_id)
        finally:
            conn.close()
        return ReprocessOut(document_id=document_id, job_id=job_id, state="pending")

    @server.tool(annotations=_READ_ONLY)
    def get_settings() -> SettingsOut:
        """Effective configuration and app version. Read-only -- edits go through the CLI.
        Never echoes secrets: summarization.apiKeyEnv (if any) is only ever an env var name,
        and its value is never read here."""
        cfg = deps.config
        return SettingsOut(
            version=current_version(),
            clio_root=str(deps.clio_root) if deps.clio_root else None,
            emails_folder_name=cfg.emails_folder_name,
            email_file_name_template=cfg.email_file_name_template,
            processing_concurrency=cfg.processing.concurrency,
            processing_watch=cfg.processing.watch,
            updates_auto=cfg.updates.auto,
            updates_check_interval_hours=cfg.updates.check_interval_hours,
            summarization_enabled=cfg.summarization.enabled,
            summarization_provider=cfg.summarization.provider,
        )
