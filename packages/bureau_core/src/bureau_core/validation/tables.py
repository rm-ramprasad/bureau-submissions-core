"""The core's private validation database (in-memory SQLite): the tables the rule SQL reads and writes, under the
same names as the app database (schema automn), so a rule's SQL is the same text on both.

Defined here, not imported from the app, so bureau_core stays independent of Layer 2. A drift test in the canonical
package checks these against the app database's tables.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Engine,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    create_engine,
    event,
)
from sqlalchemy.pool import StaticPool

from bureau_core.canonical import CanonicalModel, ColumnDef, EntityDef, canonical_model

SCHEMA = "automn"
metadata = MetaData(schema=SCHEMA)
_PK = BigInteger().with_variant(Integer(), "sqlite")
MODEL = canonical_model()


def _snapshot_fk() -> ForeignKey:
    return ForeignKey("automn.extract_snapshot.snapshot_id", ondelete="CASCADE")


tenant = Table(
    "tenant",
    metadata,
    Column("tenant_id", String(30), primary_key=True),
    Column("display_name", String(100), nullable=False),
    Column("source_schema", String(64), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

extract_snapshot = Table(
    "extract_snapshot",
    metadata,
    Column("snapshot_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("canonical_version", String(20), nullable=False),
    Column("status", String(12), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

# ---------------------------------------------------------------- reference data
ref_code = Table(
    "ref_code",
    metadata,
    Column("code_list", String(40), primary_key=True),
    Column("code", String(40), primary_key=True),
    Column("description", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False),
)
ref_state_pin = Table(
    "ref_state_pin",
    metadata,
    Column("state_code", String(40), primary_key=True),
    Column("pin_prefix_min", Integer, nullable=False),
    Column("pin_prefix_max", Integer, nullable=False),
)
ref_code_translation = Table(
    "ref_code_translation",
    metadata,
    Column("submission_type", String(20), primary_key=True),
    Column("code_list", String(40), primary_key=True),
    Column("code", String(40), primary_key=True),
    Column("output_code", String(20), nullable=False),
    Column("is_active", Boolean, nullable=False),
    Column("label", String(200)),
)
ref_code_group = Table(
    "ref_code_group",
    metadata,
    Column("submission_type", String(20), primary_key=True),
    Column("group_name", String(40), primary_key=True),
    Column("code", String(40), primary_key=True),
)
snapshot_code_translation = Table(
    "snapshot_code_translation",
    metadata,
    Column("snapshot_id", BigInteger, _snapshot_fk(), primary_key=True),
    Column("code_list", String(40), primary_key=True),
    Column("code", String(40), primary_key=True),
    Column("output_code", String(20), nullable=False),
)


# ---------------------------------------------------------------- canonical entity tables (from model.yaml)
def sa_type(col: ColumnDef) -> Any:
    if col.type in ("string", "code"):
        return String(col.length)
    return {"date": Date, "integer": Integer}.get(col.type) or Numeric(col.precision, col.scale)


def _entity_table(entity: EntityDef) -> Table:
    indexes = [Index(f"ix_{entity.table}_{'_'.join(cols)}", "snapshot_id", *cols) for cols in entity.indexes]
    return Table(
        entity.table,
        metadata,
        Column("row_id", _PK, primary_key=True, autoincrement=True),
        Column("snapshot_id", BigInteger, _snapshot_fk(), nullable=False),
        *(Column(c.name, sa_type(c)) for c in entity.columns),
        *indexes,
    )


def entity_tables(model: CanonicalModel = MODEL) -> dict[str, Table]:
    return {name: _entity_table(e) for name, e in model.entities.items()}


ENTITY_TABLES: dict[str, Table] = entity_tables()

# ---------------------------------------------------------------- results
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

# ---------------------------------------------------------------- user exceptions (read by the "matched nothing" rule)
submission_adjustment = Table(
    "submission_adjustment",
    metadata,
    Column("adjustment_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("created_by", String(100), nullable=False),
    Column("created_at", DateTime, nullable=False),
)
snapshot_adjustment = Table(
    "snapshot_adjustment",
    metadata,
    Column("snapshot_id", BigInteger, _snapshot_fk(), nullable=False),
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
snapshot_adjustment_effect = Table(
    "snapshot_adjustment_effect",
    metadata,
    Column("effect_id", _PK, primary_key=True, autoincrement=True),
    Column("snapshot_id", BigInteger, _snapshot_fk(), nullable=False),
    Column("adjustment_id", BigInteger, nullable=False),
    Column("action", String(15), nullable=False),
    Column("account_key", String(50)),
    Column("party_key", String(50)),
    Column("field", String(30)),
    Column("old_value", Numeric(20, 2)),
    Column("new_value", Numeric(20, 2)),
)
# Each frozen adjustment with the number of loans it hit (0 = not found); the same text as the app database's view.
ADJUSTMENT_VIEW_SQL = """CREATE VIEW automn.v_snapshot_adjustment AS
SELECT x.snapshot_id, x.adjustment_id, x.kind, x.account_number, x.customer_id, x.field, x.override_value,
       x.reason, x.created_by, x.created_at,
       (SELECT COUNT(DISTINCT e.account_key) FROM automn.snapshot_adjustment_effect e
        WHERE e.snapshot_id = x.snapshot_id AND e.adjustment_id = x.adjustment_id
          AND e.action IN ('EXCLUDE', 'OVERRIDE')) AS loans_affected
FROM automn.snapshot_adjustment x"""
event.listen(metadata, "after_create", DDL(ADJUSTMENT_VIEW_SQL))
event.listen(metadata, "before_drop", DDL("DROP VIEW IF EXISTS automn.v_snapshot_adjustment"))


# ---------------------------------------------------------------- the private engine
def sqlite_engine() -> Engine:
    """In-memory SQLite with schema automn attached and the T-SQL functions the rule SQL uses."""
    engine = create_engine("sqlite://", poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _attach(dbapi_conn: Any, _: Any) -> None:
        dbapi_conn.execute(f"ATTACH DATABASE ':memory:' AS {SCHEMA}")
        dbapi_conn.execute("PRAGMA foreign_keys = ON")
        dbapi_conn.create_function("LEN", 1, _len, deterministic=True)
        dbapi_conn.create_function("REGEXP_LIKE", 2, _regexp_like, deterministic=True)
        dbapi_conn.create_function("CONCAT_WS", -1, _concat_ws, deterministic=True)

    return engine


def _len(value: Any) -> int | None:
    """T-SQL LEN: characters, trailing spaces not counted."""
    return None if value is None else len(str(value).rstrip(" "))


def _regexp_like(value: Any, pattern: Any) -> int | None:
    return None if value is None or pattern is None else int(re.search(pattern, str(value)) is not None)


def _concat_ws(sep: str, *args: Any) -> str:
    return sep.join(str(a) for a in args if a is not None)
