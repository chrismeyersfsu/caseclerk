from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import Env
from mcp_types import GetPromptResult, TextContent

from caseclerk_core.config import Config


def _first_message_text(result: GetPromptResult) -> str:
    content = result.messages[0].content
    assert isinstance(content, TextContent)
    return content.text


def test_draft_email_prompt_renders_client_case_and_request(env: Env) -> None:
    result = env.get_prompt(
        "draft-email",
        client="Alvarez, Maria",
        case_number="2026-0142",
        request="tell him about the conflict",
    )
    text = _first_message_text(result)
    assert "Alvarez, Maria" in text
    assert "2026-0142" in text
    assert "tell him about the conflict" in text
    assert "save_email_draft" in text
    assert "get_case_overview" in text


def test_draft_email_prompt_honors_prompts_dir_override(make_env: Callable[..., Env], tmp_path: Path) -> None:
    custom_dir = tmp_path / "custom-prompts"
    custom_dir.mkdir()
    (custom_dir / "email-draft.md").write_text(
        "CUSTOM TEMPLATE for {client} / {case_number}: {request}", encoding="utf-8"
    )
    cfg = Config(prompts_dir=str(custom_dir))
    custom_env = make_env(config=cfg)

    result = custom_env.get_prompt(
        "draft-email", client="Alvarez, Maria", case_number="2026-0142", request="hello"
    )
    text = _first_message_text(result)
    assert text == "CUSTOM TEMPLATE for Alvarez, Maria / 2026-0142: hello"
