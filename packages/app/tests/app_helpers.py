"""Shared test helpers for app (prefixed: unique across packages)."""

from pathlib import Path

from sqlalchemy import Engine, insert

from bureau_core.contract import CanonicalBatch, ValidationResult
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository

GOLDEN = Path(__file__).resolve().parents[2] / "bureau_core" / "tests" / "golden" / "batches"


def golden(name: str) -> tuple[CanonicalBatch, ValidationResult]:
    """A captured snapshot of the mock sources and the result the reference app recorded for it."""
    return (
        CanonicalBatch.from_json((GOLDEN / f"{name}.batch.json").read_text("utf-8")),
        ValidationResult.from_json((GOLDEN / f"{name}.result.json").read_text("utf-8")),
    )


def load_snapshot(
    engine: Engine,
    batch: CanonicalBatch,
    *,
    skip: tuple[str, ...] = (),
    batch_id: str = "b-1",
    keep_row_ids: bool = True,
) -> int:
    """Put a batch's rows into a new EXTRACTED snapshot, as an extract would. Row ids are kept (a recorded result
    refers to them) unless `keep_row_ids` is False (several batches in one database: the database assigns them)."""
    snapshots = SnapshotRepository(engine)
    sid = snapshots.create(
        batch.source.get("tenant_id", "nbfc_001"), batch.submission_type, batch.as_of_date, batch_id, "golden", "t"
    )
    counts = {}
    with engine.begin() as conn:
        for entity, rows in batch.entities.items():
            if entity in skip or not rows:
                continue
            conn.execute(
                insert(ENTITY_TABLES[entity]),
                [{**{k: v for k, v in r.items() if keep_row_ids or k != "row_id"}, "snapshot_id": sid} for r in rows],
            )
            counts[entity] = len(rows)
    snapshots.mark_extracted(sid, counts)
    return sid


def install_golden_views(engine: Engine, as_of: str = "2026-09-30") -> None:
    """Stand-ins for the client's source views on SQLite, backed by the golden batches (captures of the reference's
    mock LMS): automn.vw_<type>_<entity> over a copy of each batch's rows, returning load_id 1 and the as-of date, so
    the real pipeline (views -> snapshot with adjustments -> validation -> files) runs in tests."""
    from sqlalchemy import Column, MetaData, Table, text

    from canonical.schema import MODEL
    from sources.views import has_views

    if has_views(engine, "CONSUMER"):  # installed already (several fixtures may ask)
        return
    src = MetaData(schema="automn")
    for submission_type in ("consumer", "commercial"):
        batch, _ = golden(f"{submission_type}_nbfc_001")
        for entity, rows in batch.entities.items():
            if not rows:
                continue
            c_table = ENTITY_TABLES[entity]
            columns = [c.name for c in MODEL.entities[entity].columns]
            table = Table(f"src_{submission_type}_{entity}", src, *(Column(c, c_table.c[c].type) for c in columns))
            table.create(engine)
            with engine.begin() as conn:
                conn.execute(insert(table), [{c: r.get(c) for c in columns} for r in rows])
                conn.execute(
                    text(
                        f"CREATE VIEW automn.vw_{submission_type}_{entity} AS SELECT 1 AS load_id, "
                        f"'{as_of}' AS as_of_date, {', '.join(columns)} FROM automn.src_{submission_type}_{entity}"
                    )
                )
