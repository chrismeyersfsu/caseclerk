"""Extension -> extractor lookup, and the set of extensions marked unsupported."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from caseclerk_pipeline.extractors.docx import extract_docx
from caseclerk_pipeline.extractors.pdf import extract_pdf
from caseclerk_pipeline.extractors.text import extract_text

Extractor = Callable[[Path], str]

EXTRACTORS: dict[str, Extractor] = {
    ".docx": extract_docx,
    ".pdf": extract_pdf,
    ".txt": extract_text,
    ".md": extract_text,
}

# Extractors for these are a later phase; documents with these extensions
# are marked "unsupported" rather than attempted.
UNSUPPORTED_EXTS = frozenset({".msg", ".eml", ".rtf", ".xlsx", ".doc", ".xls", ".ppt", ".pptx"})


def get_extractor(ext: str) -> Extractor | None:
    return EXTRACTORS.get(ext.lower())


def is_unsupported(ext: str) -> bool:
    return ext.lower() in UNSUPPORTED_EXTS
