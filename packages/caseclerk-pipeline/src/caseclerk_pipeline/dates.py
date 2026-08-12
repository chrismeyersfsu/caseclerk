"""Date detection in extracted text, and its inverse for search.

find_dates() normalizes written dates to ISO ("2026-04-21"), stored in
document_dates. date_query_variants() does the opposite: given an ISO
date typed into search_case's date param, expand it into the written
forms ("April 21, 2026", "4/21/26", ...) that the FTS index actually
contains, since documents are never written in ISO form.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

_MONTH_NAME_RE = re.compile(
    r"\b(?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<year>\d{4})\b"
)
_NUMERIC_SLASH_RE = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4}|\d{2})\b")
_ISO_RE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})\b")


def _to_iso(year: int, month: int, day: int) -> str | None:
    if year < 100:
        year += 2000 if year < 70 else 1900
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def find_dates(text: str) -> list[str]:
    """Unique ISO dates (YYYY-MM-DD) found in text, in first-seen order."""
    found: list[str] = []
    seen: set[str] = set()

    def add(iso: str | None) -> None:
        if iso and iso not in seen:
            seen.add(iso)
            found.append(iso)

    for match in _ISO_RE.finditer(text):
        add(_to_iso(int(match["year"]), int(match["month"]), int(match["day"])))
    for match in _MONTH_NAME_RE.finditer(text):
        month = _MONTHS.get(match["month"].lower())
        if month is not None:
            add(_to_iso(int(match["year"]), month, int(match["day"])))
    for match in _NUMERIC_SLASH_RE.finditer(text):
        add(_to_iso(int(match["year"]), int(match["month"]), int(match["day"])))

    return found


def date_query_variants(iso_date: str) -> list[str]:
    """Expand an ISO date into the written forms a document might actually contain."""
    parsed = date.fromisoformat(iso_date)
    month_name = parsed.strftime("%B")
    month_abbr = parsed.strftime("%b")
    yy = parsed.strftime("%y")
    variants = [
        f"{month_name} {parsed.day}, {parsed.year}",
        f"{month_abbr} {parsed.day}, {parsed.year}",
        f"{parsed.month}/{parsed.day}/{yy}",
        f"{parsed.month}/{parsed.day}/{parsed.year}",
        iso_date,
    ]
    seen: set[str] = set()
    unique: list[str] = []
    for variant in variants:
        if variant not in seen:
            seen.add(variant)
            unique.append(variant)
    return unique
