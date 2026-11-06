"""Capture and list user adjustments (exclusions and overrides) of one request.

Adjustments belong to a request (batch_id) and its NBFC, as-of date and submission type. They are captured while
the request is a DRAFT; submitting it for validation locks them (the caller checks the request status). Removal
is a soft delete; history stays. More exceptions after validation means a new request.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import Engine, and_, select, text, update

from app.adjustments.models import Adjustment, AppliedAdjustment
from app.adjustments.tables import snapshot_adjustment_effect as e
from app.adjustments.tables import submission_adjustment as t
from app.enums import AdjustmentKind, OverrideField, SubmissionType
from canonical.db import utcnow

MAX_KEY_LENGTH = 50
MAX_REASON_LENGTH = 500
MAX_DPD = 9999
_SPLIT = re.compile(r"[\s,;]+")


class AdjustmentError(ValueError):
    """Invalid input; the message is shown to the user."""


class AdjustmentNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class AddResult:
    added: list[str]
    already_present: list[str]


@dataclass(frozen=True)
class AdjustmentScope:
    """The request (batch) an adjustment belongs to, with its NBFC, as-of date and submission type."""

    batch_id: str
    tenant_id: str
    as_of_date: date
    submission_type: SubmissionType

    @classmethod
    def of(cls, request) -> AdjustmentScope:
        return cls(request.batch_id, request.tenant_id, request.as_of_date, SubmissionType(request.submission_type))

    def where(self):
        return and_(t.c.batch_id == self.batch_id, t.c.submission_type == str(self.submission_type))

    def values(self) -> dict:
        return dict(
            batch_id=self.batch_id,
            tenant_id=self.tenant_id,
            as_of_date=self.as_of_date,
            submission_type=str(self.submission_type),
        )


def parse_keys(text: str) -> list[str]:
    """Account numbers or customer IDs pasted one per line (commas, semicolons and spaces also separate)."""
    keys = list(dict.fromkeys(k for k in _SPLIT.split(text or "") if k))
    too_long = [k for k in keys if len(k) > MAX_KEY_LENGTH]
    if too_long:
        raise AdjustmentError(f"Longer than {MAX_KEY_LENGTH} characters: {', '.join(too_long[:5])}")
    return keys


def parse_override_value(field: OverrideField, raw: str) -> Decimal:
    try:
        value = Decimal(str(raw).replace(",", "").strip())
    except InvalidOperation:
        raise AdjustmentError(f"{field.label}: {raw!r} is not a number") from None
    if not value.is_finite():
        raise AdjustmentError(f"{field.label}: {raw!r} is not a number")
    if field is OverrideField.DAYS_PAST_DUE:
        if value != value.to_integral_value() or not 0 <= value <= MAX_DPD:
            raise AdjustmentError(f"OD days must be a whole number from 0 to {MAX_DPD}")
        return value.quantize(Decimal(1))
    if value != value.quantize(Decimal("0.01")):
        raise AdjustmentError(f"{field.label}: at most 2 decimals")
    if value < 0 and field is not OverrideField.CURRENT_BALANCE:  # only a ledger balance can be a credit
        raise AdjustmentError(f"{field.label} cannot be negative")
    if abs(value) >= Decimal(10) ** 18:
        raise AdjustmentError(f"{field.label} is too large")
    return value


def _reason(reason: str | None) -> str | None:
    reason = (reason or "").strip()
    if len(reason) > MAX_REASON_LENGTH:
        raise AdjustmentError(f"Reason: at most {MAX_REASON_LENGTH} characters")
    return reason or None


class AdjustmentRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    # ------------------------------------------------------------------ capture
    def add_exclusions(
        self, scope: AdjustmentScope, kind: AdjustmentKind, keys: Iterable[str], reason: str | None, user: str
    ) -> AddResult:
        """Exclude loans (EXCLUDE_ACCOUNT) or customers (EXCLUDE_CUSTOMER). Keys already excluded are skipped."""
        if kind not in (AdjustmentKind.EXCLUDE_ACCOUNT, AdjustmentKind.EXCLUDE_CUSTOMER):
            raise AdjustmentError(f"Not an exclusion: {kind}")
        keys = list(dict.fromkeys(k.strip() for k in keys if k and k.strip()))
        if not keys:
            raise AdjustmentError("Enter at least one loan account number or customer ID")
        customer = kind is AdjustmentKind.EXCLUDE_CUSTOMER
        col = t.c.customer_id if customer else t.c.account_number
        reason, now = _reason(reason), self.clock()
        with self.engine.begin() as conn:
            existing = set(
                conn.execute(
                    select(col).where(scope.where(), t.c.kind == str(kind), t.c.removed_at.is_(None), col.in_(keys))
                ).scalars()
            )
            added = [k for k in keys if k not in existing]
            if added:
                conn.execute(
                    t.insert(),
                    [
                        scope.values()
                        | dict(
                            kind=str(kind),
                            account_number=None if customer else k,
                            customer_id=k if customer else None,
                            field=None,
                            override_value=None,
                            reason=reason,
                            created_by=user,
                            created_at=now,
                        )
                        for k in added
                    ],
                )
        return AddResult(added, [k for k in keys if k in existing])

    def add_override(
        self,
        scope: AdjustmentScope,
        account_number: str,
        field: OverrideField,
        value: Decimal,
        reason: str | None,
        user: str,
    ) -> tuple[int, int | None]:
        """Override one field of one loan. Replaces (soft-removes) an active override of the same loan + field.

        Returns (new adjustment id, replaced adjustment id or None).
        """
        account_number = (account_number or "").strip()
        if not account_number or len(account_number) > MAX_KEY_LENGTH:
            raise AdjustmentError("Enter one loan account number")
        reason, now = _reason(reason), self.clock()
        with self.engine.begin() as conn:
            replaced = conn.execute(
                select(t.c.adjustment_id).where(
                    scope.where(),
                    t.c.kind == str(AdjustmentKind.OVERRIDE),
                    t.c.account_number == account_number,
                    t.c.field == str(field),
                    t.c.removed_at.is_(None),
                )
            ).scalar()
            if replaced is not None:
                conn.execute(update(t).where(t.c.adjustment_id == replaced).values(removed_by=user, removed_at=now))
            new_id = conn.execute(
                t.insert().values(
                    **scope.values(),
                    kind=str(AdjustmentKind.OVERRIDE),
                    account_number=account_number,
                    customer_id=None,
                    field=str(field),
                    override_value=value,
                    reason=reason,
                    created_by=user,
                    created_at=now,
                )
            ).inserted_primary_key[0]
        return int(new_id), replaced

    def remove(self, adjustment_id: int, user: str) -> Adjustment:
        adj = self.get(adjustment_id)
        if not adj.is_active:
            raise AdjustmentError(f"Adjustment {adjustment_id} was already removed")
        with self.engine.begin() as conn:
            conn.execute(
                update(t)
                .where(t.c.adjustment_id == adjustment_id, t.c.removed_at.is_(None))
                .values(removed_by=user, removed_at=self.clock())
            )
        return adj

    # ------------------------------------------------------------------ read
    def get(self, adjustment_id: int) -> Adjustment:
        with self.engine.connect() as conn:
            row = conn.execute(select(t).where(t.c.adjustment_id == adjustment_id)).first()
        if row is None:
            raise AdjustmentNotFoundError(f"Adjustment {adjustment_id} not found")
        return Adjustment.from_row(row)

    def list(self, scope: AdjustmentScope, include_removed: bool = False) -> list[Adjustment]:
        q = select(t).where(scope.where()).order_by(t.c.adjustment_id.desc())
        if not include_removed:
            q = q.where(t.c.removed_at.is_(None))
        with self.engine.connect() as conn:
            return [Adjustment.from_row(r) for r in conn.execute(q)]

    def applied(self, snapshot_id: int) -> list[AppliedAdjustment]:
        """Effects of the snapshot's frozen adjustments (old -> new values, excluded loans and borrowers)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(e)
                .where(e.c.snapshot_id == snapshot_id)
                .order_by(e.c.adjustment_id, e.c.account_key, e.c.party_key)
            ).all()
        return [
            AppliedAdjustment(
                **{k: v for k, v in r._mapping.items() if k != "effect_id"}
                | {"field": OverrideField(r.field) if r.field else None}
            )
            for r in rows
        ]

    def frozen(self, snapshot_id: int) -> dict[int, int]:
        """adjustment_id -> loans affected, for the adjustments frozen on this snapshot (from the view)."""
        with self.engine.connect() as conn:
            return dict(
                conn.execute(
                    text(
                        "SELECT adjustment_id, loans_affected FROM automn.v_snapshot_adjustment WHERE snapshot_id = :s"
                    ),
                    {"s": snapshot_id},
                ).all()
            )
