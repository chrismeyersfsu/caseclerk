"""save_email_draft: the one write tool, and the only path that touches
<case>/<emailsFolderName>/."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp_types import ToolAnnotations

from caseclerk_artifacts.email import write_email_draft
from caseclerk_mcp.deps import Deps
from caseclerk_mcp.schemas import SaveEmailDraftOut
from caseclerk_mcp.tools._common import require_case

_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)


def register_email_tools(server: MCPServer[None], deps: Deps) -> None:
    @server.tool(annotations=_WRITE)
    def save_email_draft(
        client: str,
        case_number: str,
        subject: str,
        body: str,
        slug: str,
        recipient: str | None = None,
        citations: list[str] | None = None,
    ) -> SaveEmailDraftOut:
        """Write a .eml + .txt draft into <case>/<emailsFolderName>/. Never overwrites an
        existing draft -- a name collision gets a numbered suffix instead. `slug` should be
        a few words describing the email (e.g. "deposition-conflict")."""
        conn = deps.open_db()
        try:
            require_case(conn, client, case_number)
        finally:
            conn.close()

        case_dir = deps.case_directory(client, case_number)
        eml_path, txt_path = write_email_draft(
            case_dir,
            deps.config.emails_folder_name,
            deps.config.email_file_name_template,
            subject=subject,
            body=body,
            slug=slug,
            case_number=case_number,
            recipient=recipient,
            citations=citations or (),
        )
        return SaveEmailDraftOut(eml_path=str(eml_path), txt_path=str(txt_path))
