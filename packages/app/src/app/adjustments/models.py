from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.enums import AdjustmentKind, OverrideField, SubmissionType


@dataclass(frozen=True)
class Adjustment:
    adjustment_id: int
    batch_id: str | None
    tenant_id: str
    as_of_date: date
    submission_type: SubmissionType
    kind: AdjustmentKind
    account_number: str | None
    customer_id: str | None
    field: OverrideField | None
    override_value: Decimal | None
    reason: str | None
    created_by: str
    created_at: datetime
    removed_by: str | None
    removed_at: datetime | None

    @property
    def key(self) -> str | None:
        """The loan account number or customer ID it targets."""
        return self.customer_id if self.kind is AdjustmentKind.EXCLUDE_CUSTOMER else self.account_number

    @property
    def is_active(self) -> bool:
        return self.removed_at is None

    @classmethod
    def from_row(cls, row) -> Adjustment:
        m = dict(row._mapping)
        m["submission_type"] = SubmissionType(m["submission_type"])
        m["kind"] = AdjustmentKind(m["kind"])
        m["field"] = OverrideField(m["field"]) if m["field"] else None
        return cls(**m)


@dataclass(frozen=True)
class AppliedAdjustment:
    """What one frozen adjustment hit in one snapshot (snapshot_adjustment_effect)."""

    snapshot_id: int
    adjustment_id: int
    action: str  # EXCLUDE (a loan) / EXCLUDE_PARTY (a borrower left with no loan) / OVERRIDE
    account_key: str | None
    party_key: str | None
    field: OverrideField | None
    old_value: Decimal | None
    new_value: Decimal | None


@dataclass(frozen=True)
class ApplyResult:
    excluded_accounts: int  # distinct loans removed
    overridden_accounts: int  # distinct loans with at least one override (not excluded)
    not_found: int  # active adjustments that matched nothing
