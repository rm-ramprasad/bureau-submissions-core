"""Validate a snapshot with the core and store the result in the app database.

The core (bureau_core.validation.validate) gets the snapshot as a CanonicalBatch, the client's rule set and
parameters (app.validation.settings) and the app database's reference data (it is the master of ref_code_translation
and the others). Its result is written to the same tables as before (findings, record outcomes, summary, frozen
translations), so nothing after validation changes. This is the reference app's `core` engine, the only one kept: the
`azure` engine (rules in Azure SQL) and the `shadow` comparison were retired with the move (rarfincare has run on
`core` since 2026-11-03).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, Engine, delete, func, insert, select

from app.adjustments.tables import snapshot_adjustment, snapshot_adjustment_effect
from app.enums import Outcome, RecordOutcome, SnapshotStatus
from app.validation.settings import load_parameters
from app.validation.tables import record_outcome, validation_finding, validation_summary
from bureau_core.contract import CanonicalBatch
from bureau_core.contract import ValidationResult as CoreResult
from bureau_core.validation.rules import Rule
from bureau_core.validation.validate import REFERENCE_TABLES
from bureau_core.validation.validate import validate as core_validate
from canonical.reference import freeze_translations
from canonical.schema import ENTITY_TABLES, extract_snapshot
from canonical.snapshot import SnapshotRepository


class ValidationError(Exception):
    pass


@dataclass(frozen=True)
class ValidationSummary:
    snapshot_id: int
    records: int
    accepted: int
    rejected: int
    file_rejected: bool
    findings: dict[str, int]  # outcome -> count


def capture_batch(conn: Connection, snapshot_id: int) -> CanonicalBatch:
    """The snapshot's c_* rows (and its frozen adjustments) as the core's CanonicalBatch."""
    head = conn.execute(select(extract_snapshot).where(extract_snapshot.c.snapshot_id == snapshot_id)).one()
    entities = {}
    for entity, t in ENTITY_TABLES.items():
        cols = [c for c in t.c if c.name != "snapshot_id"]
        rows = conn.execute(select(*cols).where(t.c.snapshot_id == snapshot_id).order_by(t.c.row_id)).all()
        if rows:
            entities[entity] = [dict(r._mapping) for r in rows]
    exceptions = {}
    for key, t in (("adjustments", snapshot_adjustment), ("effects", snapshot_adjustment_effect)):
        cols = [c for c in t.c if c.name != "snapshot_id"]
        order = t.c.effect_id if "effect_id" in t.c else t.c.adjustment_id
        rows = conn.execute(select(*cols).where(t.c.snapshot_id == snapshot_id).order_by(order)).all()
        if rows:
            exceptions[key] = [dict(r._mapping) for r in rows]
    return CanonicalBatch(
        head.submission_type,
        head.as_of_date,
        head.canonical_version,
        entities,
        {"tenant_id": head.tenant_id, "snapshot_id": snapshot_id, "mapping_version": head.mapping_version},
        exceptions,
    )


def snapshot_result(engine: Engine, snapshot_id: int) -> ValidationSummary:
    """The stored result of an already validated snapshot (a snapshot reused to generate files)."""
    s, o = validation_summary.c, record_outcome.c
    with engine.connect() as conn:
        by_outcome = {
            k: int(v)
            for k, v in conn.execute(
                select(s.outcome, func.sum(s.finding_count)).where(s.snapshot_id == snapshot_id).group_by(s.outcome)
            )
        }
        records: dict[str, int] = dict(
            conn.execute(select(o.outcome, func.count()).where(o.snapshot_id == snapshot_id).group_by(o.outcome)).all()
        )
    accepted, rejected = records.get(str(RecordOutcome.ACCEPTED), 0), records.get(str(RecordOutcome.REJECTED), 0)
    return ValidationSummary(
        snapshot_id, accepted + rejected, accepted, rejected, bool(by_outcome.get(str(Outcome.REJECT_FILE))), by_outcome
    )


class StreamedRows:
    """A snapshot entity's rows read from the app database a chunk at a time, each time it is iterated (the core reads
    it twice: the contract check, then the load). The whole snapshot is never held in memory (1 lakh records: about
    1 GB as Python objects)."""

    CHUNK = 5000

    def __init__(self, engine: Engine, table: Any, snapshot_id: int) -> None:
        self.engine, self.table, self.snapshot_id = engine, table, snapshot_id
        self.cols = [c for c in table.c if c.name != "snapshot_id"]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        with self.engine.connect() as conn:
            result = conn.execution_options(stream_results=True, yield_per=self.CHUNK).execute(
                select(*self.cols).where(self.table.c.snapshot_id == self.snapshot_id).order_by(self.table.c.row_id)
            )
            for chunk in result.partitions(self.CHUNK):
                for r in chunk:
                    yield dict(r._mapping)


def stream_batch(engine: Engine, snapshot_id: int) -> CanonicalBatch:
    """The snapshot as the core's CanonicalBatch, its rows streamed from the database (see StreamedRows)."""
    with engine.connect() as conn:
        head = conn.execute(select(extract_snapshot).where(extract_snapshot.c.snapshot_id == snapshot_id)).one()
        present = {
            entity
            for entity, t in ENTITY_TABLES.items()
            if conn.execute(select(t.c.row_id).where(t.c.snapshot_id == snapshot_id).limit(1)).first()
        }
        exceptions = {}
        for key, t in (("adjustments", snapshot_adjustment), ("effects", snapshot_adjustment_effect)):
            cols = [c for c in t.c if c.name != "snapshot_id"]
            order = t.c.effect_id if "effect_id" in t.c else t.c.adjustment_id
            rows = conn.execute(select(*cols).where(t.c.snapshot_id == snapshot_id).order_by(order)).all()
            if rows:
                exceptions[key] = [dict(r._mapping) for r in rows]
    # re-iterable row sources: the core reads them as it reads lists
    entities = {e: StreamedRows(engine, ENTITY_TABLES[e], snapshot_id) for e in ENTITY_TABLES if e in present}
    return CanonicalBatch(
        head.submission_type,
        head.as_of_date,
        head.canonical_version,
        entities,  # type: ignore[arg-type]
        {"tenant_id": head.tenant_id, "snapshot_id": snapshot_id, "mapping_version": head.mapping_version},
        exceptions,
    )


