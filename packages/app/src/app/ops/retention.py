"""Retention: remove old PII (canonical rows, findings, record outcomes, generated files, staged input workbooks).

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
    for req in requests.finished_with_files_before(cutoff):
        uris = [
            u
            for u in (req.output_file_path, req.exceptions_file_path, req.excel_file_path, req.review_file_path)
            if u and store.owns(u)
        ]  # never elsewhere
        result.files += uris
        if dry_run:
            continue
        for uri in uris:
            store.delete(uri)
        requests.clear_files(req.request_id)
    result.loads, uploads = _input_loads_before(engine, cutoff)
    result.files += [u for u in uploads if store.owns(u)]
    if not dry_run:
        for uri in uploads:
            if store.owns(uri):
                store.delete(uri)
        purge_loads(engine, cutoff)
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


def _input_loads_before(engine: Engine, cutoff: datetime) -> tuple[list[int], list[str]]:
    with engine.connect() as conn:
        rows = list(
            conn.execute(
                select(input_load.c.load_id, input_load.c.file_uri).where(
                    input_load.c.loaded_at < cutoff, input_load.c.status != "PURGED"
                )
            )
        )
    return [r.load_id for r in rows], [r.file_uri for r in rows if r.file_uri]
