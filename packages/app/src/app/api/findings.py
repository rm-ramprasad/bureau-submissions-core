"""Validation findings, CSV export, and exclude / override from a finding into a new draft request. Routes on the API blueprint (/api/...)."""

from __future__ import annotations

import math

from flask import Response, jsonify, request
from sqlalchemy import select

from app import audit
from app.adjustments import (
    AdjustmentScope,
)
from app.api.auth import current_user
from app.api.common import (
    PAGE_SIZE,
    body,
    bp,
    fail,
    message,
    svc,
)
from app.enums import (
    AdjustmentKind,
    OverrideField,
    RunMode,
)
from app.requests import (
    DuplicateActiveRequestError,
    RequestValidationError,
)
from app.validation import results
from bureau_core.validation.rules import report_hints
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotNotFoundError


# ---------------------------------------------------------------- findings -> exceptions of a new draft request
def _draft_for(snap) -> str:
    """The open draft request for the snapshot's NBFC, date and type; created if there is none."""
    s = svc()
    draft = s.requests.open_draft(snap.tenant_id, snap.as_of_date, snap.submission_type)
    if draft:
        return draft
    source = s.requests.list_batch(snap.batch_id) if snap.batch_id else []
    cycle = source[0].reporting_cycle if source else None
    draft = s.requests.create_batch(
        snap.tenant_id, snap.as_of_date, RunMode.VALIDATE, [snap.submission_type], (), cycle, current_user(), draft=True
    )
    audit.record(
        s.engine,
        current_user(),
        "RUN_CREATED",
        tenant_id=snap.tenant_id,
        target=draft,
        as_of=snap.as_of_date,
        mode=str(RunMode.VALIDATE),
        types=[snap.submission_type.value],
        status="DRAFT",
        source=f"findings of snapshot {snap.snapshot_id}",
    )
    return draft


def _snapshot(snapshot_id: int):
    try:
        snap = svc().snapshots.get(snapshot_id)
    except SnapshotNotFoundError:
        fail(f"No snapshot {snapshot_id}", 404)
    if svc().tenant and snap.tenant_id != svc().tenant:
        fail(f"No snapshot {snapshot_id}", 404)
    return snap


def _finding_account(snapshot_id: int):
    """(snapshot, account_number) for the account_key posted from the Findings page."""
    snap = _snapshot(snapshot_id)
    account = ENTITY_TABLES["account"]
    with svc().engine.connect() as conn:
        number = conn.execute(
            select(account.c.account_number).where(
                account.c.snapshot_id == snapshot_id, account.c.account_key == (body().get("account_key") or "")
            )
        ).scalar()
    if not number:
        fail("That loan is not in this snapshot (it may have been purged).", 404)
    return snap, number


def _open_draft(snap) -> str:
    try:
        return _draft_for(snap)
    except (RequestValidationError, DuplicateActiveRequestError) as exc:
        fail(f"Cannot open a new request for this date: {exc}", 409)


@bp.post("/snapshots/<int:snapshot_id>/exclude")
def exclude_from_findings(snapshot_id: int):
    s = svc()
    snap, number = _finding_account(snapshot_id)
    draft = _open_draft(snap)
    (row,) = [r for r in s.requests.list_batch(draft) if r.submission_type is snap.submission_type]
    result = s.adjustments.add_exclusions(
        AdjustmentScope.of(row),
        AdjustmentKind.EXCLUDE_ACCOUNT,
        [number],
        f"From findings of snapshot #{snapshot_id}",
        current_user(),
    )
    if result.added:
        audit.record(
            s.engine,
            current_user(),
            "ADJUSTMENT_ADDED",
            tenant_id=snap.tenant_id,
            target=draft,
            kind=str(AdjustmentKind.EXCLUDE_ACCOUNT),
            keys=result.added,
            count=1,
            reason=f"From findings of snapshot #{snapshot_id}",
        )
    return message(
        f"Loan {number} {'added to' if result.added else 'is already in'} the exclusions of draft request "
        f"{draft}. Submit that request to validate without it.",
        batch_id=draft,
    )


@bp.post("/snapshots/<int:snapshot_id>/override")
def override_from_findings(snapshot_id: int):
    """Opens (or creates) the draft; the frontend then shows its Exceptions step with the override pre-filled."""
    snap, number = _finding_account(snapshot_id)
    draft = _open_draft(snap)
    return jsonify(batch_id=draft, type=snap.submission_type, account=number, field=body().get("field") or None)


def _filter(snap) -> results.FindingFilter:
    """Filter from the query string; ?category= is resolved to that category's rule IDs in this snapshot."""
    cat = request.args.get("category") or None
    hints = report_hints(snap.submission_type)
    if cat and cat not in hints.categories:
        fail(f"Unknown category {cat}")
    rule_ids = results.category_rule_ids(svc().engine, snap.snapshot_id, cat, hints) if cat else None
    return results.FindingFilter(
        rule_ids=rule_ids,
        **{k: request.args.get(k) or None for k in ("rule_id", "outcome", "account_key", "party_key")},
    )


@bp.get("/snapshots/<int:snapshot_id>/findings")
def findings(snapshot_id: int):
    s = svc()
    snap = _snapshot(snapshot_id)
    flt = _filter(snap)
    page = max(1, request.args.get("page", 1, type=int))
    rows, total = results.findings(s.engine, snapshot_id, flt, PAGE_SIZE, (page - 1) * PAGE_SIZE)
    return jsonify(
        snapshot=snap,
        report=results.report(s.engine, snapshot_id, report_hints(snap.submission_type)),
        records=results.record_counts(s.engine, snapshot_id),
        findings=rows,
        total=total,
        filter=flt,
        page=page,
        pages=max(1, math.ceil(total / PAGE_SIZE)),
        overridable=[f.value for f in OverrideField],
    )


@bp.get("/snapshots/<int:snapshot_id>/findings.csv")
def findings_csv(snapshot_id: int):
    """CSV export. On Azure it is written to the exports container (deleted after a day) and handed out as a
    short-lived link, which avoids the API's response size and time limits; locally it is streamed."""
    s = svc()
    snap = _snapshot(snapshot_id)
    name = f"findings-{snap.tenant_id}-{snap.as_of_date:%Y%m%d}-{snapshot_id}.csv"
    chunks = results.findings_csv(s.engine, snapshot_id, _filter(snap))
    audit.record(s.engine, current_user(), "FINDINGS_EXPORTED", tenant_id=snap.tenant_id, target=snapshot_id)
    if s.exports is None:
        return Response(chunks, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"'})
    return jsonify(url=s.exports.publish(name, "".join(chunks).encode(), "text/csv"), file_name=name)
