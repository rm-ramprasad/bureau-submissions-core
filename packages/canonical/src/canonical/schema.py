"""SQLAlchemy Core tables built from the canonical model at import time.

Nothing here is hand-maintained per column: the c_* tables, their indexes and the reference tables
all come from model.yaml, and migrations are generated from these same objects (generate.py).
"""

from __future__ import annotations

import dataclasses
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)

import canonical.tenants.tables  # noqa: F401  (tenant table must exist for the foreign key)
from bureau_core.canonical import CanonicalModel, ColumnDef, EntityDef, canonical_model
from canonical.db import metadata

# BIGINT does not auto-increment in SQLite (used by tests); INTEGER does.
_PK = BigInteger().with_variant(Integer(), "sqlite")

MODEL = canonical_model()

# ---------------------------------------------------------------- snapshot header
extract_snapshot = Table(
    "extract_snapshot",
    metadata,
    Column("snapshot_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("as_of_date", Date, nullable=False),
    Column("batch_id", String(40), comment="Batch that triggered the extract (NULL for ad hoc extracts)"),
    Column("canonical_version", String(20), nullable=False),
    Column("mapping_version", String(64), comment="Hash or version of the tenant mapping used"),
    Column("status", String(12), nullable=False, comment="BUILDING / EXTRACTED / VALIDATED / FAILED / PURGED"),
    Column("build_owner", String(100), comment="Worker building the snapshot (build lock)"),
    Column("row_counts", Text, comment="JSON: rows per canonical table"),
    Column("error_message", Text),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("batch_id", "tenant_id", "submission_type", name="uq_extract_snapshot_batch"),
    Index("ix_extract_snapshot_tenant", "tenant_id", "as_of_date"),
)

# ---------------------------------------------------------------- reference data
# Canonical values (ref_code, ref_state_pin) come from model.yaml; what each submission type writes for them
# (ref_code_translation, ref_code_group) from canonical/types/<type>/codes.yaml. All four are seeded on deploy
# by canonical.reference (rows added directly in the tables are kept), so a new file type is new rows.
ref_code = Table(
    "ref_code",
    metadata,
    Column("code_list", String(40), primary_key=True),
    Column("code", String(40), primary_key=True),
    Column("description", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False),
    comment="Canonical code lists: the standard values the c_* tables hold (model.yaml)",
)

ref_state_pin = Table(
    "ref_state_pin",
    metadata,
    Column("state_code", String(40), primary_key=True),
    Column("pin_prefix_min", Integer, nullable=False),
    Column("pin_prefix_max", Integer, nullable=False),
    comment="Valid first two PIN digits per canonical state (model.yaml)",
)

ref_code_translation = Table(
    "ref_code_translation",
    metadata,
    Column("submission_type", String(20), primary_key=True),
    Column("code_list", String(40), primary_key=True),
    Column("code", String(40), primary_key=True, comment="Canonical value (ref_code)"),
    Column("output_code", String(20), nullable=False, comment="What this submission type's file holds"),
    Column("is_active", Boolean, nullable=False, comment="0 = reserved by the spec: not reportable"),
    Column("label", String(200), comment="The text the input workbooks use for this value (migration 004)"),
    comment="Canonical value -> file code per submission type (types/<type>/codes.yaml). No row = not reportable",
)

ref_code_group = Table(
    "ref_code_group",
    metadata,
    Column("submission_type", String(20), primary_key=True),
    Column("group_name", String(40), primary_key=True),
    Column("code", String(40), primary_key=True, comment="Canonical value"),
    comment="Named sets of canonical values a submission type's rules use (types/<type>/codes.yaml)",
)


snapshot_code_translation = Table(
    "snapshot_code_translation",
    metadata,
    Column(
        "snapshot_id",
        BigInteger,
        ForeignKey("automn.extract_snapshot.snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("code_list", String(40), primary_key=True),
    Column("code", String(40), primary_key=True, comment="Canonical value"),
    Column("output_code", String(20), nullable=False),
    comment="Active translations frozen when the snapshot was validated: the code rules and the file both use "
    "this copy, so a file matches what was validated (kept after retention: no personal data)",
)


def ref_code_rows(model: CanonicalModel = MODEL) -> list[dict]:
    return [
        dict(code_list=cl.name, code=c.code, description=c.description, is_active=c.active)
        for cl in model.code_lists.values()
        for c in cl.codes
    ]


def ref_state_pin_rows(model: CanonicalModel = MODEL) -> list[dict]:
    return [
        dict(state_code=c.code, pin_prefix_min=c.pin_min, pin_prefix_max=c.pin_max)
        for c in model.code_lists["state_code"].codes
        if c.pin_min is not None
    ]


# ---------------------------------------------------------------- canonical entity tables
def _sa_type(col: ColumnDef):
    if col.type in ("string", "code"):
        return String(col.length)
    return {"date": Date, "integer": Integer}.get(col.type) or Numeric(col.precision, col.scale)


def _comment(col: ColumnDef) -> str | None:
    parts = [p for p in (f"codes: {col.code_list}" if col.code_list else None, col.description) if p]
    return " ".join(parts) or None


def _entity_table(entity: EntityDef) -> Table:
    # Every column is nullable on purpose: the snapshot holds what the source holds, validation judges it.
    columns = [Column(c.name, _sa_type(c), comment=_comment(c)) for c in entity.columns]
    indexes = [Index(f"ix_{entity.table}_{'_'.join(cols)}", "snapshot_id", *cols) for cols in entity.indexes]
    return Table(
        entity.table,
        metadata,
        Column("row_id", _PK, primary_key=True, autoincrement=True),
        Column(
            "snapshot_id",
            BigInteger,
            ForeignKey("automn.extract_snapshot.snapshot_id", ondelete="CASCADE"),
            nullable=False,
        ),
        *columns,
        *indexes,
        comment=entity.description or None,
    )


ENTITY_TABLES: dict[str, Table] = {name: _entity_table(e) for name, e in MODEL.entities.items()}


# ---------------------------------------------------------------- record types for Python code
_PY_TYPES = {"string": str, "code": str, "date": date, "decimal": Decimal, "integer": int}


def _record_type(entity: EntityDef) -> type:
    name = "".join(part.title() for part in entity.name.split("_")) + "Record"
    fields = [("snapshot_id", int)] + [
        (c.name, _PY_TYPES[c.type] | None, dataclasses.field(default=None)) for c in entity.columns
    ]
    return dataclasses.make_dataclass(name, fields, frozen=True)


RECORD_TYPES: dict[str, type] = {name: _record_type(e) for name, e in MODEL.entities.items()}


def record_from_row(entity: str, row) -> object:
    """Build the entity's record from a DB row (row_id is dropped)."""
    cls = RECORD_TYPES[entity]
    m = row._mapping
    return cls(**{f.name: m[f.name] for f in dataclasses.fields(cls)})
