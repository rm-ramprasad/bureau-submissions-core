"""Shared pieces of the API routes: the blueprint, the services, request helpers and JSON shapes.

The NBFC chosen in the frontend's top bar is passed as ?tenant= (or "tenant" in the JSON body); empty = all NBFCs.
Errors are JSON {"error": message} with a 4xx status; actions answer {"message": ..., ...}.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify, request

from app.api.services import ApiServices
from app.enums import (
    ReportingCycle,
)
from app.ports.jobs import dispatch_batch
from app.requests import (
    stages,
)
from app.requests.stages import Stage

bp = Blueprint("api", __name__)
log = logging.getLogger(__name__)
PAGE_SIZE = 200
TRACKER_PAGE = 50
QUEUE_SLOW = timedelta(minutes=10)
DOWNLOADS = {
    "file": "output_file_path",
    "excel": "excel_file_path",
    "review": "review_file_path",
    "exceptions": "exceptions_file_path",
}


def svc() -> ApiServices:
    return current_app.extensions["bureau_submissions"]


def body() -> dict:
    return request.get_json(silent=True) or {}


def arg(name: str, default=None):
    """A value from the JSON body (POST) or the query string."""
    value = body().get(name) if request.method == "POST" else None
    return value if value is not None else request.args.get(name, default)


def message(text: str, status: int = 200, **extra):
    return jsonify(message=text, **extra), status


def fail(text: str, status: int = 400):
    abort(status, text)


CYCLE_LABELS = {
    ReportingCycle.WEEKLY_1: "Week 1 (W1)",
    ReportingCycle.WEEKLY_2: "Week 2 (W2)",
    ReportingCycle.WEEKLY_3: "Week 3 (W3)",
    ReportingCycle.MONTH_END: "Month end (ME)",
    ReportingCycle.DAILY: "Daily (DL)",
    ReportingCycle.DATA_CORRECTION: "Data correction (DC)",
    ReportingCycle.AD_HOC: "Ad hoc (AH)",
    ReportingCycle.REJECTION_RESUBMISSION: "Rejection resubmission (RR)",
}


def last_month_end(today: date | None = None) -> date:
    return (today or date.today()).replace(day=1) - timedelta(days=1)


def selected_tenant() -> str | None:
    """The deployment's NBFC when configured (whatever the page sends); otherwise the `tenant` argument (None = all).
    Unknown ids are refused."""
    if svc().tenant:
        return svc().tenant
    tenant_id = arg("tenant") or None
    if tenant_id and tenant_id not in {t.tenant_id for t in svc().tenants.list(active_only=False)}:
        fail(f"Unknown NBFC {tenant_id}")
    return tenant_id


def options(enum) -> list[dict]:
    return [{"value": e.value, "label": getattr(e, "label", e.value)} for e in enum]


def row_json(r) -> dict:
    return {
        "request_id": r.request_id,
        "batch_id": r.batch_id,
        "tenant_id": r.tenant_id,
        "run_mode": r.run_mode,
        "as_of_date": r.as_of_date,
        "submission_type": r.submission_type,
        "bureau_code": r.bureau_code,
        "snapshot_id": r.snapshot_id,
        "reporting_cycle": r.reporting_cycle,
        "requested_by": r.requested_by,
        "requested_at": r.requested_at,
        "status": r.status,
        "attempt_count": r.attempt_count,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "output_file_name": r.output_file_name,
        "has_file": bool(r.output_file_path),
        "has_exceptions": bool(r.exceptions_file_path),
        "has_excel": bool(r.excel_file_path),
        "has_review": bool(r.review_file_path),
        "output_location": r.output_file_path,
        "total_records": r.total_records,
        "accepted_records": r.accepted_records,
        "rejected_records": r.rejected_records,
        "duplicate_records": r.duplicate_records,
        "excluded_accounts": r.excluded_accounts,
        "overridden_accounts": r.overridden_accounts,
        "error_message": r.error_message,
    }


def request_json(r: stages.Request, now: datetime) -> dict:
    """A request (validation batch + its file generations) as the dashboard and tracker show it (one shared table)."""
    stage_label = r.stage.label
    if r.stage is Stage.IN_PROGRESS:
        stage_label = "Generating files" if any(f.status in stages.ACTIVE for f in r.files) else "Validating"
    return {
        "batch_id": r.batch_id,
        "tenant_id": r.tenant_id,
        "as_of_date": r.first.as_of_date,
        "submission_types": [x.submission_type for x in r.rows],
        "stage": r.stage,
        "stage_label": stage_label,
        "records": r.records,
        "accepted": r.accepted,
        "rejected": r.records - r.accepted if r.records else 0,
        "excluded": r.excluded,
        "overridden": r.overridden,
        "files": [f.bureau_code for f in r.files_done],
        "requested_by": r.first.requested_by,
        "created_at": r.created_at,
        "last_activity": r.last_activity,
        "age_seconds": r.age(now),
    }


def batch_json(b) -> dict:
    return {
        "batch_id": b.batch_id,
        "tenant_id": b.tenant_id,
        "run_mode": b.run_mode,
        "as_of_date": b.as_of_date,
        "requested_by": b.requested_by,
        "requested_at": b.requested_at,
        "submission_types": b.submission_types,
        "bureau_codes": b.bureau_codes,
        "status": b.status,
        "status_counts": b.status_counts,
    }


def dispatch(batch_id: str) -> None:
    """Start the worker for the batch's PENDING rows (after the commit). A failure here is repaired by recovery."""
    try:
        dispatch_batch(svc().dispatcher, svc().requests, batch_id)
    except Exception:
        log.exception("Dispatch of batch %s failed; recovery will retry it", batch_id)


def parse_date(value: str | None, label: str) -> date:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        fail(f"{label}: enter a date as YYYY-MM-DD")


def _tenant_required(what: str) -> str:
    tenant_id = selected_tenant()
    if not tenant_id:
        fail(f"Select an NBFC: {what} are per NBFC.")
    return tenant_id
