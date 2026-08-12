from datetime import UTC, datetime
from pathlib import Path

from caseclerk_artifacts.email import (
    MAX_FILENAME_STEM_LENGTH,
    build_eml,
    build_txt,
    render_file_name_template,
    sanitize_filename_component,
    slugify,
    write_email_draft,
)

FIXED_NOW = datetime(2026, 8, 12, 14, 30, tzinfo=UTC)


def test_slugify_lowercases_and_dashes() -> None:
    assert slugify("Deposition Conflict!!") == "deposition-conflict"
    assert slugify("  already-a-slug  ") == "already-a-slug"
    assert slugify("") == "draft"
    assert slugify("####") == "draft"


def test_render_file_name_template_tokens() -> None:
    rendered = render_file_name_template(
        "{yyyy}-{mm}-{dd}-{slug}", now=FIXED_NOW, slug="deposition-conflict", case_number="2026-0142"
    )
    assert rendered == "2026-08-12-deposition-conflict"


def test_render_file_name_template_accepts_case_token() -> None:
    rendered = render_file_name_template(
        "{case}-{slug}", now=FIXED_NOW, slug="conflict", case_number="2026-0142"
    )
    assert rendered == "2026-0142-conflict"


def test_render_file_name_template_zero_pads_month_and_day() -> None:
    rendered = render_file_name_template(
        "{yyyy}-{mm}-{dd}", now=datetime(2026, 1, 5, tzinfo=UTC), slug="x", case_number="c"
    )
    assert rendered == "2026-01-05"


def test_sanitize_strips_windows_forbidden_characters() -> None:
    assert sanitize_filename_component('bad<>:"/\\|?*name') == "badname"


def test_sanitize_strips_control_characters() -> None:
    assert sanitize_filename_component("bad\x00\x1fname\x7f") == "badname"


def test_sanitize_strips_trailing_dot_and_space() -> None:
    assert sanitize_filename_component("trailing dot. ") == "trailing dot"


def test_sanitize_renames_reserved_device_names() -> None:
    for reserved in ("CON", "con", "PRN", "AUX", "NUL", "COM1", "com9", "LPT1", "lpt9"):
        result = sanitize_filename_component(reserved)
        assert result != reserved.upper()
        assert result.upper() not in {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
            f"LPT{i}" for i in range(1, 10)
        }


def test_sanitize_does_not_flag_names_merely_containing_a_reserved_word() -> None:
    # "CONFERENCE" contains "CON" but is not itself a reserved device name
    assert sanitize_filename_component("CONFERENCE-notes") == "CONFERENCE-notes"


def test_sanitize_caps_length() -> None:
    result = sanitize_filename_component("a" * 500)
    assert len(result) <= MAX_FILENAME_STEM_LENGTH


def test_sanitize_empty_input_falls_back_to_draft() -> None:
    assert sanitize_filename_component("") == "draft"
    assert sanitize_filename_component("***") == "draft"


def test_build_eml_has_x_unsent_header_and_crlf() -> None:
    eml = build_eml(
        subject="Deposition conflict", body="Hello,\n\nDetails follow.", recipient=None, now=FIXED_NOW
    )
    assert b"X-Unsent: 1" in eml
    assert b"\r\n" in eml
    assert b"\r\r\n" not in eml  # not double-converted
    # every bare LF must be part of a CRLF pair
    assert eml.count(b"\n") == eml.count(b"\r\n")


def test_build_eml_includes_subject_and_recipient() -> None:
    eml = build_eml(
        subject="Deposition conflict", body="body text", recipient="lawyer@example.com", now=FIXED_NOW
    )
    assert b"Subject: Deposition conflict" in eml
    assert b"To: lawyer@example.com" in eml


def test_build_eml_without_recipient_omits_to_header() -> None:
    eml = build_eml(subject="s", body="b", recipient=None, now=FIXED_NOW)
    assert b"To:" not in eml


def test_build_txt_uses_crlf_and_has_sources_appendix() -> None:
    txt = build_txt(
        subject="Deposition conflict",
        body="Hello,\n\nDetails follow.",
        recipient="lawyer@example.com",
        citations=["Deposition transcript, p. 4", "Retainer letter"],
        now=FIXED_NOW,
    )
    assert txt.count(b"\n") == txt.count(b"\r\n")
    decoded = txt.decode("utf-8")
    assert "Sources" in decoded
    assert "- Deposition transcript, p. 4" in decoded
    assert "- Retainer letter" in decoded
    assert "Hello," in decoded


