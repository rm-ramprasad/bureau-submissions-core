"""SQLAlchemy Core definitions of the adjustment tables and view (created by migration 001)."""

from __future__ import annotations

from sqlalchemy import (
    DDL,
    BigInteger,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    event,
)

import canonical.schema
import canonical.tenants.tables  # noqa: F401
from canonical.db import metadata

_PK = BigInteger().with_variant(Integer(), "sqlite")

submission_adjustment = Table(
    "submission_adjustment",
    metadata,
    Column("adjustment_id", _PK, primary_key=True, autoincrement=True),
    Column("batch_id", String(40)),  # the request it belongs to (migration 007)
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("account_number", String(50)),
    Column("customer_id", String(50)),
    Column("field", String(30)),
    Column("override_value", Numeric(20, 2)),
    Column("reason", String(500)),
    Column("created_by", String(100), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("removed_by", String(100)),
    Column("removed_at", DateTime),
    Index("ix_submission_adjustment_scope", "tenant_id", "as_of_date", "submission_type"),
    Index("ix_submission_adjustment_batch", "batch_id"),
)

# Frozen copy of the adjustments in force when the snapshot was extracted (audit trail)
snapshot_adjustment = Table(
    "snapshot_adjustment",
    metadata,
    Column(
        "snapshot_id", BigInteger, ForeignKey("automn.extract_snapshot.snapshot_id", ondelete="CASCADE"), nullable=False
    ),
    Column("adjustment_id", BigInteger, ForeignKey("automn.submission_adjustment.adjustment_id"), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("account_number", String(50)),
    Column("customer_id", String(50)),
    Column("field", String(30)),
    Column("override_value", Numeric(20, 2)),
    Column("reason", String(500)),
    Column("created_by", String(100), nullable=False),
    Column("created_at", DateTime, nullable=False),
    PrimaryKeyConstraint("snapshot_id", "adjustment_id"),
)

# What each frozen adjustment hit in the source data
snapshot_adjustment_effect = Table(
    "snapshot_adjustment_effect",
    metadata,
    Column("effect_id", _PK, primary_key=True, autoincrement=True),
    Column(
        "snapshot_id", BigInteger, ForeignKey("automn.extract_snapshot.snapshot_id", ondelete="CASCADE"), nullable=False
    ),
    Column("adjustment_id", BigInteger, nullable=False),
    Column("action", String(15), nullable=False),  # EXCLUDE / EXCLUDE_PARTY / OVERRIDE
    Column("account_key", String(50)),
    Column("party_key", String(50)),
    Column("field", String(30)),
    Column("old_value", Numeric(20, 2)),
    Column("new_value", Numeric(20, 2)),
    Index("ix_snapshot_adjustment_effect_account", "snapshot_id", "action", "account_key"),
    Index("ix_snapshot_adjustment_effect_party", "snapshot_id", "action", "party_key"),
)

# Each frozen adjustment with the number of loans it hit (0 = not found). Kept identical to migration 006.
ADJUSTMENT_VIEW_SQL = """CREATE VIEW automn.v_snapshot_adjustment AS
SELECT x.snapshot_id, x.adjustment_id, x.kind, x.account_number, x.customer_id, x.field, x.override_value,
       x.reason, x.created_by, x.created_at,
       (SELECT COUNT(DISTINCT e.account_key) FROM automn.snapshot_adjustment_effect e
        WHERE e.snapshot_id = x.snapshot_id AND e.adjustment_id = x.adjustment_id
          AND e.action IN ('EXCLUDE', 'OVERRIDE')) AS loans_affected
FROM automn.snapshot_adjustment x"""

# metadata.create_all (test databases) creates the view too; Azure SQL gets it from migrations/azure/001_baseline.sql
event.listen(metadata, "after_create", DDL(ADJUSTMENT_VIEW_SQL))
event.listen(metadata, "before_drop", DDL("DROP VIEW IF EXISTS automn.v_snapshot_adjustment"))
