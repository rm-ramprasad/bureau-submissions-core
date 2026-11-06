"""Input workbooks of a request: upload (stored, then loaded into the staging tables at once so the user sees the
result) and status. One workbook per submission type of the request; while the request is a draft it can be
replaced, and the latest upload is the one the request uses. After submit the uploads are locked.

Routes are on the API blueprint (/api/batches/<batch_id>/inputs...).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from flask import jsonify, request
from sqlalchemy import Engine, select

from app import audit
from app.api.auth import current_user
from app.api.common import bp, fail, svc
from app.enums import RequestStatus, RunMode
from canonical.staging.checks import run_checks
from canonical.staging.loads import latest_loads
from canonical.staging.spec import available_specs
from canonical.staging.tables import input_issue
from sources.excel import load_workbook

log = logging.getLogger(__name__)
MAX_UPLOAD = 25 * 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ISSUES_SHOWN = 200


def needs_workbook(submission_type: str) -> bool:
    return submission_type in available_specs()


def missing_workbooks(engine: Engine, batch_id: str, submission_types) -> list[str]:
    """Types of the request that still need a successfully loaded workbook."""
    loads = latest_loads(engine, batch_id)
    return [t for t in submission_types if needs_workbook(t) and loads.get(t, {}).get("status") != "LOADED"]


def _load_json(engine: Engine, load: dict | None) -> dict | None:
    if load is None:
        return None
    with engine.connect() as conn:
        issues = [
            dict(r._mapping)
            for r in conn.execute(
                select(
                    input_issue.c.severity,
                    input_issue.c.code,
                    input_issue.c.sheet,
                    input_issue.c.source_row,
                    input_issue.c.column_name,
                    input_issue.c.message,
                )
                .where(input_issue.c.load_id == load["load_id"])
                .order_by(input_issue.c.issue_id)
                .limit(ISSUES_SHOWN)
            )
        ]
    return {k: load[k] for k in ("load_id", "file_name", "status", "loaded_by", "loaded_at", "error_message")} | {
        "row_counts": json.loads(load["row_counts"] or "{}"),
        "issues": issues,
        "summary": _issue_summary(engine, load["load_id"]),
    }


def _issue_summary(engine: Engine, load_id: int) -> list[dict]:
    """The upload's problems per check (blocking first, then by count): what, where, how many rows, and the rows."""
    i = input_issue.c
    with engine.connect() as conn:
        rows = conn.execute(
            select(i.severity, i.code, i.sheet, i.column_name, i.source_row, i.message)
            .where(i.load_id == load_id)
            .order_by(i.issue_id)
        ).all()
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r.severity, r.code, r.sheet, r.column_name)
        g = groups.setdefault(
            key,
            {
                "severity": r.severity,
                "code": r.code,
                "sheet": r.sheet,
                "column": r.column_name,
                "title": _title(r.message),
                "count": 0,
                "rows": [],
            },
        )
        if r.source_row is None and "more row(s) not listed" in (r.message or ""):
            g["count"] += int(re.search(r"(\d+) more row", r.message).group(1))  # the rows past the list limit
            g["truncated"] = True
            continue
        g["count"] += 1
        g["rows"].append({"row": r.source_row, "message": r.message})
    return sorted(groups.values(), key=lambda g: (g["severity"] != "ERROR", -g["count"], g["code"]))


def _title(message: str | None) -> str:
    """'row 12: Gender is not a known label ('F' ...)' -> 'Gender is not a known label'."""
    text = re.sub(r"^row \d+: ", "", message or "")
    return re.sub(r" \(.*\)$", "", text, flags=re.S)


def _draft_rows(batch_id: str):
    s = svc()
    rows = s.requests.list_batch(batch_id)
    if not rows or rows[0].run_mode is not RunMode.VALIDATE or (s.tenant and rows[0].tenant_id != s.tenant):
        fail(f"No request {batch_id}", 404)
    return rows


@bp.get("/batches/<batch_id>/inputs")
def request_inputs(batch_id: str):
    s = svc()
    rows = _draft_rows(batch_id)
    loads = latest_loads(s.engine, batch_id)
    is_draft = all(r.status is RequestStatus.DRAFT for r in rows)
    return jsonify(
        batch_id=batch_id,
        is_draft=is_draft,
        inputs=[
            {
                "submission_type": r.submission_type.value,
                "needs_workbook": needs_workbook(r.submission_type.value),
                "latest": _load_json(s.engine, loads.get(r.submission_type.value)),
            }
            for r in rows
        ],
    )


@bp.post("/batches/<batch_id>/inputs/<submission_type>")
def upload_input(batch_id: str, submission_type: str):
    s = svc()
    request.max_content_length = MAX_UPLOAD  # this route only; the rest of the API keeps 1 MB
    rows = _draft_rows(batch_id)
    row = next((r for r in rows if r.submission_type.value == submission_type.upper()), None)
    if row is None:
        fail(f"Request {batch_id} has no {submission_type} type", 404)
    if row.status is not RequestStatus.DRAFT:
        fail(
            "This request was already submitted, so its input workbooks are locked. For other data, create a new "
            "request for the same date.",
            409,
        )
    if not needs_workbook(row.submission_type.value):
        fail(f"{row.submission_type.value} has no input workbook", 400)
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        fail("Choose the workbook (.xlsx) to upload.")
    file_name = upload.filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
    if not file_name.lower().endswith(".xlsx"):
        fail("Upload the workbook as .xlsx (Excel workbook).")
    data = upload.read()
    if not data:
        fail("The file is empty.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        uri = s.store.put(
            f"{row.tenant_id}/{batch_id}/inputs/{row.submission_type.value.lower()}-{stamp}.xlsx", data, XLSX
        )
    except Exception:
        log.exception("Storing the workbook for %s failed", batch_id)
        fail("The workbook could not be stored; try again.", 503)
    result = load_workbook(
        s.engine,
        row.submission_type.value,
        data,
        tenant_id=row.tenant_id,
        file_name=file_name,
        loaded_by=current_user(),
        batch_id=batch_id,
        file_uri=uri,
    )
    checks = None
    if result.status == "LOADED":  # data-quality checks on the staged rows, for this date
        checks = run_checks(s.engine, result.load_id, row.submission_type.value, row.as_of_date)
    audit.record(
        s.engine,
        current_user(),
        "INPUT_UPLOADED",
        tenant_id=row.tenant_id,
        target=batch_id,
        submission_type=row.submission_type.value,
        load_id=result.load_id,
        file_name=file_name,
        status=result.status,
        rows=result.row_counts,
        dq_blocking=checks.blocking if checks else None,
        dq_warnings=checks.warnings if checks else None,
    )
    latest = _load_json(s.engine, latest_loads(s.engine, batch_id)[row.submission_type.value])
    if result.status != "LOADED":
        return jsonify(
            message="The workbook was not loaded: " + "; ".join(i.message for i in result.errors), latest=latest
        ), 422
    if not checks.passed:
        return jsonify(
            message=f"{file_name} loaded, but {checks.blocking} row(s) fail blocking data-quality checks "
            "(see below). Fix the workbook and upload it again.",
            latest=latest,
        ), 422
    warnings = sum(i.severity == "WARN" for i in result.issues) + checks.warnings
    total = sum(result.row_counts.values())
    text = f"{file_name} loaded and checked: {total} rows from {len(result.row_counts)} sheet(s)."
    if warnings:
        text += f" {warnings} warning(s); see below."
    return jsonify(message=text, latest=latest), 201
