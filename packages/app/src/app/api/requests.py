"""A request: create (always a Validate draft), detail, generate from its validation, retry, cancel, downloads. Routes on the API blueprint (/api/...)."""

from __future__ import annotations

from datetime import date

from flask import Response, jsonify

from app import audit
from app.adjustments import (
    applied_summary,
)
from app.api.auth import current_user
from app.api.common import (
    DOWNLOADS,
    arg,
    body,
    bp,
    dispatch,
    fail,
    log,
    message,
    parse_date,
    row_json,
    svc,
)
from app.enums import (
    ReportingCycle,
    RequestStatus,
    RunMode,
    SnapshotStatus,
    SubmissionType,
)
from app.pipeline import available_types
from app.ports.files import StoredFileNotFound
from app.requests import (
    AlreadyGeneratedError,
    DuplicateActiveRequestError,
    InvalidTransitionError,
    RequestNotFoundError,
    RequestValidationError,
)
from app.requests.repository import LIVE_GENERATION
from app.validation import results
from bureau_core.validation.rules import report_hints
from canonical.snapshot import SnapshotNotFoundError
from canonical.tenants import InactiveTenantError, TenantNotFoundError


# ---------------------------------------------------------------- new request (always Validate, as a draft)
@bp.post("/requests")
def create_request():
    s, b = svc(), body()
    types = b.get("submission_types") or []
    try:
        tenant = s.tenants.get_active(s.tenant or b.get("tenant_id") or "")
    except TenantNotFoundError, InactiveTenantError:
        fail("Select an active NBFC.")
    as_of = parse_date(b.get("as_of_date"), "As-of date")
    try:  # DL / DC / AH / RR may be chosen (any date); else the date's
        chosen = ReportingCycle(b["reporting_cycle"]) if b.get("reporting_cycle") else None
    except ValueError:
        fail("Unknown reporting cycle")
    if chosen is not None and not chosen.manual:
        fail(f"{chosen} follows from the as-of date; choose only DL, DC, AH or RR, or leave the cycle empty.")
    cycle = chosen or ReportingCycle.for_date(as_of)
    if cycle is None and as_of <= date.today():
        fail(
            "The as-of date must be a reporting date: the 9th (W1), 16th (W2), 23rd (W3) or the last day of a month "
            "(or choose a DL, DC, AH or RR cycle)."
        )
    from app.configstore.settings import enabled_cycles

    if cycle is not None and cycle not in enabled_cycles(s.engine, tenant.tenant_id):
        fail(f"The {cycle} cycle is switched off for this NBFC (Admin page).")
    try:
        offered = available_types(s.engine)  # one look at the views, not one per type
        not_enabled = [t for t in types if t not in SubmissionType or SubmissionType(t) not in offered]
        if not_enabled:
            raise RequestValidationError(
                f"Not set up for this NBFC: {', '.join(not_enabled)} (its views automn.vw_<type>_* are not in the database)"
            )
        batch_id = s.requests.create_batch(
            tenant.tenant_id, as_of, RunMode.VALIDATE, types, (), cycle, current_user(), draft=True
        )
    except (RequestValidationError, DuplicateActiveRequestError) as exc:
        fail(str(exc))
    except ValueError:
        fail("Check the selections.")
    audit.record(
        s.engine,
        current_user(),
        "RUN_CREATED",
        tenant_id=tenant.tenant_id,
        target=batch_id,
        as_of=as_of,
        mode=str(RunMode.VALIDATE),
        types=types,
        status="DRAFT",
    )
    return message(
        f"Request {batch_id} created. Add any exclusions or overrides, then submit it for validation.",
        201,
        batch_id=batch_id,
    )


