"""draft-email prompt: renders prompts/email-draft.md, user-overridable via config.promptsDir."""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver import MCPServer

from caseclerk_core.config import Config
from caseclerk_mcp.deps import Deps

_PACKAGED_PROMPTS_DIR = Path(__file__).parent / "prompts"
TEMPLATE_NAME = "email-draft.md"


def resolve_prompts_dir(config: Config) -> Path:
    return Path(config.prompts_dir) if config.prompts_dir else _PACKAGED_PROMPTS_DIR


def register_prompts(server: MCPServer[None], deps: Deps) -> None:
    @server.prompt(name="draft-email")
    def draft_email(client: str, case_number: str, request: str) -> str:
        """Draft an email for one client/case: gather facts via overview/search/read, cite
        documents, then call save_email_draft."""
        template = (deps.prompts_dir / TEMPLATE_NAME).read_text(encoding="utf-8")
        return template.format(client=client, case_number=case_number, request=request)
