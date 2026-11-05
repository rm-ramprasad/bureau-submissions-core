"""Lifecycle of extract snapshots (the header row; the c_* rows are written by the extract runner)."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError

from bureau_core.enums import SubmissionType
from canonical.db import utcnow
from canonical.enums import SnapshotStatus as S
from canonical.schema import MODEL
from canonical.schema import extract_snapshot as t


class SnapshotNotFoundError(LookupError):
    pass


class SnapshotExistsError(Exception):
    """A snapshot for this batch/tenant/type already exists (another worker may be building it)."""

    def __init__(self, snapshot_id: int):
        self.snapshot_id = snapshot_id
        super().__init__(f"Snapshot {snapshot_id} already exists for this batch, tenant and type")


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: int
    tenant_id: str
    submission_type: SubmissionType
    as_of_date: date
    batch_id: str | None
    canonical_version: str
    mapping_version: str | None
    status: S
    build_owner: str | None
    row_counts: dict[str, int]
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, row) -> Snapshot:
        m = dict(row._mapping)
        m["submission_type"] = SubmissionType(m["submission_type"])
        m["status"] = S(m["status"])
        m["row_counts"] = json.loads(m["row_counts"]) if m["row_counts"] else {}
        return cls(**m)


class SnapshotRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    def create(
        self,
        tenant_id: str,
        submission_type: SubmissionType | str,
        as_of_date: date,
        batch_id: str | None,
        mapping_version: str | None,
        owner: str,
    ) -> int:
        """Create a BUILDING snapshot owned by `owner`. Raises SnapshotExistsError for a duplicate batch."""
        now = self.clock()
        values = dict(
            tenant_id=tenant_id,
            submission_type=str(SubmissionType(submission_type)),
            as_of_date=as_of_date,
            batch_id=batch_id,
            canonical_version=MODEL.version,
            mapping_version=mapping_version,
            status=str(S.BUILDING),
            build_owner=owner,
            created_at=now,
            updated_at=now,
        )
        try:
            with self.engine.begin() as conn:
                return conn.execute(t.insert().values(**values)).inserted_primary_key[0]
        except IntegrityError:
            existing = self.find_for_batch(batch_id, tenant_id, submission_type) if batch_id else None
            if existing is None:
                raise
            raise SnapshotExistsError(existing.snapshot_id) from None

    def get(self, snapshot_id: int) -> Snapshot:
        with self.engine.connect() as conn:
            row = conn.execute(select(t).where(t.c.snapshot_id == snapshot_id)).first()
        if row is None:
            raise SnapshotNotFoundError(f"Snapshot {snapshot_id} not found")
        return Snapshot.from_row(row)

    def find_for_batch(self, batch_id: str, tenant_id: str, submission_type: SubmissionType | str) -> Snapshot | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(t).where(
                    t.c.batch_id == batch_id,
                    t.c.tenant_id == tenant_id,
                    t.c.submission_type == str(SubmissionType(submission_type)),
                )
            ).first()
        return Snapshot.from_row(row) if row else None

    def latest(
        self,
        tenant_id: str,
        submission_type: SubmissionType | str,
        as_of_date: date | None = None,
        status: S = S.VALIDATED,
    ) -> Snapshot | None:
        """Most recent snapshot for this tenant and type (and as-of date, if given) in the given status."""
        q = select(t).where(
            t.c.tenant_id == tenant_id,
            t.c.submission_type == str(SubmissionType(submission_type)),
            t.c.status == str(status),
        )
        if as_of_date is not None:
            q = q.where(t.c.as_of_date == as_of_date)
        with self.engine.connect() as conn:
            row = conn.execute(q.order_by(t.c.snapshot_id.desc())).first()
        return Snapshot.from_row(row) if row else None

    def list_where(
        self, statuses: list[S], updated_before: datetime | None = None, created_before: datetime | None = None
    ) -> list[Snapshot]:
        q = select(t).where(t.c.status.in_([str(s) for s in statuses]))
        if updated_before is not None:
            q = q.where(t.c.updated_at < updated_before)
        if created_before is not None:
            q = q.where(t.c.created_at < created_before)
        with self.engine.connect() as conn:
            return [Snapshot.from_row(r) for r in conn.execute(q.order_by(t.c.snapshot_id))]

    def delete(self, snapshot_id: int) -> None:
        """Remove a snapshot and (by cascade) its canonical rows and validation results."""
        with self.engine.begin() as conn:
            conn.execute(t.delete().where(t.c.snapshot_id == snapshot_id))

    def mark_purged(self, snapshot_id: int) -> None:
        self._set(snapshot_id, S.PURGED)

    def mark_extracted(self, snapshot_id: int, row_counts: dict[str, int]) -> None:
        self._set(snapshot_id, S.EXTRACTED, from_status=S.BUILDING, row_counts=json.dumps(row_counts, sort_keys=True))

    def mark_validated(self, snapshot_id: int) -> None:
        """EXTRACTED -> VALIDATED, or VALIDATED again after a re-validation."""
        self._set(snapshot_id, S.VALIDATED, from_status=(S.EXTRACTED, S.VALIDATED), error_message=None)

    def mark_failed(self, snapshot_id: int, error_message: str) -> None:
        self._set(snapshot_id, S.FAILED, error_message=error_message[:65000])

    def _set(self, snapshot_id: int, status: S, from_status: S | tuple[S, ...] | None = None, **values) -> None:
        q = update(t).where(t.c.snapshot_id == snapshot_id)
        if from_status is not None:
            allowed = from_status if isinstance(from_status, tuple) else (from_status,)
            q = q.where(t.c.status.in_([str(s) for s in allowed]))
        with self.engine.begin() as conn:
            changed = conn.execute(q.values(status=str(status), updated_at=self.clock(), **values)).rowcount
        if changed != 1:
            current = self.get(snapshot_id).status
            raise ValueError(f"Snapshot {snapshot_id} is {current}; cannot move to {status}")
