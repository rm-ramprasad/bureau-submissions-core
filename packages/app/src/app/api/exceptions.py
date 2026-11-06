"""Exceptions (exclusions and overrides) of a draft request: step 2, then submit or discard. Routes on the API blueprint (/api/...)."""

from __future__ import annotations

from typing import Any

from flask import jsonify, request

from app import audit
from app.adjustments import (
    AdjustmentError,
    AdjustmentNotFoundError,
    AdjustmentScope,
    parse_keys,
    parse_override_value,
)
from app.api.auth import current_user
from app.api.common import (
    arg,
    batch_json,
    body,
    bp,
    dispatch,
    fail,
    message,
    row_json,
    selected_tenant,
    svc,
)
from app.enums import (
    AdjustmentKind,
    BatchStatus,
    OverrideField,
    RequestStatus,
    RunMode,
)
from app.requests import (
    InvalidTransitionError,
)


# ---------------------------------------------------------------- exceptions: step 2 of a request
def _request_row(batch_id: str):
    """The request's row for the chosen submission type (?type= / body type; default the first)."""
    rows = svc().requests.list_batch(batch_id)
    if not rows or rows[0].run_mode is not RunMode.VALIDATE:
        fail(f"No request {batch_id}", 404)
    wanted = arg("type")
    return rows, next((r for r in rows if r.submission_type.value == wanted), rows[0])


def _require_draft(row) -> None:
    if row.status is not RequestStatus.DRAFT:
        fail(
            "This request was already submitted, so its exceptions are locked. For more exceptions, create a new "
            "request for the same date.",
            409,
        )


@bp.get("/exceptions")
def exceptions():
    """Requests waiting for their exceptions (drafts), for the selected NBFC."""
    drafts = [
        b for b in svc().requests.list_batches(limit=100, tenant_id=selected_tenant()) if b.status is BatchStatus.DRAFT
    ]
    return jsonify(drafts=[batch_json(b) for b in drafts])


@bp.get("/batches/<batch_id>/exceptions")
def request_exceptions(batch_id: str):
    s = svc()
    rows, row = _request_row(batch_id)
    history = bool(request.args.get("history"))
    frozen: dict[int, int] = {}
    effects: dict[int, list[Any]] = {}
    if row.snapshot_id:
        frozen = s.adjustments.frozen(row.snapshot_id)
        for e in s.adjustments.applied(row.snapshot_id):  # type: ignore[attr-defined]
            effects.setdefault(e.adjustment_id, []).append(e)
    is_draft = row.status is RequestStatus.DRAFT
    items = []
    for a in s.adjustments.list(AdjustmentScope.of(row), include_removed=history):
        hits = effects.get(a.adjustment_id, [])
        if not a.is_active:
            status = "REMOVED"
        elif is_draft:
            status = "WILL_APPLY"
        elif a.adjustment_id not in frozen:
            status = "WAITING"
        else:
            status = "NOT_FOUND" if frozen[a.adjustment_id] == 0 else "APPLIED"
        old = next((h.old_value for h in hits if h.action == "OVERRIDE"), None)
        items.append(
            {
                **{
                    k: getattr(a, k)
                    for k in (
                        "adjustment_id",
                        "kind",
                        "field",
                        "override_value",
                        "reason",
                        "created_by",
                        "created_at",
                        "removed_by",
                        "removed_at",
                    )
                },
                "key": a.key,
                "field_label": a.field.label if a.field else None,
                "is_active": a.is_active,
                "status": status,
                "loans_affected": frozen.get(a.adjustment_id),
                "old_value": old,
                "excluded_loans": sum(h.action == "EXCLUDE" for h in hits),
            }
        )
    tenant = s.tenants.get(row.tenant_id)
    return jsonify(
        batch_id=batch_id,
        tenant={"tenant_id": tenant.tenant_id, "display_name": tenant.display_name},
        rows=[row_json(r) for r in rows],
        row=row_json(row),
        is_draft=is_draft,
        history=history,
        adjustments=items,
    )


@bp.post("/batches/<batch_id>/exceptions/exclude")
def add_exclusions(batch_id: str):
    s, b = svc(), body()
    _, row = _request_row(batch_id)
    _require_draft(row)
    kind = AdjustmentKind.EXCLUDE_CUSTOMER if b.get("kind") == "customer" else AdjustmentKind.EXCLUDE_ACCOUNT
    try:
        result = s.adjustments.add_exclusions(
            AdjustmentScope.of(row), kind, parse_keys(b.get("keys") or ""), b.get("reason"), current_user()
        )
    except AdjustmentError as exc:
        fail(str(exc))
    if result.added:
        audit.record(
            s.engine,
            current_user(),
            "ADJUSTMENT_ADDED",
            tenant_id=row.tenant_id,
            target=batch_id,
            kind=str(kind),
            keys=result.added[:200],
            count=len(result.added),
            reason=b.get("reason") or None,
        )
    what = "customer(s)" if kind is AdjustmentKind.EXCLUDE_CUSTOMER else "loan(s)"
    text = f"{len(result.added)} {what} added to the exclusions."
    if result.already_present:
        text += f" Already excluded: {', '.join(result.already_present[:10])}."
    return message(text, added=result.added, already_present=result.already_present)


