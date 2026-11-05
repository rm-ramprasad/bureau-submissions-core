"""Plain data objects returned by the repository (no DB types leak out)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from app.enums import BatchStatus, BureauCode, ReportingCycle, RequestStatus, RunMode, SubmissionType


@dataclass(frozen=True)
class SubmissionRequest:
    request_id: int
    batch_id: str
    tenant_id: str
    run_mode: RunMode
    as_of_date: date
    submission_type: SubmissionType
    bureau_code: BureauCode | None  # None for VALIDATE rows
    snapshot_id: int | None
    reporting_cycle: ReportingCycle | None
    requested_by: str | None
    requested_at: datetime
    status: RequestStatus
    worker_id: str | None
    attempt_count: int
    started_at: datetime | None
    finished_at: datetime | None
    output_file_name: str | None
    output_file_path: str | None
    exceptions_file_path: str | None
    total_records: int | None
    accepted_records: int | None
    rejected_records: int | None
    duplicate_records: int | None
    excluded_accounts: int | None
    overridden_accounts: int | None
    error_message: str | None
    excel_file_path: str | None = None  # bureau Excel (migration 002)
    review_file_path: str | None = None  # review Excel (migration 002)

    @classmethod
    def from_row(cls, row) -> SubmissionRequest:
        m = dict(row._mapping)
        m.pop("active_key", None)
        m.pop("generate_key", None)
        m["run_mode"] = RunMode(m["run_mode"])
        m["submission_type"] = SubmissionType(m["submission_type"])
        m["bureau_code"] = BureauCode(m["bureau_code"]) if m["bureau_code"] else None
        m["status"] = RequestStatus(m["status"])
        m["reporting_cycle"] = ReportingCycle(m["reporting_cycle"]) if m["reporting_cycle"] else None
        return cls(**m)


@dataclass(frozen=True)
class RequestResult:
    """What a generator reports back for one row."""

    output_file_name: str | None = None
    output_file_path: str | None = None
    exceptions_file_path: str | None = None
    excel_file_path: str | None = None  # bureau Excel (Data Submission Form)
    review_file_path: str | None = None  # review Excel (form + analysis sheets)
    total_records: int | None = None
    accepted_records: int | None = None
    rejected_records: int | None = None
    duplicate_records: int | None = None
    excluded_accounts: int | None = None
    overridden_accounts: int | None = None


@dataclass(frozen=True)
class BatchSummary:
    batch_id: str
    tenant_id: str
    run_mode: RunMode
    as_of_date: date
    requested_by: str | None
    requested_at: datetime
    submission_types: tuple[SubmissionType, ...]
    bureau_codes: tuple[BureauCode, ...]
    status_counts: dict[RequestStatus, int]
    status: BatchStatus
