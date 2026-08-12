from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from conftest import Env

from caseclerk_core.config import Config


def test_save_email_draft_writes_into_the_configured_emails_folder(env: Env) -> None:
    env.seed_case("Alvarez, Maria", "2026-0142")

    result = env.call(
        "save_email_draft",
        client="Alvarez, Maria",
        case_number="2026-0142",
        subject="Deposition conflict",
        body="Tell him about the conflict.",
        slug="Deposition Conflict",
        recipient="lawyer@example.com",
        citations=["deposition-transcript.docx"],
    )
    payload = result.structured_content
    eml_path = Path(payload["eml_path"])
    txt_path = Path(payload["txt_path"])

    expected_dir = env.documents_root / "Alvarez, Maria" / "2026-0142" / "emails-generated"
    assert eml_path.parent == expected_dir
    assert txt_path.parent == expected_dir
    assert eml_path.exists()
    assert txt_path.exists()
    assert b"X-Unsent: 1" in eml_path.read_bytes()
    assert "deposition-transcript.docx" in txt_path.read_text(encoding="utf-8")


def test_save_email_draft_never_overwrites(env: Env) -> None:
    env.seed_case("Alvarez, Maria", "2026-0142")

    first = env.call(
        "save_email_draft",
        client="Alvarez, Maria",
        case_number="2026-0142",
        subject="s1",
        body="first",
        slug="conflict",
    )
    second = env.call(
        "save_email_draft",
        client="Alvarez, Maria",
        case_number="2026-0142",
        subject="s2",
        body="second",
        slug="conflict",
    )

    first_path = Path(first.structured_content["eml_path"])
    second_path = Path(second.structured_content["eml_path"])
    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()


def test_save_email_draft_respects_configured_folder_name_and_template(
    make_env: Callable[..., Env],
) -> None:
    # documents_root is intentionally left unset here: make_env() always passes its own
    # temp documents_root explicitly to build_server(), which takes precedence over config.
    cfg = Config(emails_folder_name="drafts", email_file_name_template="{case}-{slug}")
    custom_env = make_env(config=cfg)
    custom_env.seed_case("Barrett Holdings LLC", "2026-0201")

    result = custom_env.call(
        "save_email_draft",
        client="Barrett Holdings LLC",
        case_number="2026-0201",
        subject="s",
        body="b",
        slug="intro",
    )
    eml_path = Path(result.structured_content["eml_path"])
    assert eml_path.parent.name == "drafts"
    assert eml_path.name == "2026-0201-intro.eml"
