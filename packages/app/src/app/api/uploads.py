"""Uploads page (ADR 0002 PR 7; owner 2026-11-10: a separate Uploads page): a workbook uploaded once, not inside a
request. It is kept and recorded at once, then loaded into staging, checked (the type's input_checks.yaml) and, when
nothing blocks and the type is on the customer tables (Admin page, data source), saved in them, in the background
(app.uploadjobs; owner 2026-11-12: progress on screen).
Requests of such a type then need no workbook of their own; types on the source views keep the in-request upload.

Routes on the API blueprint: GET /api/uploads, GET /api/uploads/<load_id>, POST /api/uploads/<submission_type>.
"""

from __future__ import annotations

import logging
from datetime import date

from flask import jsonify, request
from sqlalchemy import func, select

from app.api.auth import current_user
from app.api.common import bp, fail, svc
from app.api.inputs import ISSUES_SHOWN, MAX_UPLOAD, XLSX, _load_json, needs_workbook
from app.configstore.settings import data_source
from app.enums import SubmissionType
from app.uploadjobs import DONE, INTERRUPTED, interrupted, process_upload, record_upload
from canonical.staging.tables import input_issue, input_load, input_save_item
from canonical.tenants import InactiveTenantError, TenantNotFoundError

log = logging.getLogger(__name__)
PAGE = 50


def _tenant(given: str | None) -> str:
    s = svc()
    try:
        return s.tenants.get_active(s.tenant or given or "").tenant_id
    except TenantNotFoundError, InactiveTenantError:
        fail("Select an active NBFC.")
    raise AssertionError  # fail() raises


DETAIL_ROWS = 20000  # issues / items sent to the details page (and its CSV)


def _save_items(engine, load_id: int, limit: int | None = None) -> list[dict]:
    c = input_save_item.c
    q = select(c.code, c.sanction_reference_number, c.account_number, c.message).where(c.load_id == load_id)
    q = q.order_by(c.item_id)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(q.limit(limit) if limit else q)]


def _row_json(engine, r) -> dict:
    base = _load_json(engine, dict(r._mapping)) or {}
    groups = base.get("summary") or []
    with engine.connect() as conn:
        not_saved = conn.execute(select(func.count()).select_from(input_save_item).where(
            input_save_item.c.load_id == r.load_id, input_save_item.c.code.in_(("NO_BORROWER", "SAVE_FAILED")))
        ).scalar_one()  # fmt: skip
    source = data_source(engine, r.tenant_id, r.submission_type)
    summary, stage = r.apply_summary, r.stage
    if interrupted(r):
        summary, stage = summary or INTERRUPTED, DONE  # the processing stopped (server restart, time limit)
    return base | {
        "submission_type": r.submission_type,
        "as_of_date": r.as_of_date,
        "applied_at": r.applied_at,
        "apply_summary": summary,
        "source": source,
        "stage": stage or DONE,  # uploads before migration 021 were processed in the request: done
        "stage_at": r.stage_at,
        "dq_errors": sum(g["count"] for g in groups if g["severity"] == "ERROR"),
        "dq_warnings": sum(g["count"] for g in groups if g["severity"] != "ERROR"),
        "not_saved": not_saved,
    }


@bp.get("/uploads")
def list_uploads():
    """The NBFC's uploads made on this page (newest first), with their checks and what they changed."""
    s = svc()
    tenant_id = _tenant(request.args.get("tenant_id"))
    t = input_load.c
    query = select(input_load).where(t.tenant_id == tenant_id, t.batch_id.is_(None))
    kind = (request.args.get("type") or "").upper()
    if kind:
        query = query.where(t.submission_type == kind)
    with s.engine.connect() as conn:
        rows = conn.execute(query.order_by(t.loaded_at.desc(), t.load_id.desc()).limit(PAGE)).all()
    types = [
        {"submission_type": k, "source": data_source(s.engine, tenant_id, k)}
        for k in (str(x.value) for x in SubmissionType)
        if needs_workbook(k)
    ]
    return jsonify(tenant_id=tenant_id, types=types, uploads=[_row_json(s.engine, r) for r in rows],
                   issues_shown=ISSUES_SHOWN)  # fmt: skip


@bp.get("/uploads/<int:load_id>")
def upload_status(load_id: int):
    """One upload, for the page to follow its progress."""
    s = svc()
    tenant_id = _tenant(request.args.get("tenant_id"))
    with s.engine.connect() as conn:
        row = conn.execute(select(input_load).where(input_load.c.load_id == load_id,
                                                     input_load.c.tenant_id == tenant_id)).one_or_none()  # fmt: skip
    if row is None:
        fail(f"No upload {load_id}", 404)
    out = _row_json(s.engine, row)
    if request.args.get("details"):  # the upload's details page: every issue and save item (CSV made in the page)
        i = input_issue.c
        with s.engine.connect() as conn:
            issues = [dict(x._mapping) for x in conn.execute(
                select(i.severity, i.code, i.sheet, i.source_row, i.column_name, i.message)
                .where(i.load_id == load_id).order_by(i.issue_id).limit(DETAIL_ROWS))]  # fmt: skip
        out |= {"all_issues": issues, "save_items": _save_items(s.engine, load_id, DETAIL_ROWS),
                "detail_limit": DETAIL_ROWS}  # fmt: skip
    return jsonify(upload=out)


@bp.post("/uploads/<submission_type>")
def upload(submission_type: str):
    """Upload one workbook: {file, as_of_date, tenant_id}. Kept and recorded at once (202); loading, checks and saving
    into the customer tables run in the background (app.uploadjobs), the page follows GET /uploads/<load_id>."""
    s = svc()
    request.max_content_length = MAX_UPLOAD  # this route only
    kind = submission_type.upper()
    if not needs_workbook(kind):
        fail(f"{kind} has no input workbook", 404)
    tenant_id = _tenant(request.form.get("tenant_id"))
    try:
        as_of = date.fromisoformat(request.form.get("as_of_date") or "")
    except ValueError:
        fail("Give the as-of date of the data (YYYY-MM-DD).")
    if as_of > date.today():
        fail("The as-of date cannot be in the future.")
    upload_file = request.files.get("file")
    if upload_file is None or not upload_file.filename:
        fail("Choose the workbook (.xlsx) to upload.")
    file_name = upload_file.filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
    if not file_name.lower().endswith(".xlsx"):
        fail("Upload the workbook as .xlsx (Excel workbook).")
    data = upload_file.read()
    if not data:
        fail("The file is empty.")
    try:
        load_id = record_upload(s.engine, s.store, tenant_id=tenant_id, submission_type=kind, data=data,
                                file_name=file_name, loaded_by=current_user(), as_of_date=as_of, content_type=XLSX)  # fmt: skip
    except Exception:
        log.exception("Storing the workbook failed")
        fail("The workbook could not be stored; try again.", 503)
    dispatch = getattr(s.dispatcher, "dispatch_upload", None)
    queued = False
    if dispatch is not None:
        try:
            dispatch(load_id)
            queued = True
        except Exception:  # no queue: process it now (slower answer, same result)
            log.exception("Queueing upload %s failed; processing it in the request", load_id)
    if queued:
        message = f"{file_name} received; it is being loaded and checked (follow it in the list)."
    else:  # local runs and tests: no queue, processed now
        message = process_upload(s.engine, s.store, load_id).message
    with s.engine.connect() as conn:
        row = conn.execute(select(input_load).where(input_load.c.load_id == load_id)).one()
    return jsonify(message=message, upload=_row_json(s.engine, row)), 202
