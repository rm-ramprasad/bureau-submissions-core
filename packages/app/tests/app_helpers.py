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


def load_snapshot(engine: Engine, batch: CanonicalBatch, *, skip: tuple[str, ...] = (), batch_id: str = "b-1") -> int:
    """Put a batch's rows into a new EXTRACTED snapshot (row ids kept), as an extract would."""
    snapshots = SnapshotRepository(engine)
    sid = snapshots.create(
        batch.source.get("tenant_id", "nbfc_001"), batch.submission_type, batch.as_of_date, batch_id, "golden", "t"
    )
    counts = {}
    with engine.begin() as conn:
        for entity, rows in batch.entities.items():
            if entity in skip or not rows:
                continue
            conn.execute(insert(ENTITY_TABLES[entity]), [{**r, "snapshot_id": sid} for r in rows])
            counts[entity] = len(rows)
    snapshots.mark_extracted(sid, counts)
    return sid
