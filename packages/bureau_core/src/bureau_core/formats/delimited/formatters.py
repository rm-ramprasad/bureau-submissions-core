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


VALUE_FORMATTERS = {
    **TUDF_FORMATTERS,
    "signed_amount_leading": _signed_amount_leading,
    "dpd_1nnn": _dpd_1nnn,
    "hundredths": _hundredths,
}