@bp.post("/batches/<batch_id>/exceptions/override")
def add_override(batch_id: str):
    s, b = svc(), body()
    _, row = _request_row(batch_id)
    _require_draft(row)
    try:
        field = OverrideField(b.get("field") or "")
    except ValueError:
        fail("Choose the field to override.")
    account = (b.get("account_number") or "").strip()
    try:
        value = parse_override_value(field, str(b.get("value") or ""))
        new_id, replaced = s.adjustments.add_override(
            AdjustmentScope.of(row), account, field, value, b.get("reason"), current_user()
        )
    except AdjustmentError as exc:
        fail(str(exc))
    audit.record(
        s.engine,
        current_user(),
        "ADJUSTMENT_ADDED",
        tenant_id=row.tenant_id,
        target=batch_id,
        kind=str(AdjustmentKind.OVERRIDE),
        adjustment_id=new_id,
        account_number=account,
        field=field.value,
        value=str(value),
        replaced=replaced,
        reason=b.get("reason") or None,
    )
    return message(
        f"Override saved: {account} {field.label} = {value}{' (replaces the previous one)' if replaced else ''}.",
        adjustment_id=new_id,
    )


@bp.post("/batches/<batch_id>/exceptions/<int:adjustment_id>/remove")
def remove_adjustment(batch_id: str, adjustment_id: int):
    s = svc()
    _, row = _request_row(batch_id)
    try:
        adj = s.adjustments.get(adjustment_id)
    except AdjustmentNotFoundError:
        fail("No such exception", 404)
    if adj.batch_id != batch_id:
        fail("No such exception", 404)
    _require_draft(row)
    try:
        s.adjustments.remove(adjustment_id, current_user())
    except AdjustmentError as exc:
        fail(str(exc))
    audit.record(
        s.engine,
        current_user(),
        "ADJUSTMENT_REMOVED",
        tenant_id=adj.tenant_id,
        target=batch_id,
        adjustment_id=adjustment_id,
        kind=str(adj.kind),
        key=adj.key,
        field=adj.field.value if adj.field else None,
    )
    return message(f"Removed: {adj.kind.value.replace('_', ' ').lower()} {adj.key}.")


@bp.post("/batches/<batch_id>/submit")
def submit_request(batch_id: str):
    """Step 3: lock the exceptions and input workbooks and queue the validation (DRAFT -> PENDING)."""
    from app.api.inputs import missing_workbooks

    s = svc()
    rows, _ = _request_row(batch_id)
    missing = s.require_inputs and missing_workbooks(
        s.engine, batch_id, [r.submission_type.value for r in rows if r.status is RequestStatus.DRAFT]
    )
    if missing:
        fail(
            f"Upload an input workbook that loads and passes the blocking data-quality checks first: "
            f"{', '.join(t.title() for t in missing)}.",
            409,
        )
    try:
        s.requests.submit_draft(batch_id)
    except InvalidTransitionError as exc:
        fail(str(exc), 409)
    counts = {r.submission_type.value: len(s.adjustments.list(AdjustmentScope.of(r))) for r in rows}
    audit.record(
        s.engine, current_user(), "RUN_SUBMITTED", tenant_id=rows[0].tenant_id, target=batch_id, adjustments=counts
    )
    dispatch(batch_id)
    return message(
        "Submitted for validation. Excluded records are left out of the extract; validation runs on the "
        "rest. This page refreshes until it finishes."
    )


@bp.post("/batches/<batch_id>/discard")
def discard_request(batch_id: str):
    """Cancel a draft request (its exceptions stay in the history)."""
    s = svc()
    rows, _ = _request_row(batch_id)
    drafts = [r for r in rows if r.status is RequestStatus.DRAFT]
    if not drafts:
        fail("Only a draft request can be discarded.", 409)
    for r in drafts:
        s.requests.cancel(r.request_id)
    audit.record(
        s.engine,
        current_user(),
        "REQUEST_CANCELLED",
        tenant_id=rows[0].tenant_id,
        target=batch_id,
        batch_id=batch_id,
        draft=True,
    )
    return message(f"Draft request {batch_id} discarded.")
