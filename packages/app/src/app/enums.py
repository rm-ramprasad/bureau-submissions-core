"""The app's codes (requests, adjustments), plus the core's (bureau_core.enums) and Layer 2's (canonical.enums),
re-exported so app modules import every code from here. Stored in the DB as their string values."""

from __future__ import annotations

from enum import StrEnum

from bureau_core.enums import BureauCode, Outcome, RecordOutcome, ReportingCycle, SubmissionType
from canonical.enums import ExtractStrategy, SnapshotStatus

__all__ = [
    "AdjustmentKind",
    "BatchStatus",
    "BureauCode",
    "ExtractStrategy",
    "Outcome",
    "OverrideField",
    "RecordOutcome",
    "ReportingCycle",
    "RequestStatus",
    "RunMode",
    "SnapshotStatus",
    "SubmissionType",
]


class RunMode(StrEnum):
    VALIDATE = "VALIDATE"  # extract + validate, findings only (bureau-independent)
    GENERATE = "GENERATE"  # extract + validate + file for one bureau


class AdjustmentKind(StrEnum):
    """What a user-captured adjustment does (applied while the snapshot is built, before validation)."""

    EXCLUDE_ACCOUNT = "EXCLUDE_ACCOUNT"  # one loan, all its holders
    EXCLUDE_CUSTOMER = "EXCLUDE_CUSTOMER"  # every loan the customer holds, whole
    OVERRIDE = "OVERRIDE"  # replace one c_account value of one loan


class OverrideField(StrEnum):
    """c_account columns users may override, with their UI labels."""

    DAYS_PAST_DUE = "days_past_due"
    CURRENT_BALANCE = "current_balance"
    AMOUNT_OVERDUE = "amount_overdue"
    SETTLEMENT_AMOUNT = "settlement_amount"

    @property
    def label(self) -> str:
        return {
            "days_past_due": "OD days",
            "current_balance": "Ledger balance",
            "amount_overdue": "OD amount",
            "settlement_amount": "Settlement amount",
        }[self.value]


class RequestStatus(StrEnum):
    """Status of one row (one type x bureau file)."""

    DRAFT = "DRAFT"  # created by the New request form; exceptions are captured, then it is submitted
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BatchStatus(StrEnum):
    """Derived status of all rows created by one click. Never stored."""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
