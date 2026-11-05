"""Request stages and dashboard figures (docs/ARCHITECTURE.md §5 and §6).

A *request* is a validation batch plus the file generations made from its snapshot(s). Its stage is derived
from the row statuses, never stored. All figures are computed from submission_request rows: a few thousand rows
at most for this team, so one query and Python aggregation keep it simple.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import Engine, select

from app.enums import BureauCode, RunMode, SubmissionType
from app.enums import RequestStatus as S
from app.requests.models import SubmissionRequest
from app.requests.tables import submission_request as t

ACTIVE = {S.PENDING, S.RUNNING}
STALE_DRAFT = timedelta(days=2)
STALE_VALIDATED = timedelta(days=2)
STUCK = timedelta(hours=2)


class Stage(StrEnum):
    AWAITING_EXCEPTIONS = "AWAITING_EXCEPTIONS"
    IN_PROGRESS = "IN_PROGRESS"
    VALIDATED = "VALIDATED"  # validated, files pending
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def label(self) -> str:
        return {
            "AWAITING_EXCEPTIONS": "Awaiting exceptions",
            "IN_PROGRESS": "In progress",
            "VALIDATED": "Validated, files pending",
            "COMPLETED": "Completed",
            "FAILED": "Failed",
            "CANCELLED": "Cancelled",
        }[self.value]


class Period(StrEnum):
    THIS_MONTH = "month"
    LAST_30 = "30d"
    LAST_90 = "90d"
    ALL = "all"

    @property
    def label(self) -> str:
        return {"month": "This month", "30d": "Last 30 days", "90d": "Last 90 days", "all": "All time"}[self.value]

    def start(self, now: datetime) -> datetime | None:
        if self is Period.THIS_MONTH:
            return datetime(now.year, now.month, 1)
        if self is Period.ALL:
            return None
        return now - timedelta(days=30 if self is Period.LAST_30 else 90)


@dataclass
class Request:
    batch_id: str
    rows: list[SubmissionRequest]  # the validation rows
    files: list[SubmissionRequest] = field(default_factory=list)  # GENERATE rows from its snapshots

    @property
    def first(self) -> SubmissionRequest:
        return self.rows[0]

    @property
    def tenant_id(self) -> str:
        return self.first.tenant_id

    @property
    def created_at(self) -> datetime:
        return min(r.requested_at for r in self.rows)

    @property
    def last_activity(self) -> datetime:
        return max(r.finished_at or r.started_at or r.requested_at for r in self.rows + self.files)

    @property
    def stage(self) -> Stage:
        statuses = {r.status for r in self.rows}
        file_statuses = {f.status for f in self.files}
        if statuses == {S.DRAFT}:
            return Stage.AWAITING_EXCEPTIONS
        if statuses <= {S.CANCELLED, S.DRAFT}:
            return Stage.CANCELLED
        if statuses & ACTIVE or file_statuses & ACTIVE:
            return Stage.IN_PROGRESS
        if S.SUCCEEDED in file_statuses:
            return Stage.COMPLETED
        if S.SUCCEEDED in statuses:
            return (
                Stage.FAILED
                if S.FAILED in file_statuses and not file_statuses - {S.FAILED, S.CANCELLED}
                else Stage.VALIDATED
            )
        return Stage.FAILED

    def _sum(self, rows, attr) -> int:
        return sum(getattr(r, attr) or 0 for r in rows if r.status is S.SUCCEEDED)

    @property
    def records(self) -> int:
        return self._sum(self.rows, "total_records")

    @property
    def accepted(self) -> int:
        return self._sum(self.rows, "accepted_records")

    @property
    def excluded(self) -> int:
        return self._sum(self.rows, "excluded_accounts")

    @property
    def overridden(self) -> int:
        return self._sum(self.rows, "overridden_accounts")

    @property
    def files_done(self) -> list[SubmissionRequest]:
        return [f for f in self.files if f.status is S.SUCCEEDED]

    def age(self, now: datetime) -> timedelta:
        return now - self.created_at


def load_requests(
    engine: Engine,
    tenant_id: str | None = None,
    since: datetime | None = None,
    submission_type: SubmissionType | str | None = None,
) -> list[Request]:
    """Requests (validation batches) newest first, each with the file generations made from its snapshots.

    With `submission_type`, only that type's rows count: a batch validating consumer and commercial appears with the
    stage and figures of that type alone (its files come from that type's snapshot), or not at all if it has none."""
    q = select(t)
    if tenant_id:
        q = q.where(t.c.tenant_id == tenant_id)
    if submission_type:
        q = q.where(t.c.submission_type == str(SubmissionType(submission_type)))
    with engine.connect() as conn:
        rows = [SubmissionRequest.from_row(r) for r in conn.execute(q.order_by(t.c.request_id))]
    by_batch: dict[str, list[SubmissionRequest]] = defaultdict(list)
    files_by_snapshot: dict[int, list[SubmissionRequest]] = defaultdict(list)
    for r in rows:
        if r.run_mode is RunMode.VALIDATE:
            by_batch[r.batch_id].append(r)
        elif r.snapshot_id is not None:
            files_by_snapshot[r.snapshot_id].append(r)
    requests = []
    for batch_id, vrows in by_batch.items():
        files = [f for v in vrows if v.snapshot_id for f in files_by_snapshot.get(v.snapshot_id, [])]
        req = Request(batch_id, vrows, files)
        if since is None or req.created_at >= since:
            requests.append(req)
    return sorted(requests, key=lambda r: r.created_at, reverse=True)


@dataclass(frozen=True)
class Attention:
    request: Request
    reason: str


@dataclass
class Dashboard:
    requests: list[Request]
    by_stage: dict[Stage, list[Request]]
    attention: list[Attention]
    bureaus: list[dict]
    now: datetime

    def count(self, stage: Stage) -> int:
        return len(self.by_stage.get(stage, []))

    def oldest_age(self, stage: Stage) -> timedelta | None:
        reqs = self.by_stage.get(stage, [])
        return max((r.age(self.now) for r in reqs), default=None)

    @property
    def raised(self) -> int:
        return len(self.requests)

    @property
    def completion_rate(self) -> float | None:
        base = self.raised - self.count(Stage.CANCELLED)
        return self.count(Stage.COMPLETED) / base if base else None

    def total(self, attr: str) -> int:
        return sum(getattr(r, attr) for r in self.requests)

    @property
    def acceptance_rate(self) -> float | None:
        records = self.total("records")
        return self.total("accepted") / records if records else None

    @property
    def files_generated(self) -> int:
        return sum(len(r.files_done) for r in self.requests)

    @property
    def records_submitted(self) -> int:
        return sum(f.accepted_records or 0 for r in self.requests for f in r.files_done)


def _attention(requests: list[Request], now: datetime) -> list[Attention]:
    out = []
    for r in requests:
        stage = r.stage
        if stage is Stage.FAILED:
            error = next((x.error_message for x in r.rows + r.files if x.error_message), None)
            out.append(Attention(r, f"Failed{': ' + error[:120] if error else ''}"))
        elif stage is Stage.AWAITING_EXCEPTIONS and r.age(now) > STALE_DRAFT:
            out.append(Attention(r, f"Draft for {r.age(now).days} days: submit or discard it"))
        elif stage is Stage.VALIDATED:
            done = max((x.finished_at for x in r.rows if x.finished_at), default=None)
            if done and now - done > STALE_VALIDATED:
                out.append(Attention(r, f"Validated {(now - done).days} days ago, no files generated yet"))
        elif stage is Stage.IN_PROGRESS:
            started = min(
                (x.started_at or x.requested_at for x in r.rows + r.files if x.status in ACTIVE), default=None
            )
            if started and now - started > STUCK:
                out.append(Attention(r, f"In progress for {int((now - started).total_seconds() // 3600)} hours"))
    return out


def _bureaus(requests: list[Request]) -> list[dict]:
    stats: dict[BureauCode, dict] = {}
    for r in requests:
        for f in r.files_done:
            s = stats.setdefault(f.bureau_code, {"bureau": f.bureau_code, "files": 0, "records": 0, "last": None})
            s["files"] += 1
            s["records"] += f.accepted_records or 0
            s["last"] = max(filter(None, (s["last"], f.finished_at)), default=None)
    top = max((s["records"] for s in stats.values()), default=0)
    for s in stats.values():
        s["share"] = round(100 * s["records"] / top) if top else 0
    return sorted(stats.values(), key=lambda s: s["records"], reverse=True)


def build_dashboard(
    engine: Engine,
    tenant_id: str | None,
    period: Period,
    now: datetime,
    submission_type: SubmissionType | str | None = None,
) -> Dashboard:
    """The dashboard of one submission type (record counts of different types do not add up)."""
    requests = load_requests(engine, tenant_id, period.start(now), submission_type)
    by_stage: dict[Stage, list[Request]] = defaultdict(list)
    for r in requests:
        by_stage[r.stage].append(r)
    return Dashboard(requests, dict(by_stage), _attention(requests, now), _bureaus(requests), now)


def latest_per_tenant(engine: Engine, tenant_ids: list[str]) -> dict[str, Request]:
    """Each NBFC's most recent request (any period), for the NBFC status table."""
    latest: dict[str, Request] = {}
    for r in load_requests(engine):  # newest first
        if r.tenant_id in tenant_ids and r.tenant_id not in latest:
            latest[r.tenant_id] = r
    return latest
