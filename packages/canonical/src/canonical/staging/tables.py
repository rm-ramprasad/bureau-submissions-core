"""Staging tables for uploaded workbooks, built from the same layouts the loader uses (types/<type>/inputs.yaml), plus
the load header (input_load) and what the loader reported (input_issue)."""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, Integer, String, Table, Unicode

from canonical.db import SCHEMA, metadata
from canonical.staging.spec import VALUE_LENGTH, WorkbookSpec, available_specs, load_spec

_PK = BigInteger().with_variant(Integer(), "sqlite")
KEY_LENGTH = 100  # the key column is indexed: SQL Server index keys are limited to 1700 bytes

input_load = Table(
    "input_load",
    metadata,
    Column("load_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", String(30), nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("batch_id", String(36)),  # the request batch the workbook was uploaded for
    Column("file_name", Unicode(255), nullable=False),
    Column("file_uri", String(1000)),  # where the uploaded workbook is kept (blob)
    Column("sha256", String(64), nullable=False),
    Column("status", String(20), nullable=False),  # LOADED | FAILED
    Column("row_counts", String(2000)),  # JSON: sheet -> rows loaded
    Column("error_message", Unicode(2000)),
    Column("loaded_by", String(100), nullable=False),
    Column("loaded_at", DateTime, nullable=False),
    Index("ix_input_load_batch", "batch_id"),
    Index("ix_input_load_tenant", "tenant_id", "submission_type", "loaded_at"),
)

input_issue = Table(
    "input_issue",
    metadata,
    Column("issue_id", _PK, primary_key=True, autoincrement=True),
    Column("load_id", _PK, ForeignKey("automn.input_load.load_id"), nullable=False),
    Column("severity", String(10), nullable=False),  # ERROR (the load stops) | WARN (the row or value is reported)
    Column("code", String(40), nullable=False),
    Column("sheet", Unicode(100)),
    Column("source_row", Integer),
    Column("column_name", String(60)),
    Column("message", Unicode(1000), nullable=False),
    Index("ix_input_issue_load", "load_id"),
)


def staging_table(spec_sheet) -> Table:
    """The staging table of one sheet: (load_id, source_row) + one text column per listed header."""
    key = f"{SCHEMA}.{spec_sheet.table}"
    if key in metadata.tables:
        return metadata.tables[key]
    return Table(
        spec_sheet.table,
        metadata,
        Column("load_id", _PK, ForeignKey("automn.input_load.load_id"), primary_key=True, autoincrement=False),
        Column("source_row", Integer, primary_key=True, autoincrement=False),
        *(Column(name, Unicode(KEY_LENGTH if name == spec_sheet.key else VALUE_LENGTH)) for name in spec_sheet.columns),
        Index(f"ix_{spec_sheet.table}_key"[:60], "load_id", spec_sheet.key),
    )


def staging_tables(spec: WorkbookSpec) -> dict[str, Table]:
    """sheet name -> staging table"""
    return {s.sheet: staging_table(s) for s in spec.sheets}


STAGING_TABLES: dict[str, dict[str, Table]] = {t: staging_tables(load_spec(t)) for t in available_specs()}
