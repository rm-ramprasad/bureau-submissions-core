"""All reads and writes of submission_request.

Used by the web app (create, list, read) and the worker (claim, record result).
Neither side touches the table directly.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError

from app.enums import BureauCode, ReportingCycle, RunMode, SubmissionType
from app.enums import RequestStatus as S
from app.requests.models import BatchSummary, RequestResult, SubmissionRequest
from app.requests.state import ACTIVE_STATUSES, InvalidTransitionError, check_transition, derive_batch_status
from app.requests.tables import submission_request as t
from canonical.db import utcnow


class RequestValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ActiveConflict:
    tenant_id: str
    as_of_date: date
    submission_type: SubmissionType
    run_mode: RunMode
    bureau_code: BureauCode | None

    def __str__(self) -> str:
        return f"{self.tenant_id} {self.as_of_date:%d-%m-%Y} {self.submission_type} {self.run_mode}" + (
            f" {self.bureau_code}" if self.bureau_code else ""
        )


class DuplicateActiveRequestError(Exception):
    def __init__(self, conflicts: list[ActiveConflict]):
        self.conflicts = conflicts
        super().__init__(
            f"Already open (draft, pending or running): "
            f"{', '.join(map(str, conflicts)) or 'another request'}. Submit or discard it first."
        )


class RequestNotFoundError(LookupError):
    pass


class AlreadyGeneratedError(Exception):
    """Files were already generated (or are being generated) from this validation: once per validation."""

    def __init__(self, batch_id: str | None):
        self.batch_id = batch_id
        super().__init__(
            "Files were already generated from this validation"
            + (f" (request {batch_id})" if batch_id else "")
            + ". To generate again, create a new request."
        )


LIVE_GENERATION = (S.PENDING, S.RUNNING, S.SUCCEEDED)  # a FAILED or CANCELLED generation can be retried


def active_key(
    tenant_id: str, as_of_date: date, submission_type: SubmissionType, run_mode: RunMode, bureau_code: BureauCode | None
) -> str:
    return f"{tenant_id}|{as_of_date:%Y%m%d}|{submission_type}|{run_mode}|{bureau_code or '-'}"


def generate_key(snapshot_id: int | None, bureau_code: BureauCode | str | None) -> str | None:
    return f"{snapshot_id}|{bureau_code}" if snapshot_id is not None and bureau_code else None


def new_batch_id(now: datetime) -> str:
    return f"{now:%Y%m%d%H%M%S}-{secrets.token_hex(3)}"


class RequestRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    # ------------------------------------------------------------------ create
    def create_batch(
        self,
        tenant_id: str,
        as_of_date: date,
        run_mode: RunMode | str,
        submission_types: Iterable[SubmissionType | str],
        bureau_codes: Iterable[BureauCode | str] = (),
        reporting_cycle: ReportingCycle | str | None = None,
        requested_by: str | None = None,
        snapshot_id: int | None = None,
        draft: bool = False,
    ) -> str:
        """Insert the rows for one click, all sharing a new batch_id: PENDING, or DRAFT (`draft`, VALIDATE only)
        so exceptions can be captured against the batch before it is submitted (`submit_draft`).

        VALIDATE: one row per submission type (validation is bureau-independent; no bureaus allowed).
        GENERATE: one row per bureau, always from an already validated snapshot (`snapshot_id` required):
        "generate files from this validation". There are no direct GENERATE runs.
        Tenant existence/activity is the caller's check; the foreign key is the backstop.
        """
        now = self.clock()
        mode = RunMode(run_mode)
        types = list(dict.fromkeys(SubmissionType(x) for x in submission_types))
        bureaus = list(dict.fromkeys(BureauCode(x) for x in bureau_codes))
        cycle = ReportingCycle(reporting_cycle) if reporting_cycle else None
        if not tenant_id:
            raise RequestValidationError("Select an NBFC")
        if not types:
            raise RequestValidationError("Select at least one submission type")
        if mode is RunMode.GENERATE and snapshot_id is None:
            raise RequestValidationError("Files are generated only from a validated run: validate first")
        if mode is RunMode.GENERATE and not bureaus:
            raise RequestValidationError("Select at least one bureau to generate files")
        if mode is RunMode.VALIDATE and bureaus:
            raise RequestValidationError("Validation is bureau-independent; do not select bureaus")
        if mode is RunMode.VALIDATE and snapshot_id is not None:
            raise RequestValidationError("A snapshot can only be reused to generate files")
        if snapshot_id is not None and len(types) != 1:
            raise RequestValidationError("A reused snapshot belongs to exactly one submission type")
        if as_of_date > now.date():
            raise RequestValidationError("As-of date cannot be in the future")
        if draft and mode is not RunMode.VALIDATE:
            raise RequestValidationError("Only validation requests start as drafts")

        units = [(st, bc) for st in types for bc in (bureaus or [None])]
        keys = [active_key(tenant_id, as_of_date, st, mode, bc) for st, bc in units]
        conflicts = self._active_conflicts(keys)
        if conflicts:
            raise DuplicateActiveRequestError(conflicts)
        if mode is RunMode.GENERATE and (done := self.generation_of(snapshot_id)):
            raise AlreadyGeneratedError(done)

        batch_id = new_batch_id(now)
        rows = [
            dict(
                batch_id=batch_id,
                tenant_id=tenant_id,
                run_mode=str(mode),
                as_of_date=as_of_date,
                submission_type=str(st),
                bureau_code=str(bc) if bc else None,
                snapshot_id=snapshot_id,
                reporting_cycle=str(cycle) if cycle else None,
                requested_by=requested_by,
                requested_at=now,
                status=str(S.DRAFT if draft else S.PENDING),
                active_key=key,
                generate_key=generate_key(snapshot_id, bc) if mode is RunMode.GENERATE else None,
                attempt_count=0,
            )
            for (st, bc), key in zip(units, keys, strict=True)
        ]
        try:
            with self.engine.begin() as conn:
                conn.execute(t.insert(), rows)
        except IntegrityError:
            conflicts = self._active_conflicts(keys)
            if conflicts:  # lost a race with a concurrent submit; the unique active_key caught it
                raise DuplicateActiveRequestError(conflicts) from None
            if mode is RunMode.GENERATE and (done := self.generation_of(snapshot_id)):
                raise AlreadyGeneratedError(done) from None  # the unique generate_key caught it
            raise  # something else, e.g. unknown tenant (foreign key)
        return batch_id

    def generation_of(self, snapshot_id: int | None) -> str | None:
        """The batch that generated (or is generating) files from this validated snapshot, if any."""
        if snapshot_id is None:
            return None
        with self.engine.connect() as conn:
            return conn.execute(
                select(t.c.batch_id)
                .where(
                    t.c.snapshot_id == snapshot_id,
                    t.c.run_mode == str(RunMode.GENERATE),
                    t.c.status.in_([str(x) for x in LIVE_GENERATION]),
                )
                .order_by(t.c.request_id)
                .limit(1)
            ).scalar()

    def generations(self, snapshot_ids: Iterable[int]) -> list[SubmissionRequest]:
        """Every file-generation row made from these snapshots (any status), oldest first."""
        ids = [x for x in snapshot_ids if x is not None]
        if not ids:
            return []
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t)
                .where(t.c.snapshot_id.in_(ids), t.c.run_mode == str(RunMode.GENERATE))
                .order_by(t.c.request_id)
            ).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def validation_batch_of(self, snapshot_id: int) -> str | None:
        """The request (validation batch) whose snapshot this is."""
        with self.engine.connect() as conn:
            return conn.execute(
                select(t.c.batch_id)
                .where(t.c.snapshot_id == snapshot_id, t.c.run_mode == str(RunMode.VALIDATE))
                .order_by(t.c.request_id)
                .limit(1)
            ).scalar()

    def _active_conflicts(self, keys: list[str]) -> list[ActiveConflict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t.c.tenant_id, t.c.as_of_date, t.c.submission_type, t.c.run_mode, t.c.bureau_code).where(
                    t.c.active_key.in_(keys)
                )
            ).all()
        return [
            ActiveConflict(
                r.tenant_id,
                r.as_of_date,
                SubmissionType(r.submission_type),
                RunMode(r.run_mode),
                BureauCode(r.bureau_code) if r.bureau_code else None,
            )
            for r in rows
        ]

    # ------------------------------------------------------------------ read
    def get(self, request_id: int) -> SubmissionRequest:
        with self.engine.connect() as conn:
            row = conn.execute(select(t).where(t.c.request_id == request_id)).first()
        if row is None:
            raise RequestNotFoundError(f"Request {request_id} not found")
        return SubmissionRequest.from_row(row)

    def list_batch(self, batch_id: str) -> list[SubmissionRequest]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t).where(t.c.batch_id == batch_id).order_by(t.c.submission_type, t.c.bureau_code)
            ).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def list_batches(
        self,
        limit: int = 50,
        tenant_id: str | None = None,
        run_mode: RunMode | None = None,
        bureau_code: BureauCode | None = None,
        requested_from: datetime | None = None,
        requested_to: datetime | None = None,
    ) -> list[BatchSummary]:
        """Most recent batches first, each with its derived status. Optional filters on the rows."""
        latest_q = select(t.c.batch_id, func.max(t.c.requested_at).label("at")).group_by(t.c.batch_id)
        if tenant_id is not None:
            latest_q = latest_q.where(t.c.tenant_id == tenant_id)
        if run_mode is not None:
            latest_q = latest_q.where(t.c.run_mode == str(run_mode))
        if bureau_code is not None:
            latest_q = latest_q.where(t.c.bureau_code == str(bureau_code))
        if requested_from is not None:
            latest_q = latest_q.where(t.c.requested_at >= requested_from)
        if requested_to is not None:
            latest_q = latest_q.where(t.c.requested_at < requested_to)
        with self.engine.connect() as conn:
            latest = latest_q.order_by(func.max(t.c.requested_at).desc()).limit(limit).subquery()
            rows = conn.execute(
                select(t).join(latest, latest.c.batch_id == t.c.batch_id).order_by(latest.c.at.desc(), t.c.request_id)
            ).all()
        grouped: dict[str, list[SubmissionRequest]] = {}
        for r in rows:
            grouped.setdefault(r.batch_id, []).append(SubmissionRequest.from_row(r))
        return [self._summarise(batch_id, reqs) for batch_id, reqs in grouped.items()]

    @staticmethod
    def _summarise(batch_id: str, reqs: list[SubmissionRequest]) -> BatchSummary:
        counts: dict[S, int] = {}
        for r in reqs:
            counts[r.status] = counts.get(r.status, 0) + 1
        first = reqs[0]
        return BatchSummary(
            batch_id=batch_id,
            tenant_id=first.tenant_id,
            run_mode=first.run_mode,
            as_of_date=first.as_of_date,
            requested_by=first.requested_by,
            requested_at=first.requested_at,
            submission_types=tuple(dict.fromkeys(r.submission_type for r in reqs)),
            bureau_codes=tuple(dict.fromkeys(r.bureau_code for r in reqs if r.bureau_code)),
            status_counts=counts,
            status=derive_batch_status(r.status for r in reqs),
        )

    def list_files(
        self,
        tenant_id: str | None = None,
        bureau_code: BureauCode | None = None,
        since: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[SubmissionRequest], int]:
        """Generated bureau files (succeeded GENERATE rows, newest first) and the total count.
        Rows whose files retention removed are included (output_file_path is NULL)."""
        conds = [
            t.c.run_mode == str(RunMode.GENERATE),
            t.c.status == str(S.SUCCEEDED),
            t.c.output_file_name.is_not(None),
        ]
        if tenant_id:
            conds.append(t.c.tenant_id == tenant_id)
        if bureau_code:
            conds.append(t.c.bureau_code == str(bureau_code))
        if since:
            conds.append(t.c.finished_at >= since)
        with self.engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(t).where(*conds)).scalar()
            rows = conn.execute(
                select(t)
                .where(*conds)
                .order_by(t.c.finished_at.desc(), t.c.request_id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        return [SubmissionRequest.from_row(r) for r in rows], int(total)

    def open_draft(self, tenant_id: str, as_of_date: date, submission_type: SubmissionType | str) -> str | None:
        """The batch id of the open DRAFT request for this NBFC, date and type, if any."""
        with self.engine.connect() as conn:
            return conn.execute(
                select(t.c.batch_id)
                .where(
                    t.c.tenant_id == tenant_id,
                    t.c.as_of_date == as_of_date,
                    t.c.status == str(S.DRAFT),
                    t.c.submission_type == str(SubmissionType(submission_type)),
                    t.c.run_mode == str(RunMode.VALIDATE),
                )
                .order_by(t.c.request_id.desc())
            ).scalar()

    def running_started_before(self, cutoff: datetime) -> list[SubmissionRequest]:
        """RUNNING rows whose worker started them before `cutoff` (candidates for stuck-row recovery)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t).where(t.c.status == str(S.RUNNING), t.c.started_at < cutoff).order_by(t.c.started_at)
            ).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def finished_with_files_before(self, cutoff: datetime) -> list[SubmissionRequest]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t).where(
                    t.c.finished_at < cutoff,
                    (t.c.output_file_path.is_not(None))
                    | (t.c.exceptions_file_path.is_not(None))
                    | (t.c.excel_file_path.is_not(None))
                    | (t.c.review_file_path.is_not(None)),
                )
            ).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def clear_files(self, request_id: int) -> None:
        """Forget file paths after retention removed the files."""
        with self.engine.begin() as conn:
            conn.execute(
                update(t)
                .where(t.c.request_id == request_id)
                .values(output_file_path=None, exceptions_file_path=None, excel_file_path=None, review_file_path=None)
            )

    def submit_draft(self, batch_id: str) -> int:
        """Send a DRAFT batch to validation (DRAFT -> PENDING). Its exceptions are locked from now on."""
        with self.engine.begin() as conn:
            moved = conn.execute(
                update(t)
                .where(t.c.batch_id == batch_id, t.c.status == str(S.DRAFT))
                .values(status=str(S.PENDING), requested_at=self.clock())
            ).rowcount
        if moved == 0:
            raise InvalidTransitionError(f"Request {batch_id} is not a draft; it was already submitted or cancelled")
        return moved

    # ------------------------------------------------------------------ worker
    def claim_next(self, worker_id: str) -> SubmissionRequest | None:
        """Atomically move the oldest PENDING row to RUNNING for this worker."""
        while True:
            with self.engine.begin() as conn:
                candidate = conn.execute(
                    select(t.c.request_id)
                    .where(t.c.status == str(S.PENDING))
                    .order_by(t.c.requested_at, t.c.request_id)
                    .limit(1)
                ).scalar()
                if candidate is None:
                    return None
                claimed = conn.execute(
                    update(t)
                    .where(t.c.request_id == candidate, t.c.status == str(S.PENDING))
                    .values(
                        status=str(S.RUNNING),
                        worker_id=worker_id,
                        started_at=self.clock(),
                        finished_at=None,
                        error_message=None,
                        attempt_count=t.c.attempt_count + 1,
                    )
                ).rowcount
            if claimed == 1:
                return self.get(candidate)
            # another worker took it first; try the next one

    def claim(self, request_id: int, worker_id: str) -> SubmissionRequest | None:
        """Atomically move this PENDING row to RUNNING (queue-triggered worker). None if it is not PENDING (any more):
        a duplicate delivery, or the row was cancelled or already processed."""
        with self.engine.begin() as conn:
            claimed = conn.execute(
                update(t)
                .where(t.c.request_id == request_id, t.c.status == str(S.PENDING))
                .values(
                    status=str(S.RUNNING),
                    worker_id=worker_id,
                    started_at=self.clock(),
                    finished_at=None,
                    error_message=None,
                    attempt_count=t.c.attempt_count + 1,
                )
            ).rowcount
        return self.get(request_id) if claimed == 1 else None

    def pending_before(self, cutoff: datetime) -> list[SubmissionRequest]:
        """PENDING rows queued before `cutoff` (their dispatch may have been lost: re-dispatch them)."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(t).where(t.c.status == str(S.PENDING), t.c.requested_at < cutoff).order_by(t.c.requested_at)
            ).all()
        return [SubmissionRequest.from_row(r) for r in rows]

    def oldest_pending(self) -> datetime | None:
        """When the longest-waiting PENDING row was queued (queue health on the UI)."""
        with self.engine.connect() as conn:
            return conn.execute(select(func.min(t.c.requested_at)).where(t.c.status == str(S.PENDING))).scalar()

    def assign_snapshot(self, request_id: int, snapshot_id: int) -> None:
        """Record which extract snapshot an active row is using (set by the worker)."""
        with self.engine.begin() as conn:
            changed = conn.execute(
                update(t)
                .where(t.c.request_id == request_id, t.c.status.in_([str(s) for s in ACTIVE_STATUSES]))
                .values(snapshot_id=snapshot_id)
            ).rowcount
        if changed != 1:
            raise InvalidTransitionError(f"Request {request_id} is not active; cannot assign a snapshot")

    def release(self, request_id: int) -> None:
        """Hand a RUNNING row back to the queue (e.g. its snapshot is being built by another worker)."""
        self._transition(request_id, S.PENDING, worker_id=None, started_at=None)

    def mark_succeeded(self, request_id: int, result: RequestResult) -> None:
        self._transition(request_id, S.SUCCEEDED, finished_at=self.clock(), active_key=None, **vars(result))

    def mark_failed(self, request_id: int, error_message: str, review_file_path: str | None = None) -> None:
        """`review_file_path`: the review Excel of a generation whose reconciliation did not match (kept so the
        credit team can see why)."""
        self._transition(
            request_id,
            S.FAILED,
            finished_at=self.clock(),
            active_key=None,
            generate_key=None,
            error_message=error_message[:65000],
            review_file_path=review_file_path,
        )

    # ------------------------------------------------------------------ user actions
    def cancel(self, request_id: int) -> None:
        self._transition(request_id, S.CANCELLED, finished_at=self.clock(), active_key=None, generate_key=None)

    def requeue(self, request_id: int) -> None:
        """Retry a FAILED or CANCELLED row. Fails if the same unit of work is already active, or (a generation) if
        that validation's files were generated meanwhile for the bureau."""
        req = self.get(request_id)
        key = active_key(req.tenant_id, req.as_of_date, req.submission_type, req.run_mode, req.bureau_code)
        gen = generate_key(req.snapshot_id, req.bureau_code) if req.run_mode is RunMode.GENERATE else None
        try:
            self._transition(
                request_id,
                S.PENDING,
                active_key=key,
                generate_key=gen,
                worker_id=None,
                started_at=None,
                finished_at=None,
                error_message=None,
            )
        except IntegrityError:
            conflicts = self._active_conflicts([key])
            if conflicts or gen is None:
                raise DuplicateActiveRequestError(conflicts) from None
            raise AlreadyGeneratedError(self.generation_of(req.snapshot_id)) from None

    # ------------------------------------------------------------------ internals
    def _transition(self, request_id: int, target: S, **values) -> None:
        current = self.get(request_id).status
        check_transition(current, target)
        with self.engine.begin() as conn:
            changed = conn.execute(
                update(t)
                .where(t.c.request_id == request_id, t.c.status == str(current))
                .values(status=str(target), **values)
            ).rowcount
        if changed != 1:
            raise InvalidTransitionError(f"Request {request_id} changed concurrently; expected {current}")
