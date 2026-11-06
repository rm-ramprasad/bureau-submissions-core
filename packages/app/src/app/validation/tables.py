"""SQLAlchemy Core definitions of the validation tables (created by migration 001)."""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
)

import canonical.schema
import canonical.tenants.tables  # noqa: F401
from canonical.db import metadata

_PK = BigInteger().with_variant(Integer(), "sqlite")


def _snapshot_fk() -> ForeignKey:
    return ForeignKey("automn.extract_snapshot.snapshot_id", ondelete="CASCADE")


validation_rule = Table(
    "validation_rule",
    metadata,
    Column("rule_id", String(80), primary_key=True),
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("entity", String(30), nullable=False),
    Column("segment", String(4)),
    Column("field", String(64)),
    Column("outcome", String(16), nullable=False),
    Column("message", String(500), nullable=False),
    Column("sql_text", Text, nullable=False),
    Column("is_enabled", Boolean, nullable=False, default=True),
    Column("updated_at", DateTime, nullable=False),
    Column("definition", Text),  # migration 009: JSON of a form-built rule (custom.py)
    Column("created_by", String(100)),
)

validation_policy = Table(
    "validation_policy",
    metadata,
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), primary_key=True),
    Column("rule_id", String(80), primary_key=True),
    Column("is_enabled", Boolean),
    Column("outcome", String(16)),
    Column("updated_at", DateTime, nullable=False),
)

validation_parameter = Table(  # migration 008: per-tenant rule parameter overrides
    "validation_parameter",
    metadata,
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), primary_key=True),
    Column("submission_type", String(20), primary_key=True),
    Column("name", String(64), primary_key=True),
    Column("value", String(500), nullable=False),
    Column("updated_by", String(100), nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

validation_finding = Table(
    "validation_finding",
    metadata,
    Column("finding_id", _PK, primary_key=True, autoincrement=True),
    Column("snapshot_id", BigInteger, _snapshot_fk(), nullable=False),
    Column("rule_id", String(80), nullable=False),
    Column("entity", String(30), nullable=False),
    Column("row_id", BigInteger),
    Column("account_key", String(50)),
    Column("party_key", String(50)),
    Column("segment", String(4)),
    Column("field", String(64)),
    Column("outcome", String(16), nullable=False),
    Column("value", String(500)),
    Column("message", String(500), nullable=False),
    Index("ix_validation_finding_rule", "snapshot_id", "rule_id"),
    Index("ix_validation_finding_record", "snapshot_id", "account_key", "party_key"),
)

validation_summary = Table(
    "validation_summary",
    metadata,
    Column("snapshot_id", BigInteger, _snapshot_fk(), nullable=False),
    Column("rule_id", String(80), nullable=False),
    Column("outcome", String(16), nullable=False),
    Column("finding_count", Integer, nullable=False),
    PrimaryKeyConstraint("snapshot_id", "rule_id", "outcome"),
)

record_outcome = Table(
    "record_outcome",
    metadata,
    Column("snapshot_id", BigInteger, _snapshot_fk(), nullable=False),
    Column("account_key", String(50), nullable=False),
    Column("party_key", String(50), nullable=False),
    Column("outcome", String(10), nullable=False),
    Column("reason_rule_id", String(80)),
    PrimaryKeyConstraint("snapshot_id", "account_key", "party_key"),
)