# ---------------------------------------------------------------- request (batch) detail
@bp.get("/batches/<batch_id>")
def batch(batch_id: str):
    s = svc()
    rows = s.requests.list_batch(batch_id)
    if not rows or (s.tenant and rows[0].tenant_id != s.tenant):
        fail(f"No request {batch_id}", 404)
    snapshots = []
    for snapshot_id in sorted({r.snapshot_id for r in rows if r.snapshot_id}):
        try:
            snap = s.snapshots.get(snapshot_id)
        except SnapshotNotFoundError:
            continue
        snapshots.append(
            {
                "snapshot": snap,
                "records": results.record_counts(s.engine, snapshot_id),
                "report": results.report(s.engine, snapshot_id, report_hints(snap.submission_type)),
                "adjusted": applied_summary(s.engine, snapshot_id),
            }
        )
    validated = {x["snapshot"].snapshot_id for x in snapshots if x["snapshot"].status is SnapshotStatus.VALIDATED}  # type: ignore[attr-defined]
    made = s.requests.generations({r.snapshot_id for r in rows if r.run_mode is RunMode.VALIDATE and r.snapshot_id})
    live = {g.snapshot_id for g in made if g.status in LIVE_GENERATION}
    generate_from = [
        {
            "request_id": r.request_id,
            "submission_type": r.submission_type,
            "snapshot_id": r.snapshot_id,
            "bureaus": [p.bureau_code for p in s.bureaus.list(r.tenant_id, r.submission_type)],
        }
        for r in rows
        if r.run_mode is RunMode.VALIDATE
        and r.status is RequestStatus.SUCCEEDED
        and r.snapshot_id in validated
        and r.snapshot_id not in live
    ]  # files once per validation
    parent = None  # a generation batch belongs to its validation's request
    if rows[0].run_mode is RunMode.GENERATE and rows[0].snapshot_id:
        parent = s.requests.validation_batch_of(rows[0].snapshot_id)
    tenant = s.tenants.get(rows[0].tenant_id)
    return jsonify(
        batch_id=batch_id,
        tenant={"tenant_id": tenant.tenant_id, "display_name": tenant.display_name},
        rows=[row_json(r) for r in rows],
        snapshots=snapshots,
        generate_from=generate_from,
        generations=[row_json(g) for g in made],
        parent_batch_id=parent,
        active=any(r.status in (RequestStatus.PENDING, RequestStatus.RUNNING) for r in rows + made),
    )


@bp.post("/batches/<batch_id>/generate")
def generate_from_validation(batch_id: str):
    s, b = svc(), body()
    try:
        source = s.requests.get(int(b["request_id"]))
    except KeyError, TypeError, ValueError, RequestNotFoundError:
        fail("Choose the validation to generate from")
    if source.batch_id != batch_id or source.run_mode is not RunMode.VALIDATE or source.snapshot_id is None:
        fail("That is not a validation of this request")
    bureaus = b.get("bureau_codes") or []
    adjusted = applied_summary(s.engine, source.snapshot_id)
    try:
        if (adjusted.excluded_accounts or adjusted.overridden_accounts) and not b.get("confirm_adjustments"):
            raise RequestValidationError("Confirm the excluded loans and overrides before generating.")
        if not bureaus:
            raise RequestValidationError("Select at least one bureau")
        active = {p.bureau_code.value for p in s.bureaus.list(source.tenant_id, source.submission_type)}
        missing = [f"{x} ({source.submission_type.value})" for x in bureaus if x not in active]
        if missing:
            raise RequestValidationError(f"No active bureau profile: {', '.join(missing)}")
        new_batch = s.requests.create_batch(
            source.tenant_id,
            source.as_of_date,
            RunMode.GENERATE,
            [source.submission_type],
            bureaus,
            source.reporting_cycle,
            current_user(),
            snapshot_id=source.snapshot_id,
        )
    except AlreadyGeneratedError as exc:
        fail(str(exc), 409)
    except (RequestValidationError, DuplicateActiveRequestError, ValueError) as exc:
        fail(str(exc))
    audit.record(
        s.engine,
        current_user(),
        "GENERATE_FROM_VALIDATION",
        tenant_id=source.tenant_id,
        target=new_batch,
        snapshot_id=source.snapshot_id,
        bureaus=bureaus,
    )
    if adjusted.excluded_accounts or adjusted.overridden_accounts:
        audit.record(
            s.engine,
            current_user(),
            "ADJUSTMENTS_CONFIRMED",
            tenant_id=source.tenant_id,
            target=new_batch,
            snapshot_id=source.snapshot_id,
            excluded_loans=adjusted.excluded_accounts,
            overridden_loans=adjusted.overridden_accounts,
            request_ids=[r.request_id for r in s.requests.list_batch(new_batch)],
        )
    dispatch(new_batch)
    return message("File generation queued from the validated snapshot.", 201, batch_id=new_batch)


