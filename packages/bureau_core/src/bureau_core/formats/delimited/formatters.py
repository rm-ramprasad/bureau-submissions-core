"""Value formatters for delimited layouts: every TUDF value formatter, plus the ones delimited formats need.

Same signature as formats/tudf/formatters.py: (value, row, args) -> text, or None to leave the field empty.
"""

from __future__ import annotations

from decimal import Decimal

from bureau_core.formats.tudf.encoding import whole_rupees
from bureau_core.formats.tudf.formatters import VALUE_FORMATTERS as TUDF_FORMATTERS


def _signed_amount_leading(value, row, args) -> str:
    """Whole rupees, a leading '-' for a credit balance: -2450.5 -> '-2451'."""
    return str(whole_rupees(value))


def _dpd_1nnn(value, row, args) -> str:
    """Days past due as 1nnn (Commercial 8.9): 0 -> 1000, 214 -> 1214, 999 or more -> 1999."""
    return str(1000 + min(int(value), 999))


def _hundredths(value, row, args) -> str:
    """A percentage in hundredths, no separator: 99.99 -> 9999, 100 -> 10000."""
    return str(int((Decimal(str(value)) * 100).to_integral_value()))


def _truncate(value) -> int:
    """Whole rupees by cutting the decimals, as the CIC does (MFI 2.3): 1234.99 -> 1234, -10.5 -> -10."""
    return int(Decimal(str(value)))


def _amount_truncated(value, row, args) -> str | None:
    """Unsigned whole rupees, decimals cut (MFI 2.3); a negative value is left out (validation reports it)."""
    n = _truncate(value)
    return None if n < 0 else str(n)


def _signed_amount_truncated(value, row, args) -> str:
    """Whole rupees, decimals cut, a leading '-' when negative (MFI GRPCRD #6, ACTCRD #87)."""
    return str(_truncate(value))


def _word_part(value, row, args) -> str | None:
    """One part of a text packed word by word into fields of `widths` (e.g. [100, 50, 50]) without splitting a word
    (MFI 7.1); `part` is 1-based. A word that does not fit its field moves to the next one; once a word fits no
    remaining field, packing stops (words are never reordered; validation reports the name as too long)."""
    widths, part = list(args["widths"]), int(args["part"])
    parts = [""] * len(widths)
    i = 0
    for word in str(value).split():
        while i < len(widths):
            candidate = f"{parts[i]} {word}".strip()
            if len(candidate) <= widths[i]:
                parts[i] = candidate
                break
            i += 1
    return parts[part - 1] or None


VALUE_FORMATTERS = {
    **TUDF_FORMATTERS,
    "signed_amount_leading": _signed_amount_leading,
    "dpd_1nnn": _dpd_1nnn,
    "hundredths": _hundredths,
    "amount_truncated": _amount_truncated,
    "signed_amount_truncated": _signed_amount_truncated,
    "word_part": _word_part,
}
