"""Retention: remove old PII from the database (canonical rows, findings, record outcomes, staged input rows).

Files are never removed (owner decision 2026-11-06): the uploaded input workbooks and the generated outputs (bureau
file, exceptions file, bureau Excel, review Excel) stay in storage for good. The retention policy for the database
is to be finalized; until then SUBMISSIONS_RETENTION_DAYS applies.

The snapshot header and its per-rule validation_summary are kept for trends; the snapshot becomes PURGED. An input
load keeps its input_load header (PURGED); its staged rows, issues and uploaded workbook are removed.

Run by the retention job (a timer on Azure; its entry point comes with the shell, step 1.3e).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Engine, delete, select

from app import audit, reconciliation
from app.enums import SnapshotStatus
from app.ports.files import FileStore
from app.requests import RequestRepository
from app.validation.tables import record_outcome, validation_finding
from canonical.db import utcnow
from canonical.snapshot import SnapshotRepository
from canonical.staging.tables import input_load
from sources.excel import purge_loads
from sources.extract import purge_rows

PURGEABLE = [SnapshotStatus.EXTRACTED, SnapshotStatus.VALIDATED, SnapshotStatus.FAILED]


@dataclass
class PurgeResult:
    snapshots: list[int] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    loads: list[int] = field(default_factory=list)


def purge(
    engine: Engine,
    older_than: timedelta,
    store: FileStore,
    *,
    dry_run: bool = False,
    actor: str = "purge",
    clock: Callable[[], datetime] = utcnow,
) -> PurgeResult:
    cutoff, result = clock() - older_than, PurgeResult()
    snapshots, requests = SnapshotRepository(engine), RequestRepository(engine)
    for snap in snapshots.list_where(PURGEABLE, created_before=cutoff):
        result.snapshots.append(snap.snapshot_id)
        if dry_run:
            continue
        purge_rows(engine, snap.snapshot_id)
        reconciliation.remove_mismatches(  # cell values: personal data; the counts stay for trends
            engine, [g.request_id for g in requests.generations([snap.snapshot_id])]
        )
        with engine.begin() as conn:
            for t in (validation_finding, record_outcome):
                conn.execute(delete(t).where(t.c.snapshot_id == snap.snapshot_id))
        snapshots.mark_purged(snap.snapshot_id)
    result.loads = _input_loads_before(engine, cutoff)
    if not dry_run:
        purge_loads(engine, cutoff)  # staged rows only; the uploaded workbook file stays
    if not dry_run and (result.snapshots or result.files or result.loads):
        audit.record(
            engine,
            actor,
            "RETENTION_PURGE",
            target=f"before {cutoff:%Y-%m-%d}",
            snapshots=result.snapshots,
            files=len(result.files),
            input_loads=len(result.loads),
        )
    return result


def _input_loads_before(engine: Engine, cutoff: datetime) -> list[int]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                select(input_load.c.load_id).where(input_load.c.loaded_at < cutoff, input_load.c.status != "PURGED")
            ).scalars()
        )