def _row_action(request_id: int, action: str):
    s = svc()
    try:
        req = s.requests.get(request_id)
        getattr(s.requests, action)(request_id)
    except RequestNotFoundError:
        fail(f"No request {request_id}", 404)
    except (InvalidTransitionError, DuplicateActiveRequestError, AlreadyGeneratedError) as exc:
        fail(str(exc), 409)
    audit.record(
        s.engine,
        current_user(),
        "REQUEST_RETRIED" if action == "requeue" else "REQUEST_CANCELLED",
        tenant_id=req.tenant_id,
        target=request_id,
        batch_id=req.batch_id,
    )
    if action == "requeue":
        try:
            s.dispatcher.dispatch([s.requests.get(request_id)])
        except Exception:
            log.exception("Dispatch of request %s failed; recovery will retry it", request_id)
    return message(
        f"Request {request_id}: {'re-queued' if action == 'requeue' else 'cancelled'}.", batch_id=req.batch_id
    )


@bp.post("/requests/<int:request_id>/retry")
def retry(request_id: int):
    return _row_action(request_id, "requeue")


@bp.post("/requests/<int:request_id>/cancel")
def cancel(request_id: int):
    return _row_action(request_id, "cancel")


@bp.get("/requests/<int:request_id>/reconciliation")
def reconciliation_of(request_id: int):
    """A generated consumer file's reconciliation (bureau Excel vs TUDF), as stored (issue #39)."""
    from app import reconciliation

    s = svc()
    try:
        req = s.requests.get(request_id)
    except RequestNotFoundError:
        fail("Request not found", 404)
    if s.tenant and req.tenant_id != s.tenant:
        fail("Request not found", 404)
    stored = reconciliation.load(s.engine, request_id)
    if stored is None:
        fail("No reconciliation for this request (only generated consumer files have one)", 404)
    return jsonify(stored)


@bp.get("/requests/<int:request_id>/<kind>")
def download(request_id: int, kind: str):
    """Audit the download, then hand out a short-lived link ({"url": ...}). A locally stored file (development)
    is streamed instead. Kinds: file (TUDF), excel (bureau Excel), review (review Excel: also for a failed generation,
    whose reconciliation did not match), exceptions (older requests)."""
    s = svc()
    if kind not in DOWNLOADS:
        fail("Unknown download", 404)
    try:
        req = s.requests.get(request_id)
    except RequestNotFoundError:
        fail(f"No request {request_id}", 404)
    uri = getattr(req, DOWNLOADS[kind])
    allowed = (RequestStatus.SUCCEEDED, RequestStatus.FAILED) if kind == "review" else (RequestStatus.SUCCEEDED,)
    if req.status not in allowed or not uri:
        fail(
            f"Request {request_id} has no {kind} to download (status {req.status.value}). "
            "It may not have finished, or retention has removed the file.",
            404,
        )
    if not s.store.owns(uri):
        log.warning("Download refused for request %s: %s is outside the configured store", request_id, uri)
        fail(
            f"The file for request {request_id} was saved at {uri}, which is not in the storage this app "
            f"is configured for ({s.store.describe()}). Check that the API and the workers use the same "
            "SUBMISSIONS_STORAGE / bucket / prefix settings, or regenerate the file.",
            404,
        )
    name = (
        req.output_file_name if kind == "file" and req.output_file_name else uri.replace("\\", "/").rsplit("/", 1)[-1]
    )
    mimetype = (
        "text/csv"
        if name.endswith(".csv")
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        if name.endswith(".xlsx")
        else "text/plain"
    )
    if kind == "file" and arg("view"):  # the TUDF viewer: the stored bytes as they are, for reading on screen
        try:
            content = b"".join(s.store.read_chunks(uri))
        except StoredFileNotFound:
            fail(f"The file {uri} no longer exists in storage (deleted or purged).", 404)
        audit.record(
            s.engine,
            current_user(),
            "FILE_VIEWED",
            tenant_id=req.tenant_id,
            target=request_id,
            kind=kind,
            file=name,
            location=uri,
        )
        return Response(content, mimetype="application/octet-stream", headers={"Cache-Control": "no-store"})
    try:
        url = s.store.download_url(uri, name, mimetype)
    except StoredFileNotFound:
        fail(f"The file {uri} no longer exists in storage (deleted or purged). Regenerate it from the validation.", 404)
    audit.record(
        s.engine,
        current_user(),
        "FILE_DOWNLOADED",
        tenant_id=req.tenant_id,
        target=request_id,
        kind=kind,
        file=name,
        location=uri,
    )
    if url:
        return jsonify(url=url, file_name=name)
    return Response(
        s.store.read_chunks(uri), mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )
