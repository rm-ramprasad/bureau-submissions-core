"""Uploads page (ADR 0002 PR 7; owner 2026-11-10: a separate Uploads page): a workbook uploaded once, not inside a
request. It is stored, loaded into staging and checked (the type's input_checks.yaml, for the data's as-of date), and,
when nothing blocks and the type is on the customer tables (Admin page, data source), loaded into them at once.
Requests of such a type then need no workbook of their own; types on the source views keep the in-request upload.

Routes on the API blueprint: GET /api/uploads, POST /api/uploads/<submission_type>.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from flask import jsonify, request
from sqlalchemy import select, update

from app import audit
from app.api.auth import current_user
from app.api.common import bp, fail, svc
from app.api.inputs import ISSUES_SHOWN, MAX_UPLOAD, XLSX, _load_json, needs_workbook
from app.configstore.settings import data_source
from app.enums import SubmissionType
from canonical.db import utcnow
from canonical.staging.checks import run_checks
from canonical.staging.tables import input_load
from canonical.tenants import InactiveTenantError, TenantNotFoundError
from sources.excel import load_workbook

log = logging.getLogger(__name__)
PAGE = 50


def _tenant(given: str | None) -> str:
    s = svc()
    try:
        return s.tenants.get_active(s.tenant or given or "").tenant_id
    except TenantNotFoundError, InactiveTenantError:
        fail("Select an active NBFC.")
    raise AssertionError  # fail() raises


def _row_json(engine, r) -> dict:
    base = _load_json(engine, dict(r._mapping)) or {}
    return base | {
        "submission_type": r.submission_type,
        "as_of_date": r.as_of_date,
        "applied_at": r.applied_at,
        "apply_summary": r.apply_summary,
        "source": data_source(engine, r.tenant_id, r.submission_type),
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


@bp.post("/uploads/<submission_type>")
def upload(submission_type: str):
    """Upload one workbook: {file, as_of_date, tenant_id}. Stored; loaded into staging; checked for the as-of date;
    loaded into the customer tables when nothing blocks and the type is on them."""
    from canonical.master.apply import UploadConflict
    from sources.master_load import load_upload

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
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    try:
        uri = s.store.put(f"{tenant_id}/uploads/{kind.lower()}-{as_of:%Y%m%d}-{stamp}.xlsx", data, XLSX)
    except Exception:
        log.exception("Storing the workbook failed")
        fail("The workbook could not be stored; try again.", 503)
    result = load_workbook(s.engine, kind, data, tenant_id=tenant_id, file_name=file_name, loaded_by=current_user(),
                           batch_id=None, file_uri=uri, as_of_date=as_of)  # fmt: skip
    t = input_load.c
    checks = run_checks(s.engine, result.load_id, kind, as_of) if result.status == "LOADED" else None
    source = data_source(s.engine, tenant_id, kind)
    applied, summary, status = None, None, 201
    if result.status != "LOADED":
        text = "The workbook was not loaded: " + "; ".join(i.message for i in result.errors)
        status = 422
    elif checks is not None and not checks.passed:
        text = (f"{file_name} loaded, but {checks.blocking} row(s) fail blocking data-quality checks (see below). "
                "Fix the workbook and upload it again.")  # fmt: skip
        status = 422
    elif source == "master":
        try:
            res = load_upload(s.engine, s.engine, tenant_id, kind, as_of, result.load_id)  # views in the app database
        except UploadConflict as exc:
            text, status = f"{file_name} passed the checks but was not loaded into the customer tables: {exc}", 422
        else:
            applied = utcnow()
            summary = (f"customers {res.parties_added} added, {res.parties_updated} updated; accounts "
                       f"{res.sanctions_added} added, {res.sanctions_updated} updated; {res.changes} values changed"
                       + (f". {' '.join(res.warnings)}" if res.warnings else ""))  # fmt: skip
            with s.engine.begin() as conn:
                conn.execute(update(input_load).where(t.load_id == result.load_id)
                             .values(applied_at=applied, apply_summary=summary[:1000]))  # fmt: skip
            text = f"{file_name} loaded, checked and saved in the customer tables: {summary}."
    else:
        text = (f"{file_name} loaded and checked. {kind.title()} is on the source views (Admin page, data source): "
                "attach the workbook to a request as before.")  # fmt: skip
    audit.record(
        s.engine, current_user(), "DATA_UPLOADED", tenant_id=tenant_id, target=result.load_id, submission_type=kind,
        as_of_date=as_of.isoformat(), file_name=file_name, status=result.status, rows=result.row_counts,
        dq_blocking=checks.blocking if checks else None, dq_warnings=checks.warnings if checks else None,
        applied=bool(applied),
    )  # fmt: skip
    with s.engine.connect() as conn:
        row = conn.execute(select(input_load).where(t.load_id == result.load_id)).one()
    return jsonify(message=text, upload=_row_json(s.engine, row)), status
