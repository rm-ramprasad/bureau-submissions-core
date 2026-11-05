"""ID number format rules per (canonical) ID type: consumer spec §7.1, commercial Annexure D part C."""

from __future__ import annotations

import re
from functools import lru_cache

_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]

# Default patterns per ID type; the rule pack declares the same defaults as parameters (id_pattern_*), which an
# NBFC can override on the Rules & policies page. The Aadhaar checksum (Verhoeff) is an algorithm, not a setting.
DEFAULT_PATTERNS = {
    "id_pattern_pan": r"^[A-Z]{3}[PH][A-Z][0-9]{4}[A-Z]$",
    "id_pattern_passport": r"^(?=.{7,10}$)[A-Z]{1,2}[0-9]+$",
    "id_pattern_voter": r"^(?=.{9,}$)[A-Z]{2,3}[A-Z0-9]+$",
    "id_pattern_ckyc": r"^[A-Z0-9]{1,15}$",
    "id_pattern_gram_g": r"^[A-Z]{2}-[0-9]{2}-[0-9]{3}-[0-9]{3}-[0-9]{3}/[0-9]{3}[A-Za-z0-9]+$",
    "id_pattern_cin": r"^[A-Z0-9]{21}$",
    "id_pattern_tin": r"^[A-Z0-9]{11}$",
    "id_pattern_udyam": r"^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$",
    "id_pattern_din": r"^[0-9]{8}$",
}
_MESSAGES = {  # used while the default pattern applies; a changed pattern is named in the message instead
    "id_pattern_pan": "PAN must be 5 letters, 4 digits, 1 letter with 4th letter P or H",
    "id_pattern_passport": "passport must be 7-10 chars, a letter in position 1 or 2, digits after",
    "id_pattern_voter": "voter ID must be >= 9 chars starting with 2-3 letters",
    "id_pattern_ckyc": "CKYC must be up to 15 letters/digits",
    "id_pattern_gram_g": "G RAM G must look like SS-DD-BBB-GGG-VVV/HHHSurname",
    "id_pattern_cin": "CIN must be 21 letters and digits",
    "id_pattern_tin": "TIN must be 11 letters and digits",
    "id_pattern_udyam": "Udyam number must look like UDYAM-XX-00-0000000",
    "id_pattern_din": "DIN must be 8 digits",
}
_BY_TYPE = {
    "PAN": "id_pattern_pan",
    "PASSPORT": "id_pattern_passport",
    "VOTER_ID": "id_pattern_voter",
    "CKYC": "id_pattern_ckyc",
    "G_RAM_G": "id_pattern_gram_g",
    "CIN": "id_pattern_cin",
    "TIN": "id_pattern_tin",
    "UDYAM": "id_pattern_udyam",
    "DIN": "id_pattern_din",
}  # canonical id_type values
_KEEP_DELIMITERS = {"G_RAM_G", "UDYAM"}  # matched with spaces removed, delimiters kept


@lru_cache(maxsize=64)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0


def normalise(id_number: str) -> str:
    """Strip delimiters (spaces, hyphens, slashes...) and uppercase."""
    return re.sub(r"[^A-Za-z0-9]", "", id_number or "").upper()


def id_number_problem(id_type: str, id_number: str | None, patterns: dict | None = None) -> str | None:
    """Reason the ID number is invalid for its (canonical) type, or None. Driving licence and ration card are not
    validated.

    `patterns` overrides DEFAULT_PATTERNS (rule parameters id_pattern_*).
    """
    raw = (id_number or "").strip()
    n = normalise(raw)
    if not n:
        return "empty ID number"
    if id_type == "AADHAAR":
        if not (len(n) == 12 and n.isdigit()):
            return "Aadhaar must be 12 digits"
        return None if verhoeff_valid(n) else "Aadhaar check digit (Verhoeff) is wrong"
    name = _BY_TYPE.get(id_type)
    if name is None:
        return None
    pattern = (patterns or {}).get(name) or DEFAULT_PATTERNS[name]
    value = raw.replace(" ", "") if id_type in _KEEP_DELIMITERS else n
    if id_type == "UDYAM":
        value = value.upper()
    if _compiled(pattern).match(value):
        return None
    return _MESSAGES[name] if pattern == DEFAULT_PATTERNS[name] else f"does not match the pattern {pattern}"
