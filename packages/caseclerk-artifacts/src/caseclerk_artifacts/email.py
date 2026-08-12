"""Email draft artifact: writes a matched .eml + .txt pair, never overwriting.

The .eml carries ``X-Unsent: 1`` so double-clicking it opens an
editable unsent draft in Outlook; the .txt is a plain read/print copy
with a Sources appendix listing the documents the draft cited. This is
the only write path CaseClerk has, and it only ever writes under
``<case>/<emailsFolderName>/``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime
from pathlib import Path

_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_WINDOWS_FORBIDDEN_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_SLUG_NONWORD_RE = re.compile(r"[^a-z0-9]+")
MAX_FILENAME_STEM_LENGTH = 150


def slugify(text: str) -> str:
    lowered = text.strip().lower()
    slug = _SLUG_NONWORD_RE.sub("-", lowered).strip("-")
    return slug or "draft"


def sanitize_filename_component(name: str) -> str:
    """Windows-safe filename stem (no extension): strips forbidden/control characters,
    trailing dots/spaces, renames a bare reserved device name, and caps length."""
    stripped = _CONTROL_CHARS_RE.sub("", name)
    stripped = _WINDOWS_FORBIDDEN_CHARS_RE.sub("", stripped)
    stripped = stripped.rstrip(" .")
    if stripped.upper() in _RESERVED_NAMES:
        stripped = f"_{stripped}"
    if len(stripped) > MAX_FILENAME_STEM_LENGTH:
        stripped = stripped[:MAX_FILENAME_STEM_LENGTH].rstrip(" .")
    return stripped or "draft"


def render_file_name_template(template: str, *, now: datetime, slug: str, case_number: str) -> str:
    return (
        template.replace("{yyyy}", f"{now.year:04d}")
        .replace("{mm}", f"{now.month:02d}")
        .replace("{dd}", f"{now.day:02d}")
        .replace("{slug}", slug)
        .replace("{case}", case_number)
    )


def build_eml(*, subject: str, body: str, recipient: str | None, now: datetime) -> bytes:
    message = EmailMessage(policy=policy.SMTP)  # policy.SMTP uses CRLF line endings
    message["Subject"] = subject
    if recipient:
        message["To"] = recipient
    message["Date"] = format_datetime(now)
    message["X-Unsent"] = "1"
    message.set_content(body)
    return message.as_bytes()


def build_txt(
    *, subject: str, body: str, recipient: str | None, citations: Sequence[str], now: datetime
) -> bytes:
    lines: list[str] = []
    if recipient:
        lines.append(f"To: {recipient}")
    lines.append(f"Subject: {subject}")
    lines.append(f"Date: {format_datetime(now)}")
    lines.append("")
    lines.extend(body.splitlines())
    if citations:
        lines.append("")
        lines.append("Sources")
        lines.append("-------")
        lines.extend(f"- {citation}" for citation in citations)
    text = "\r\n".join([*lines, ""])
    return text.encode("utf-8")


def _uniquify_pair(directory: Path, stem: str, exts: tuple[str, str]) -> tuple[Path, Path]:
    """Find a stem where neither exts[0] nor exts[1] exists yet, so the .eml/.txt pair matches."""
    candidate_stem = stem
    suffix = 1
    while True:
        candidates = (directory / f"{candidate_stem}{exts[0]}", directory / f"{candidate_stem}{exts[1]}")
        if not any(path.exists() for path in candidates):
            return candidates
        suffix += 1
        candidate_stem = f"{stem}-{suffix}"


def write_email_draft(
    case_dir: Path,
    emails_folder_name: str,
    file_name_template: str,
    *,
    subject: str,
    body: str,
    slug: str,
    case_number: str,
    recipient: str | None = None,
    citations: Sequence[str] = (),
    now: datetime | None = None,
) -> tuple[Path, Path]:
    """Render the filename template, sanitize it, and write the .eml + .txt pair; never overwrites."""
    at = now or datetime.now(UTC)
    rendered = render_file_name_template(
        file_name_template, now=at, slug=slugify(slug), case_number=case_number
    )
    stem = sanitize_filename_component(rendered)

    emails_dir = case_dir / emails_folder_name
    emails_dir.mkdir(parents=True, exist_ok=True)

    eml_path, txt_path = _uniquify_pair(emails_dir, stem, (".eml", ".txt"))
    eml_path.write_bytes(build_eml(subject=subject, body=body, recipient=recipient, now=at))
    txt_path.write_bytes(
        build_txt(subject=subject, body=body, recipient=recipient, citations=citations, now=at)
    )
    return eml_path, txt_path
