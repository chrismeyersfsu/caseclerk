"""Synthetic Clio Drive fixture generator: two invented clients, two cases each.

All content is fabricated for testing; no real names, firms, or client
data appear anywhere. The demo case (Alvarez, Maria / 2026-0142) carries
the April 21, 2026 alibi conflict the CaseClerk demo/e2e happy path is
built around: a deposition transcript in which the opposing party claims
to have been in Tampa, and a security report placing him in Clearwater
the same day. Content is fixed, literal strings -- no randomness -- so
the generated tree is byte-for-byte deterministic.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument
from fpdf import FPDF

DEMO_CLIENT = "Alvarez, Maria"
DEMO_CASE = "2026-0142"
SECOND_ALVAREZ_CASE = "2026-0143"
OTHER_CLIENT = "Barrett Holdings LLC"
OTHER_CLIENT_CASE_1 = "2026-0201"
OTHER_CLIENT_CASE_2 = "2026-0202"

OPPOSING_PARTY = "Daniel Whitfield"
OPPOSING_COUNSEL_LINE = "R. Calloway, calloway@example-firm.com"
CONFLICT_DATE = "April 21, 2026"
CONFLICT_DATE_ISO = "2026-04-21"

DEPOSITION_TRANSCRIPT_FILE = "deposition-transcript.docx"
SECURITY_REPORT_FILE = "security-report.pdf"
CORRESPONDENCE_FILE = "correspondence-with-opposing-counsel.docx"
INTAKE_NOTES_FILE = "client-intake-notes.txt"
UNSUPPORTED_FILE = "old-invoice.xlsx"
SCANNED_EXHIBIT_FILE = "scanned-exhibit.pdf"

DEPOSITION_TRANSCRIPT = [
    f"DEPOSITION OF {OPPOSING_PARTY.upper()}",
    f"Witness: {OPPOSING_PARTY}",
    f"Taken {CONFLICT_DATE}",
    "",
    f"Q: Mr. Whitfield, where were you on {CONFLICT_DATE}?",
    "A: I was at a work conference in Tampa, Florida, all day. I did not leave the conference.",
    "",
    "Q: Can anyone confirm that?",
    "A: Several colleagues saw me there. I have no reason to lie about my whereabouts.",
    "",
    f"Q: Are you certain you were in Tampa the entire day of {CONFLICT_DATE}?",
    "A: Yes, I am certain.",
]

SECURITY_REPORT = [
    "SECURITY LOG -- Sunset Bay Property, Clearwater, FL",
    f"Date: {CONFLICT_DATE}",
    "",
    "09:14 - Vehicle registered to D. Whitfield entered the gate.",
    "09:20 - D. Whitfield observed by front-desk camera entering the lobby.",
    "16:45 - D. Whitfield observed leaving via the west entrance.",
    "",
    "Note: subject was present on-site for the majority of the day, contradicting",
    "any claim of an out-of-town commitment on this date.",
]

CORRESPONDENCE = [
    "Re: Alvarez v. Whitfield -- Case No. 2026-0142",
    "",
    "Dear Ms. Alvarez,",
    "",
    "Please direct all further correspondence in this matter to opposing counsel:",
    "",
    OPPOSING_COUNSEL_LINE,
    "",
    "We look forward to resolving this matter promptly.",
    "",
    "Sincerely,",
    "Case Correspondence",
]

INTAKE_NOTES = (
    "Client intake notes.\n\n"
    "Client reports a scheduling conflict involving the opposing party's claimed\n"
    f"whereabouts on {CONFLICT_DATE}. See deposition transcript and security report.\n"
)

RETAINER_AGREEMENT = [
    "RETAINER AGREEMENT",
    "",
    f"This agreement covers representation in case {SECOND_ALVAREZ_CASE}.",
    "Scope of engagement: general matter follow-up.",
]

CASE_NOTES = f"Follow-up notes for case {SECOND_ALVAREZ_CASE}. No outstanding conflicts identified.\n"

CONTRACT_DRAFT = [
    "DRAFT SERVICES AGREEMENT",
    "",
    f"Parties: {OTHER_CLIENT} and a prospective vendor.",
    "Draft for internal review only.",
]

INSPECTION_REPORT = [
    "PROPERTY INSPECTION REPORT",
    "",
    f"Site: {OTHER_CLIENT} warehouse annex.",
    "Findings: routine maintenance items only, no structural concerns.",
]

MEETING_NOTES = "Meeting notes: quarterly review, no action items requiring counsel.\n"

BARRETT_CORRESPONDENCE = [
    f"Re: {OTHER_CLIENT} -- Case No. {OTHER_CLIENT_CASE_2}",
    "",
    "Confirming receipt of the requested documents.",
    "",
    "Sincerely,",
    "Case Correspondence",
]


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = DocxDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(str(path))


def _write_pdf(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(0, 8, text="\n".join(lines))
    pdf.output(str(path))


def _write_blank_pdf(path: Path) -> None:
    """A page with no text layer -- stands in for a scanned exhibit that needs OCR."""
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = FPDF()
    pdf.add_page()
    pdf.output(str(path))


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_unsupported(path: Path) -> None:
    """A placeholder for an extension caseclerk-pipeline marks unsupported and never parses."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"placeholder binary content, never parsed by any extractor\n")


def build_fixture_drive(dest: Path) -> Path:
    """Build a synthetic Clio Drive tree at dest (creating it if needed) and return dest.

    Deterministic and idempotent: re-running overwrites the same fixed content at the
    same paths, so callers can rebuild freely between test runs.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)

    demo_case = dest / DEMO_CLIENT / DEMO_CASE
    _write_docx(demo_case / DEPOSITION_TRANSCRIPT_FILE, DEPOSITION_TRANSCRIPT)
    _write_pdf(demo_case / SECURITY_REPORT_FILE, SECURITY_REPORT)
    _write_docx(demo_case / CORRESPONDENCE_FILE, CORRESPONDENCE)
    _write_text(demo_case / INTAKE_NOTES_FILE, INTAKE_NOTES)
    _write_unsupported(demo_case / UNSUPPORTED_FILE)
    _write_blank_pdf(demo_case / SCANNED_EXHIBIT_FILE)

    second_alvarez_case = dest / DEMO_CLIENT / SECOND_ALVAREZ_CASE
    _write_docx(second_alvarez_case / "retainer-agreement.docx", RETAINER_AGREEMENT)
    _write_text(second_alvarez_case / "case-notes.txt", CASE_NOTES)

    barrett_case_1 = dest / OTHER_CLIENT / OTHER_CLIENT_CASE_1
    _write_docx(barrett_case_1 / "contract-draft.docx", CONTRACT_DRAFT)
    _write_pdf(barrett_case_1 / "inspection-report.pdf", INSPECTION_REPORT)

    barrett_case_2 = dest / OTHER_CLIENT / OTHER_CLIENT_CASE_2
    _write_text(barrett_case_2 / "meeting-notes.txt", MEETING_NOTES)
    _write_docx(barrett_case_2 / "correspondence.docx", BARRETT_CORRESPONDENCE)

    return dest
