"""Codes of the bureau files and their validation. Stored and exchanged as their string values."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from enum import StrEnum
from typing import ClassVar


class _SubmissionTypes(type):
    """Iterating the class lists the registered types in their descriptor order (consumer first)."""

    def __iter__(cls) -> Iterator[SubmissionType]:
        from bureau_core.types import registered_names

        return iter([SubmissionType(n) for n in registered_names()])

    def __contains__(cls, value: object) -> bool:
        from bureau_core.types import registered_names

        return str(value) in registered_names()


class SubmissionType(str, metaclass=_SubmissionTypes):
    """A submission type: one of the type folders (bureau_core/types/<type>/type.yaml), not a fixed list (step 5).

    Behaves like the string it is (stored and exchanged as text); `SubmissionType("X")` raises ValueError for a type
    without a folder. CONSUMER and COMMERCIAL are kept as names for the two types the product ships.
    """

    __slots__ = ()
    CONSUMER: ClassVar[SubmissionType]
    COMMERCIAL: ClassVar[SubmissionType]

    def __new__(cls, value: object) -> SubmissionType:
        if isinstance(value, SubmissionType):
            return value
        from bureau_core.types import registered_names

        text = str(value)
        if text not in registered_names():
            raise ValueError(f"{text!r} is not a valid SubmissionType (known: {', '.join(registered_names())})")
        return _interned(text)

    @property
    def value(self) -> str:
        return str.__str__(self)

    def __str__(self) -> str:
        return str.__str__(self)

    def __repr__(self) -> str:
        return f"SubmissionType({str.__repr__(self)})"


_INSTANCES: dict[str, SubmissionType] = {}


def _interned(text: str) -> SubmissionType:
    """One object per type, so `is` comparisons keep working as they did with the enum."""
    if text not in _INSTANCES:
        _INSTANCES[text] = str.__new__(SubmissionType, text)
    return _INSTANCES[text]


SubmissionType.CONSUMER = _interned("CONSUMER")
SubmissionType.COMMERCIAL = _interned("COMMERCIAL")


class Outcome(StrEnum):
    """Outcome of a validation finding, lightest first (spec §0.1, plus WARN)."""

    WARN = "WARN"  # reported only
    REJECT_FIELD = "REJECT_FIELD"  # field dropped (or defaulted)
    REJECT_SEGMENT = "REJECT_SEGMENT"  # segment instance dropped
    REJECT_RECORD = "REJECT_RECORD"  # whole record (account x holder) excluded
    REJECT_FILE = "REJECT_FILE"  # nothing from the snapshot may be submitted


class RecordOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


class BureauCode(StrEnum):
    EXPERIAN = "EXPERIAN"
    CIBIL = "CIBIL"
    CRIF_HIGHMARK = "CRIF_HIGHMARK"
    EQUIFAX = "EQUIFAX"


class ReportingCycle(StrEnum):
    """UCRF header HD.53."""

    DAILY = "DL"
    WEEKLY_1 = "W1"
    WEEKLY_2 = "W2"
    WEEKLY_3 = "W3"
    MONTH_END = "ME"
    DATA_CORRECTION = "DC"
    AD_HOC = "AH"
    REJECTION_RESUBMISSION = "RR"

    @property
    def manual(self) -> bool:
        """Chosen by the user (daily, data correction, ad hoc, rejection resubmission); the others follow the date."""
        return self in (
            ReportingCycle.DAILY,
            ReportingCycle.DATA_CORRECTION,
            ReportingCycle.AD_HOC,
            ReportingCycle.REJECTION_RESUBMISSION,
        )

    @classmethod
    def for_date(cls, as_of: date) -> ReportingCycle | None:
        """The cycle an as-of date belongs to (business rule, 2026-10-30): the 9th W1, the 16th W2, the 23rd W3, the
        last day of the month ME. Any other date is not a reporting date (None)."""
        if (as_of + timedelta(days=1)).day == 1:
            return cls.MONTH_END
        return {9: cls.WEEKLY_1, 16: cls.WEEKLY_2, 23: cls.WEEKLY_3}.get(as_of.day)

    @classmethod
    def dates(cls, until: date, months: int = 13) -> list[date]:
        """The reporting dates (9th, 16th, 23rd, month end) up to `until`, newest first, over `months` months."""
        out, first = [], until.replace(day=1)
        for _ in range(months):
            month_end = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            out += [
                d for d in (first.replace(day=9), first.replace(day=16), first.replace(day=23), month_end) if d <= until
            ]
            first = (first - timedelta(days=1)).replace(day=1)
        return sorted(out, reverse=True)
