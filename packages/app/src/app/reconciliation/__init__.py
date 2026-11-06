"""Reconciliation results in the database (issue #39): each generated consumer file's reconciliation (bureau Excel
vs TUDF), kept like the validation results so trends across months can be queried and the result outlives the
files. Tables created by migration 007. The review Excel shows the same data (built from the same result)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    Index,
    Integer,
    String,
    Table,
    delete,
    insert,
    select,
)

from bureau_core.tudf.reconcile import Reconciliation
from canonical.db import metadata, utcnow

_PK = BigInteger().with_variant(Integer(), "sqlite")

reconciliation_summary = Table(
    "reconciliation_summary",
    metadata,
    Column("request_id", BigInteger, primary_key=True, autoincrement=False),  # the GENERATE request (not a new id)
    Column("snapshot_id", BigInteger, nullable=False),
    Column("submission_type", String(20), nullable=False),
    Column("bureau_code", String(20), nullable=False),
    Column("matched", Boolean, nullable=False),
    Column("form_rows", Integer, nullable=False),
    Column("tudf_records", Integer, nullable=False),
    Column("mismatches", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Index("ix_reconciliation_summary_time", "created_at"),
)
reconciliation_check = Table(
    "reconciliation_check",
    metadata,
    Column("request_id", BigInteger, primary_key=True),
    Column("check_no", Integer, primary_key=True),
    Column("check_name", String(200), nullable=False),
    Column("form_value", String(500)),
    Column("tudf_value", String(500)),
    Column("ok", Boolean, nullable=False),
)
reconciliation_column = Table(
    "reconciliation_column",
    metadata,
    Column("request_id", BigInteger, primary_key=True),
    Column("column_no", Integer, primary_key=True),
    Column("column_name", String(200), nullable=False),
    Column("tudf_place", String(200), nullable=False),
    Column("compared", Integer, nullable=False),
    Column("matched", Integer, nullable=False),
    Column("both_empty", Integer, nullable=False),
    Column("spec_rules", String(1000)),
    Column("mismatched", Integer, nullable=False),
)
reconciliation_mismatch = Table(  # cell values: personal data, removed by retention with the snapshot's rows
    "reconciliation_mismatch",
    metadata,
    Column("mismatch_id", _PK, primary_key=True, autoincrement=True),
    Column("request_id", BigInteger, nullable=False),
    Column("form_row", Integer),
    Column("column_name", String(200), nullable=False),
    Column("form_value", String(500)),
    Column("tudf_place", String(200)),
    Column("tudf_value", String(500)),
    Column("problem", String(500)),
    Index("ix_reconciliation_mismatch_request", "request_id"),
)
TABLES = (reconciliation_mismatch, reconciliation_column, reconciliation_check, reconciliation_summary)


def _text(value: Any, length: int) -> str | None:
    return None if value is None else str(value)[:length]


def mismatch_count(recon: Reconciliation) -> int:
    return sum(c.mismatched for c in recon.columns) + sum(1 for c in recon.checks if not c[3])


def save(
    engine: Engine, request_id: int, snapshot_id: int, submission_type: str, bureau_code: str, recon: Reconciliation
) -> None:
    """Store one generated file's reconciliation (replacing an earlier one for the same request: a retry)."""
    with engine.begin() as conn:
        for t in TABLES:
            conn.execute(delete(t).where(t.c.request_id == request_id))
        conn.execute(
            insert(reconciliation_summary).values(
                request_id=request_id, snapshot_id=snapshot_id, submission_type=str(submission_type),
                bureau_code=str(bureau_code), matched=recon.matched, form_rows=recon.form_rows,
                tudf_records=recon.tudf_records, mismatches=mismatch_count(recon), created_at=utcnow(),
            )
        )  # fmt: skip
        if recon.checks:
            conn.execute(
                insert(reconciliation_check),
                [
                    dict(
                        request_id=request_id,
                        check_no=i,
                        check_name=_text(name, 200),
                        form_value=_text(form, 500),
                        tudf_value=_text(tudf, 500),
                        ok=bool(ok),
                    )
                    for i, (name, form, tudf, ok) in enumerate(recon.checks, 1)
                ],
            )
        if recon.columns:
            conn.execute(
                insert(reconciliation_column),
                [
                    dict(
                        request_id=request_id,
                        column_no=i,
                        column_name=_text(c.place.header, 200),
                        tudf_place=_text(c.place.label, 200),
                        compared=c.compared,
                        matched=c.matched,
                        both_empty=c.both_empty,
                        mismatched=c.mismatched,
                        spec_rules=_text("; ".join(f"{n} x {r}" for r, n in c.spec_rules.items()), 1000) or None,
                    )
                    for i, c in enumerate(recon.columns, 1)
                ],
            )
        if recon.mismatches:
            conn.execute(
                insert(reconciliation_mismatch),
                [
                    dict(
                        request_id=request_id,
                        form_row=row,
                        column_name=_text(col, 200),
                        form_value=_text(fv, 500),
                        tudf_place=_text(place, 200),
                        tudf_value=_text(tv, 500),
                        problem=_text(problem, 500),
                    )
                    for row, col, fv, place, tv, problem in recon.mismatches
                ],
            )


def load(engine: Engine, request_id: int) -> dict[str, Any] | None:
    """One file's stored reconciliation: the summary (`mismatches` = the count), `checks`, `columns` and
    `mismatch_cells` (the differing cells, capped), or None."""
    with engine.connect() as conn:
        summary = conn.execute(
            select(reconciliation_summary).where(reconciliation_summary.c.request_id == request_id)
        ).first()
        if summary is None:
            return None

        def rows(t: Table, order: Any) -> list[dict[str, Any]]:
            q = select(*(c for c in t.c if c.name != "request_id")).where(t.c.request_id == request_id).order_by(order)
            return [dict(r._mapping) for r in conn.execute(q)]

        return {
            **dict(summary._mapping),
            "checks": rows(reconciliation_check, reconciliation_check.c.check_no),
            "columns": rows(reconciliation_column, reconciliation_column.c.column_no),
            "mismatch_cells": rows(reconciliation_mismatch, reconciliation_mismatch.c.mismatch_id),
        }


def remove_mismatches(engine: Engine, request_ids: list[int]) -> int:
    """Retention: remove the mismatching cell values (personal data); the counts stay for trends."""
    if not request_ids:
        return 0
    with engine.begin() as conn:
        t = reconciliation_mismatch
        return int(conn.execute(delete(t).where(t.c.request_id.in_(request_ids))).rowcount or 0)
