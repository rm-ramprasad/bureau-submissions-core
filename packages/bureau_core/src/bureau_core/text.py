"""Cell text as the core reads workbooks (the bureau Excel; the app's uploads use the same rules)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time

DDMMYYYY = re.compile(r"^\d{8}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalise_header(text) -> str:
    """Header text for matching: case, any whitespace (incl. line breaks, non-breaking spaces) and quote styles
    ignored. 'Borrower‟s  Name' == "borrower's name"."""
    s = unicodedata.normalize("NFKC", "" if text is None else str(text))
    s = re.sub(r"[‘’‚‛“”„‟′″`´]", "'", s)
    return " ".join(s.split()).casefold()


def cell_text(value) -> str | None:
    """A cell value as staging text: None for blanks (incl. non-breaking spaces), integers without '.0',
    dates as YYYY-MM-DD (date-times as YYYY-MM-DD HH:MM:SS)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time() == time(0) else value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, (date, time)):
        return value.isoformat()
    text = str(value).replace("\xa0", " ").strip()
    return text or None


def as_date(value: str | None) -> date | None:
    """DDMMYYYY (the form's format) or YYYY-MM-DD (a real Excel date); None if not a date."""
    if value is None:
        return None
    try:
        if DDMMYYYY.match(value):
            return date(int(value[4:]), int(value[2:4]), int(value[:2]))
        if ISO.match(value):
            return date.fromisoformat(value)
    except ValueError:
        return None
    return None
