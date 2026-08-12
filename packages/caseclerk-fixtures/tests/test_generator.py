from __future__ import annotations

from pathlib import Path

from caseclerk_fixtures import (
    CONFLICT_DATE_ISO,
    DEMO_CASE,
    DEMO_CLIENT,
    OPPOSING_COUNSEL_LINE,
    OPPOSING_PARTY,
    OTHER_CLIENT,
    OTHER_CLIENT_CASE_1,
    OTHER_CLIENT_CASE_2,
    SECOND_ALVAREZ_CASE,
    build_fixture_drive,
)
from caseclerk_pipeline.dates import find_dates
from caseclerk_pipeline.extractors.docx import extract_docx
from caseclerk_pipeline.extractors.pdf import NeedsOcrError, extract_pdf
from caseclerk_pipeline.extractors.registry import is_unsupported


def _deescape(text: str) -> str:
    """mammoth's markdown output backslash-escapes punctuation (".", "-", ...); undo that
    for content assertions, since the escaped form is an implementation detail of the
    extractor, not something a search/read tool consumer should have to know about."""
    return text.replace("\\", "")


def test_build_fixture_drive_creates_the_expected_tree(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    expected = {
        f"{DEMO_CLIENT}/{DEMO_CASE}/deposition-transcript.docx",
        f"{DEMO_CLIENT}/{DEMO_CASE}/security-report.pdf",
        f"{DEMO_CLIENT}/{DEMO_CASE}/correspondence-with-opposing-counsel.docx",
        f"{DEMO_CLIENT}/{DEMO_CASE}/client-intake-notes.txt",
        f"{DEMO_CLIENT}/{DEMO_CASE}/old-invoice.xlsx",
        f"{DEMO_CLIENT}/{DEMO_CASE}/scanned-exhibit.pdf",
        f"{DEMO_CLIENT}/{SECOND_ALVAREZ_CASE}/retainer-agreement.docx",
        f"{DEMO_CLIENT}/{SECOND_ALVAREZ_CASE}/case-notes.txt",
        f"{OTHER_CLIENT}/{OTHER_CLIENT_CASE_1}/contract-draft.docx",
        f"{OTHER_CLIENT}/{OTHER_CLIENT_CASE_1}/inspection-report.pdf",
        f"{OTHER_CLIENT}/{OTHER_CLIENT_CASE_2}/meeting-notes.txt",
        f"{OTHER_CLIENT}/{OTHER_CLIENT_CASE_2}/correspondence.docx",
    }
    # .as_posix(), not str(): on Windows, Path's str form uses backslashes, which would
    # never match the forward-slash literals above
    actual = {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}
    assert actual == expected


def test_build_fixture_drive_is_deterministic(tmp_path: Path) -> None:
    first = build_fixture_drive(tmp_path / "one")
    second = build_fixture_drive(tmp_path / "two")
    first_notes = (first / DEMO_CLIENT / DEMO_CASE / "client-intake-notes.txt").read_bytes()
    second_notes = (second / DEMO_CLIENT / DEMO_CASE / "client-intake-notes.txt").read_bytes()
    assert first_notes == second_notes


def test_build_fixture_drive_is_idempotent_on_rebuild(tmp_path: Path) -> None:
    dest = tmp_path / "clio"
    build_fixture_drive(dest)
    before = (dest / DEMO_CLIENT / DEMO_CASE / "client-intake-notes.txt").read_bytes()
    build_fixture_drive(dest)
    after = (dest / DEMO_CLIENT / DEMO_CASE / "client-intake-notes.txt").read_bytes()
    assert before == after


def test_deposition_transcript_contains_the_april_21_conflict(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    path = dest / DEMO_CLIENT / DEMO_CASE / "deposition-transcript.docx"

    markdown = _deescape(extract_docx(path))
    assert OPPOSING_PARTY in markdown
    assert "Tampa" in markdown
    assert find_dates(markdown) == [CONFLICT_DATE_ISO]


def test_security_report_places_opposing_party_in_clearwater_same_day(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    path = dest / DEMO_CLIENT / DEMO_CASE / "security-report.pdf"

    text = _deescape(extract_pdf(path))
    assert "Clearwater" in text
    assert "Whitfield" in text
    assert find_dates(text) == [CONFLICT_DATE_ISO]


def test_correspondence_contains_opposing_counsel_contact(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    path = dest / DEMO_CLIENT / DEMO_CASE / "correspondence-with-opposing-counsel.docx"

    markdown = _deescape(extract_docx(path))
    assert OPPOSING_COUNSEL_LINE in markdown


def test_scanned_exhibit_needs_ocr(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    path = dest / DEMO_CLIENT / DEMO_CASE / "scanned-exhibit.pdf"
    try:
        extract_pdf(path)
    except NeedsOcrError:
        pass
    else:
        raise AssertionError("expected NeedsOcrError for a blank/scanned PDF")


def test_unsupported_file_extension_is_flagged(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    path = dest / DEMO_CLIENT / DEMO_CASE / "old-invoice.xlsx"
    assert path.exists()
    assert is_unsupported(path.suffix)


def test_second_alvarez_case_docx_extracts_cleanly(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    path = dest / DEMO_CLIENT / SECOND_ALVAREZ_CASE / "retainer-agreement.docx"
    markdown = extract_docx(path)
    assert markdown.strip()


def test_barrett_case_pdf_extracts_cleanly(tmp_path: Path) -> None:
    dest = build_fixture_drive(tmp_path / "clio")
    path = dest / OTHER_CLIENT / OTHER_CLIENT_CASE_1 / "inspection-report.pdf"
    text = extract_pdf(path)
    assert "inspection" in text.lower() or "Inspection" in text
