"""TUDF encoding primitives: printable ASCII, TLV and fixed-width fields, amounts, dates, word fitting.

Format-independent: segment tags, lengths and record structure come from the layout (layout.py).
Spec references are to docs/specs/UCRF_Consumer_v3.74_Spec.md.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

_SPACES_RE = re.compile(r"\s+")


class EncodingError(ValueError):
    pass


# ---------------------------------------------------------------- text
def printable(value) -> str:
    """Printable ASCII 32-126 only (§3.1 P): transliterate accents, drop anything else, collapse whitespace."""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if 32 <= ord(ch) <= 126 or ch.isspace())
    return _SPACES_RE.sub(" ", text).strip()


def fit_words(text: str, width: int, max_parts: int) -> tuple[list[str], bool]:
    """Pack words into at most `max_parts` chunks of `width` chars without splitting words (§6.1 #4, §10.1).
    Returns (parts, overflowed). A single word longer than `width` is cut (it cannot be moved)."""
    parts, current, overflowed = [], "", False
    for word in printable(text).split(" "):
        if not word:
            continue
        if len(word) > width:
            word, overflowed = word[:width], True
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= width:
            current = candidate
            continue
        parts.append(current)
        current = word
    if current:
        parts.append(current)
    if len(parts) > max_parts:
        parts, overflowed = parts[:max_parts], True
    return parts, overflowed


# ---------------------------------------------------------------- values
def ddmmyyyy(d: date) -> str:
    return d.strftime("%d%m%Y")


def whole_rupees(amount) -> int:
    return int(Decimal(str(amount)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def amount(value) -> str:
    """Unsigned whole-rupee amount (§3.5). Negative values are an error (validation should have caught them)."""
    n = whole_rupees(value)
    if n < 0:
        raise EncodingError(f"negative amount {value} for an unsigned field")
    return str(n)


def signed_amount(value) -> str:
    """Whole rupees with a trailing '-' for credit balances (§2.3, TL.13): -2450.5 -> '2451-'."""
    n = whole_rupees(value)
    return f"{-n}-" if n < 0 else str(n)


def rate(value) -> str:
    """Rate of interest: digits '.' digits, first 3 decimals kept, rest truncated (TL.38)."""
    return str(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN))


# ---------------------------------------------------------------- fields
def tlv(tag: str, value: str) -> str:
    """Variable-length field: 2-char tag + 2-digit length + value (§2.2)."""
    if len(value) > 99:
        raise EncodingError(f"field {tag} longer than 99 bytes")
    return f"{tag}{len(value):02d}{value}"


def fixed(value: str, width: int, numeric: bool = False) -> str:
    """Fixed-width field (§2.3): text left-justified space-filled, numbers right-justified zero-filled."""
    value = printable(value) if not numeric else str(value)
    if len(value) > width:
        raise EncodingError(f"value {value!r} longer than {width}")
    return value.rjust(width, "0") if numeric else value.ljust(width)
