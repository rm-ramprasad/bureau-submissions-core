"""Telephone number rules (spec §8.1): pre-validation edits, then validation of what is left."""

from __future__ import annotations

import itertools
import re

MULTI_SPLIT_RE = re.compile(r"[\\/,:;&]")
MOBILE_TYPE = "MOBILE"  # canonical phone_type values
NOT_CLASSIFIED = "NOT_CLASSIFIED"


def _is_sequence(d: str) -> bool:
    steps = {int(b) - int(a) for a, b in itertools.pairwise(d)}
    return len(d) >= 5 and steps in ({1}, {-1})


def _national(digits: str) -> str:
    """Strip an Indian ISD (91 / 0091) or trunk (0) prefix from a mobile number."""
    for prefix in ("0091", "91", "0"):
        if digits.startswith(prefix) and len(digits) - len(prefix) == 10:
            return digits[len(prefix) :]
    return digits


def number_problem(raw: str, phone_type: str | None) -> str | None:
    """Return why one number is invalid, or None."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 5:
        return "fewer than 5 digits"
    if len(set(digits)) == 1:
        return "single repeated digit"
    if _is_sequence(digits):
        return "ascending or descending sequence"
    is_isd = raw.strip().startswith(("+", "00")) and not raw.strip().lstrip("+").startswith(("91", "0091"))
    if is_isd:
        return None if len(digits) >= 9 else "ISD number shorter than 9 digits"
    if digits[0] == "1":
        return "starts with 1"
    if phone_type == MOBILE_TYPE:
        national = _national(digits)
        if len(national) < 10 or national[0] not in "56789":
            return "Indian mobile must be 10 digits starting 5-9"
    return None


def valid_numbers(raw: str | None, phone_type: str | None) -> list[str]:
    """Numbers that survive the edits. Several numbers are allowed only when the type is not classified."""
    if not raw or not raw.strip():
        return []
    parts = MULTI_SPLIT_RE.split(raw) if phone_type in (NOT_CLASSIFIED, None) else [raw]
    return [p.strip() for p in parts if p.strip() and number_problem(p, phone_type) is None]
