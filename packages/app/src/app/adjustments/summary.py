"""Read what the adjustments did to a snapshot (from the frozen copy and its effects)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from sqlalchemy import Engine, func, select, text

from app.adjustments.models import ApplyResult
from app.adjustments.tables import snapshot_adjustment_effect as e

_V_NOT_FOUND = (
    "SELECT COUNT(*) FROM automn.v_snapshot_adjustment WHERE snapshot_id = :snapshot_id AND loans_affected = 0"
)


_CSV_SQL = """SELECT x.adjustment_id, x.kind, COALESCE(x.account_number, x.customer_id) AS adjustment_key,
       e.action, e.account_key, e.party_key, e.field, e.old_value, e.new_value, x.reason, x.created_by
FROM automn.snapshot_adjustment x
LEFT JOIN automn.snapshot_adjustment_effect e ON e.snapshot_id = x.snapshot_id AND e.adjustment_id = x.adjustment_id
WHERE x.snapshot_id = :snapshot_id
ORDER BY CASE WHEN e.action = 'OVERRIDE' THEN 2 ELSE 1 END, x.adjustment_id, e.account_key, e.party_key"""


def adjustments_csv(engine: Engine, snapshot_id: int) -> str:
    """Section appended to a bureau file's exceptions CSV: excluded loans/borrowers and overrides (old -> new)."""
    with engine.connect() as conn:
        rows = conn.execute(text(_CSV_SQL), {"snapshot_id": snapshot_id}).all()
    if not rows:
        return ""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow([])
    w.writerow(["# User adjustments applied to this snapshot (excluded loans and overrides, old -> new)"])
    w.writerow(
        [
            "adjustment_id",
            "kind",
            "entered_key",
            "effect",
            "account_key",
            "party_key",
            "field",
            "old_value",
            "new_value",
            "reason",
            "entered_by",
        ]
    )

    def amount(v):
        return "" if v is None else f"{Decimal(str(v)):.2f}"

    for r in rows:
        w.writerow(
            [
                r.adjustment_id,
                r.kind,
                r.adjustment_key,
                r.action or "NOT_FOUND",
                r.account_key or "",
                r.party_key or "",
                r.field or "",
                amount(r.old_value),
                amount(r.new_value),
                r.reason or "",
                r.created_by,
            ]
        )
    return out.getvalue()


def applied_summary(engine: Engine, snapshot_id: int) -> ApplyResult:
    excluded_keys = select(e.c.account_key).where(e.c.snapshot_id == snapshot_id, e.c.action == "EXCLUDE")
    with engine.connect() as conn:
        excluded = conn.execute(
            select(func.count(e.c.account_key.distinct())).where(
                e.c.snapshot_id == snapshot_id, e.c.action == "EXCLUDE"
            )
        ).scalar()
        overridden = conn.execute(
            select(func.count(e.c.account_key.distinct())).where(  # excluded loans don't count
                e.c.snapshot_id == snapshot_id, e.c.action == "OVERRIDE", e.c.account_key.notin_(excluded_keys)
            )
        ).scalar()
        not_found = conn.execute(text(_V_NOT_FOUND), {"snapshot_id": snapshot_id}).scalar()
    return ApplyResult(int(excluded), int(overridden), int(not_found))