def core_result(engine: Engine, snapshots: SnapshotRepository, snapshot_id: int, rules: list[Rule]) -> CoreResult:
    """The core's result for the snapshot, from the app database's inputs (nothing is written)."""
    snap = snapshots.get(snapshot_id)
    parameters = load_parameters(engine, snap.tenant_id, snap.submission_type)
    with engine.connect() as conn:
        reference = {name: [dict(r._mapping) for r in conn.execute(select(t))] for name, t in REFERENCE_TABLES.items()}
    return core_validate(stream_batch(engine, snapshot_id), rules, parameters, reference)


def validate_snapshot(
    engine: Engine, snapshots: SnapshotRepository, snapshot_id: int, rules: list[Rule]
) -> ValidationSummary:
    """Validate with the core and store its result. On failure the snapshot is FAILED."""
    snap = snapshots.get(snapshot_id)
    if snap.status not in (SnapshotStatus.EXTRACTED, SnapshotStatus.VALIDATED):
        raise ValidationError(f"Snapshot {snapshot_id} is {snap.status}; only extracted snapshots can be validated")
    try:
        core = core_result(engine, snapshots, snapshot_id, rules)
        with engine.begin() as conn:
            store_result(conn, snapshot_id, str(snap.submission_type), core)
            summary = {
                o: int(n)
                for o, n in conn.execute(
                    select(validation_summary.c.outcome, func.sum(validation_summary.c.finding_count))
                    .where(validation_summary.c.snapshot_id == snapshot_id)
                    .group_by(validation_summary.c.outcome)
                )
            }
    except Exception as exc:
        snapshots.mark_failed(snapshot_id, f"validation (core): {exc}")
        raise
    snapshots.mark_validated(snapshot_id)
    accepted = sum(1 for *_, x, _ in core.outcomes if x == RecordOutcome.ACCEPTED)
    rejected = sum(1 for *_, x, _ in core.outcomes if x == RecordOutcome.REJECTED)
    return ValidationSummary(
        snapshot_id, accepted + rejected, accepted, rejected, bool(summary.get(str(Outcome.REJECT_FILE))), summary
    )


def store_result(conn: Connection, snapshot_id: int, submission_type: str, core: CoreResult) -> None:
    """Replace the snapshot's stored validation with the core's result (findings, record outcomes, summary) and
    freeze the translations it used (the app database's, the same rows the core was given)."""
    for t in (validation_finding, validation_summary, record_outcome):
        conn.execute(delete(t).where(t.c.snapshot_id == snapshot_id))
    freeze_translations(conn, snapshot_id, submission_type)
    if core.findings:
        conn.execute(insert(validation_finding), [{**f, "snapshot_id": snapshot_id} for f in core.findings])
    if core.outcomes:
        conn.execute(
            insert(record_outcome),
            [
                dict(snapshot_id=snapshot_id, account_key=a, party_key=p, outcome=x, reason_rule_id=r)
                for a, p, x, r in core.outcomes
            ],
        )
    counts = Counter((f["rule_id"], f["outcome"]) for f in core.findings)
    if counts:
        conn.execute(
            insert(validation_summary),
            [
                dict(snapshot_id=snapshot_id, rule_id=r, outcome=o, finding_count=n)
                for (r, o), n in sorted(counts.items())
            ],
        )
