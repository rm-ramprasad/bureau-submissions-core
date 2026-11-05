"""Consumer name rules (spec §6.1, Appendix A)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

DESCRIPTOR_RE = re.compile(r"^[SWHD][/\\]O$", re.IGNORECASE)  # S/O W/O H/O D/O (or with \)
# A leading salutation is ignored (spec §6.1 #7); the spec gives no list, so the pattern is a rule parameter
# (name_salutation_pattern) an NBFC can extend, e.g. with regional titles. Matched case-insensitively.
DEFAULT_SALUTATION_PATTERN = r"^(MR|MRS|MS|DR|SMT|KUM)\.?$|^(MISS|SHRI)$"
DISALLOWED = set("~!#$%^&*=|?+,@")


@dataclass(frozen=True)
class NameCheck:
    has_digits: bool
    disallowed_chars: str  # disallowed characters found (incl. / or \ outside descriptors)
    starts_with_descriptor: bool
    has_valid_token: bool  # at least one token with >= 2 letters
    word_count: int  # words after dropping a leading salutation and descriptors


@lru_cache(maxsize=16)
def _salutation(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.IGNORECASE)


def check_name(full_name: str | None, salutation_pattern: str = DEFAULT_SALUTATION_PATTERN) -> NameCheck:
    tokens = (full_name or "").split()
    if tokens and _salutation(salutation_pattern).search(tokens[0]):  # leading salutation is ignored
        tokens = tokens[1:]
    words = [t for t in tokens if not DESCRIPTOR_RE.match(t)]
    bad = sorted({ch for t in words for ch in t if ch in DISALLOWED or ch in "/\\"})
    return NameCheck(
        has_digits=any(ch.isdigit() for ch in full_name or ""),
        disallowed_chars="".join(bad),
        starts_with_descriptor=bool(tokens) and bool(DESCRIPTOR_RE.match(tokens[0])),
        has_valid_token=any(sum(ch.isalpha() for ch in t) >= 2 for t in words),
        word_count=len(words),
    )
