"""Recover from crashed or killed workers. Runs at the start of every worker poll (cheap queries).

- RUNNING rows older than `run_timeout` go back to PENDING, or FAILED once `max_attempts` is reached.
- Snapshots stuck in BUILDING/EXTRACTED longer than `build_timeout` are failed and their rows removed,
  so a retried row can rebuild them.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import Engine

from app import audit
from app.enums import SnapshotStatus
from app.requests import InvalidTransitionError, RequestRepository
from canonical.db import utcnow
from canonical.snapshot import SnapshotRepository
from sources.extract import purge_rows

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecoverySettings:
    run_timeout: timedelta = timedelta(hours=2)
    build_timeout: timedelta = timedelta(hours=2)
    max_attempts: int = 3


@dataclass
class RecoveryResult:
    released: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    snapshots_failed: list[int] = field(default_factory=list)


def recover(
    engine: Engine,
    requests: RequestRepository,
    snapshots: SnapshotRepository,
    settings: RecoverySettings = RecoverySettings(),  # noqa: B008 (frozen: safe to share)
    clock: Callable[[], datetime] = utcnow,
) -> RecoveryResult:
    now, result = clock(), RecoveryResult()
    for req in requests.running_started_before(now - settings.run_timeout):
        try:
            if req.attempt_count >= settings.max_attempts:
                requests.mark_failed(
                    req.request_id,
                    f"Timed out after {req.attempt_count} attempt(s): the worker "
                    f"running it stopped or took longer than {settings.run_timeout}",
                )
                result.failed.append(req.request_id)
            else:
                requests.release(req.request_id)
                result.released.append(req.request_id)
        except InvalidTransitionError:
            continue  # finished meanwhile
        audit.record(
            engine,
            "recovery",
            "REQUEST_RECOVERED",
            tenant_id=req.tenant_id,
            target=req.request_id,
            worker_id=req.worker_id,
            started_at=req.started_at,
            outcome="failed" if req.request_id in result.failed else "released",
        )
    for snap in snapshots.list_where(
        [SnapshotStatus.BUILDING, SnapshotStatus.EXTRACTED], updated_before=now - settings.build_timeout
    ):
        purge_rows(engine, snap.snapshot_id)
        snapshots.mark_failed(
            snap.snapshot_id, f"Build abandoned by {snap.build_owner} (no progress for {settings.build_timeout})"
        )
        result.snapshots_failed.append(snap.snapshot_id)
        audit.record(
            engine,
            "recovery",
            "SNAPSHOT_ABANDONED",
            tenant_id=snap.tenant_id,
            target=snap.snapshot_id,
            build_owner=snap.build_owner,
        )
    if result.released or result.failed or result.snapshots_failed:
        log.warning(
            "Recovery: released %s, failed %s, abandoned snapshots %s",
            result.released,
            result.failed,
            result.snapshots_failed,
        )
    return result
