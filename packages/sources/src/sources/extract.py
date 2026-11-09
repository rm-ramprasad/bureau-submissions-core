"""Build a canonical snapshot (automn.c_*) from the source views for one upload and as-of date.

Strategies (tenant.extract_strategy):
  stream         read the SELECTs on the source engine, insert batches into c_* on the app engine
  insert_select  the views are in the app's database: one INSERT ... SELECT per entity (no rows through Python)

Exclusions and overrides (user adjustments) join in through the `adjustments` hook (implemented by the app, which
owns their tables): when a request has any, every entity is copied with INSERT ... SELECT, the SELECT wrapped so that
excluded loans drop out and overridden values replace the source values. That needs the views in the app's database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

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


class Adjustments(Protocol):
    def prepare(self, snapshot_id: int, sql: dict[str, str], run: dict[str, Any]) -> int:
        """Freeze the request's active adjustments for the snapshot and resolve what they hit; returns how many."""

    def final_select(self, entity: str, sql: str, columns: list[str]) -> str:
        """The entity's SELECT with the adjustments applied (same columns; may use :snapshot_id)."""

    def purge(self, snapshot_id: int) -> None:
        """Remove the snapshot's adjustment records (a failed extract)."""


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
    adjustments: Adjustments | None = None,
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
    run = {"as_of_date": as_of_date, "load_id": load_id, "tenant_id": tenant.tenant_id}  # each SQL binds what it uses
    snapshot_id = snapshots.create(tenant.tenant_id, submission_type, as_of_date, batch_id, compiled.version, owner)
    counts = dict.fromkeys(MODEL.entities, 0)
    try:
        adjusted = adjustments.prepare(snapshot_id, compiled.sql, run) if adjustments else 0
        if (
            adjusted
            and tenant.extract_strategy is not ExtractStrategy.INSERT_SELECT
            and source_engine is not app_engine
        ):
            raise ExtractError(
                f"{adjusted} exclusion(s)/override(s) are set for this request, but the source views are on another "
                "server: adjustments are joined in SQL, so the views must be in the app's database (or remove them)"
            )
        for entity, sql in compiled.sql.items():
            params = bind_params(sql, run)
            try:
                if adjusted and adjustments:  # joined to the adjustments: runs next to the views
                    columns = [c.name for c in MODEL.entities[entity].columns]
                    final = adjustments.final_select(entity, sql, columns)
                    counts[entity] = _insert_select(entity, final, params, snapshot_id, app_engine)
                elif tenant.extract_strategy is ExtractStrategy.INSERT_SELECT:
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
        if adjustments:
            adjustments.purge(snapshot_id)
        snapshots.mark_failed(snapshot_id, str(exc))
        raise
    return ExtractResult(snapshot_id, compiled.version, counts)
