"""Codes of the bureau files and their validation. Stored and exchanged as their string values."""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum


class SubmissionType(StrEnum):
    CONSUMER = "CONSUMER"
    COMMERCIAL = "COMMERCIAL"
    NESL = "NESL"


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
