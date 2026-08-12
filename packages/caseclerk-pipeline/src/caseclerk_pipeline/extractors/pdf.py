"""pdf text extraction via pdfminer.six.

A PDF that yields no (or only whitespace) text has no extractable text
layer -- almost always a scanned image PDF -- and needs OCR, which is
out of scope; that case raises NeedsOcrError rather than returning empty text.
"""

from __future__ import annotations

from pathlib import Path

from pdfminer.high_level import extract_text as _pdfminer_extract_text


class NeedsOcrError(Exception):
    pass


def extract_pdf(path: Path) -> str:
    text = _pdfminer_extract_text(str(path))
    if not text or not text.strip():
        raise NeedsOcrError("scanned PDF — needs OCR")
    return text