def test_build_txt_without_citations_has_no_sources_section() -> None:
    txt = build_txt(subject="s", body="b", recipient=None, citations=[], now=FIXED_NOW)
    assert b"Sources" not in txt


def test_write_email_draft_writes_matched_pair(tmp_path: Path) -> None:
    case_dir = tmp_path / "Alvarez, Maria" / "2026-0142"
    case_dir.mkdir(parents=True)

    eml_path, txt_path = write_email_draft(
        case_dir,
        "emails-generated",
        "{yyyy}-{mm}-{dd}-{slug}",
        subject="Deposition conflict",
        body="Tell him about the conflict.",
        slug="Deposition Conflict",
        case_number="2026-0142",
        recipient="lawyer@example.com",
        citations=["Deposition transcript"],
        now=FIXED_NOW,
    )

    assert eml_path.parent == case_dir / "emails-generated"
    assert eml_path.name == "2026-08-12-deposition-conflict.eml"
    assert txt_path.name == "2026-08-12-deposition-conflict.txt"
    assert eml_path.exists()
    assert txt_path.exists()
    assert b"X-Unsent: 1" in eml_path.read_bytes()
    assert "Sources" in txt_path.read_text(encoding="utf-8")


def test_write_email_draft_never_overwrites_and_uniquifies(tmp_path: Path) -> None:
    case_dir = tmp_path / "Alvarez, Maria" / "2026-0142"
    case_dir.mkdir(parents=True)

    first_eml, first_txt = write_email_draft(
        case_dir,
        "emails-generated",
        "{yyyy}-{mm}-{dd}-{slug}",
        subject="s1",
        body="first body",
        slug="conflict",
        case_number="2026-0142",
        now=FIXED_NOW,
    )
    second_eml, second_txt = write_email_draft(
        case_dir,
        "emails-generated",
        "{yyyy}-{mm}-{dd}-{slug}",
        subject="s2",
        body="second body",
        slug="conflict",
        case_number="2026-0142",
        now=FIXED_NOW,
    )
    third_eml, third_txt = write_email_draft(
        case_dir,
        "emails-generated",
        "{yyyy}-{mm}-{dd}-{slug}",
        subject="s3",
        body="third body",
        slug="conflict",
        case_number="2026-0142",
        now=FIXED_NOW,
    )

    assert first_eml.name == "2026-08-12-conflict.eml"
    assert second_eml.name == "2026-08-12-conflict-2.eml"
    assert third_eml.name == "2026-08-12-conflict-3.eml"
    assert second_txt.name == "2026-08-12-conflict-2.txt"
    assert third_txt.name == "2026-08-12-conflict-3.txt"

    # the original file's content is untouched by later writes
    assert "first body" in first_txt.read_text(encoding="utf-8")
    assert "second body" in second_txt.read_text(encoding="utf-8")
    assert "third body" in third_txt.read_text(encoding="utf-8")


def test_write_email_draft_creates_emails_folder_if_missing(tmp_path: Path) -> None:
    case_dir = tmp_path / "Barrett Holdings LLC" / "2026-0201"
    case_dir.mkdir(parents=True)
    assert not (case_dir / "emails-generated").exists()

    write_email_draft(
        case_dir,
        "emails-generated",
        "{yyyy}-{mm}-{dd}-{slug}",
        subject="s",
        body="b",
        slug="x",
        case_number="2026-0201",
        now=FIXED_NOW,
    )
    assert (case_dir / "emails-generated").is_dir()


def test_write_email_draft_sanitizes_a_reserved_device_name_slug(tmp_path: Path) -> None:
    case_dir = tmp_path / "Alvarez, Maria" / "2026-0142"
    case_dir.mkdir(parents=True)

    eml_path, _txt_path = write_email_draft(
        case_dir,
        "emails-generated",
        "{slug}",
        subject="s",
        body="b",
        slug="con",
        case_number="2026-0142",
        now=FIXED_NOW,
    )
    assert eml_path.stem.upper() != "CON"
