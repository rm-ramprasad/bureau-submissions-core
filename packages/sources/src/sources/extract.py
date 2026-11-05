"""Build a canonical snapshot (automn.c_*) from the source views for one upload and as-of date.

Strategies (tenant.extract_strategy):
  stream         read the SELECTs on the source engine, insert batches into c_* on the app engine
  insert_select  the views are in the app's database: one INSERT ... SELECT per entity (no rows through Python)

Exclusions and overrides (user adjustments) are joined in here in step 1.3, with their tables (app layer).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Engine, delete, text

from bureau_core.canonical import ColumnDef
from canonical.enums import ExtractStrategy
from canonical.schema import ENTITY_TABLES, MODEL
from canonical.snapshot import SnapshotRepository
from canonical.tenants.models import Tenant
from sources.views import CompiledViews, bind_params

BATCH_SIZE = 1000


class ExtractError(Exception):
    pass


@dataclass(frozen=True)
class ExtractResult:
    snapshot_id: int
    mapping_version: str
    row_counts: dict[str, int]


def coerce(col: ColumnDef, value: Any) -> Any:
    """Convert a source value to the canonical column type (drivers differ, e.g. SQLite returns dates as text)."""
    if value is None:
        return None
    try:
        if col.type in ("string", "code"):
            return value if isinstance(value, str) else str(value)
        if col.type == "date":
            if isinstance(value, datetime):
                return value.date()
            return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
        if col.type == "decimal":
            return value if isinstance(value, Decimal) else Decimal(str(value))
        return int(value)
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise ExtractError(f"cannot convert {value!r} to {col.type} for column {col.name}") from exc


def _stream(entity: str, sql: str, params: dict[str, Any], snapshot_id: int, source: Engine, app: Engine) -> int:
    table = ENTITY_TABLES[entity]
    cols = MODEL.entities[entity].columns
    count = 0
    with source.connect() as src, app.begin() as dst:
        result = src.execution_options(stream_results=True).execute(text(sql), params)
        for chunk in result.partitions(BATCH_SIZE):
            rows = []
            for r in chunk:
                m = r._mapping
                try:
                    rows.append({"snapshot_id": snapshot_id, **{c.name: coerce(c, m[c.name]) for c in cols}})
                except ExtractError as exc:
                    raise ExtractError(f"{entity}: {exc}") from exc
            dst.execute(table.insert(), rows)
            count += len(rows)
    return count


def _insert_select(entity: str, sql: str, params: dict[str, Any], snapshot_id: int, app: Engine) -> int:
    table = ENTITY_TABLES[entity]
    names = [c.name for c in MODEL.entities[entity].columns]
    stmt = (
        f"INSERT INTO {table.fullname} (snapshot_id, {', '.join(names)})\n"
        f"SELECT :snapshot_id, {', '.join('q.' + n for n in names)}\nFROM (\n{sql}\n) q"
    )
    with app.begin() as conn:
        return conn.execute(text(stmt), {**params, "snapshot_id": snapshot_id}).rowcount


def purge_rows(app: Engine, snapshot_id: int) -> None:
    """Delete a snapshot's canonical rows (the snapshot header stays)."""
    with app.begin() as conn:
        for table in ENTITY_TABLES.values():
            conn.execute(delete(table).where(table.c.snapshot_id == snapshot_id))


def run_extract(
    *,
    tenant: Tenant,
    compiled: CompiledViews,
    submission_type: str,
    as_of_date: date,
    app_engine: Engine,
    source_engine: Engine,
    snapshots: SnapshotRepository,
    owner: str,
    batch_id: str | None = None,
    load_id: int | None = None,
) -> ExtractResult:
    """Create a snapshot and fill its canonical tables from `compiled` (sources.views.compile_views).
    `load_id`: the request's loaded input workbook (automn.input_load), which the views filter on.

    On failure the rows are removed and the snapshot is FAILED.
    """
    if load_id is None and compiled.uses("load_id"):
        raise ExtractError(
            "The views read the uploaded input workbook (:load_id), but this request has no successfully loaded "
            "workbook: upload it on the request before submitting"
        )
    run = {"as_of_date": as_of_date, "load_id": load_id}
    snapshot_id = snapshots.create(tenant.tenant_id, submission_type, as_of_date, batch_id, compiled.version, owner)
    counts = dict.fromkeys(MODEL.entities, 0)
    try:
        for entity, sql in compiled.sql.items():
            params = bind_params(sql, run)
            try:
                if tenant.extract_strategy is ExtractStrategy.INSERT_SELECT:
                    counts[entity] = _insert_select(entity, sql, params, snapshot_id, app_engine)
                else:
                    counts[entity] = _stream(entity, sql, params, snapshot_id, source_engine, app_engine)
            except ExtractError:
                raise
            except Exception as exc:
                raise ExtractError(f"{entity}: {exc}") from exc
        snapshots.mark_extracted(snapshot_id, counts)
    except Exception as exc:
        purge_rows(app_engine, snapshot_id)
        snapshots.mark_failed(snapshot_id, str(exc))
        raise
    return ExtractResult(snapshot_id, compiled.version, counts)
