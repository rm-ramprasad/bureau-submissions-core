"""Dashboard, tracker and the submitted-files list. Routes on the API blueprint (/api/...)."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from flask import jsonify, request

from app.api.common import (
    TRACKER_PAGE,
    bp,
    fail,
    request_json,
    row_json,
    selected_tenant,
    svc,
)
from app.enums import (
    BureauCode,
    SubmissionType,
)
from app.pipeline import available_types, enabled_types
from app.requests import (
    stages,
)
from app.requests.stages import Period, Stage
from canonical.db import utcnow


# ---------------------------------------------------------------- dashboard / tracker / files
def _period() -> Period:
    try:
        return Period(request.args.get("period") or Period.THIS_MONTH.value)
    except ValueError:
        return Period.THIS_MONTH


def _offered_types() -> list[SubmissionType]:
    """Types the selected NBFC can run (its mapping files), or every enabled type across all NBFCs."""
    s = svc()
    return available_types(s.engine) if s.tenant else enabled_types()


def _dashboard_type() -> SubmissionType:
    """The dashboard's submission type (?type=): one type at a time, record counts of types do not add up."""
    offered = _offered_types()
    try:
        chosen = SubmissionType(request.args.get("type")) if request.args.get("type") else None
    except ValueError:
        fail("Unknown submission type")
    return chosen if chosen in offered else (offered[0] if offered else SubmissionType.CONSUMER)


@bp.get("/dashboard")
def dashboard():
    s, now = svc(), utcnow()
    period, tenant_id, sub_type = _period(), selected_tenant(), _dashboard_type()
    board = stages.build_dashboard(s.engine, tenant_id, period, now, sub_type)
    types = [
        {
            "value": t,
            "label": t.value.title(),
            "attention": len(board.attention)
            if t is sub_type
            else len(stages.build_dashboard(s.engine, tenant_id, period, now, t).attention),
        }
        for t in _offered_types()
    ]
    nbfc_status = None
    if not tenant_id:  # all NBFCs: who has (not) submitted
        active = s.tenants.list()
        latest = stages.latest_per_tenant(s.engine, [x.tenant_id for x in active])
        nbfc_status = [
            {
                "tenant_id": x.tenant_id,
                "display_name": x.display_name,
                "latest": request_json(latest[x.tenant_id], now) if x.tenant_id in latest else None,
            }
            for x in active
        ]
    return jsonify(
        period=period,
        type=sub_type,
        types=types,
        raised=board.raised,
        stages=[
            {"value": st, "label": st.label, "count": board.count(st), "oldest_age": board.oldest_age(st)}
            for st in Stage
        ],
        completion_rate=board.completion_rate,
        acceptance_rate=board.acceptance_rate,
        records=board.total("records"),
        accepted=board.total("accepted"),
        excluded=board.total("excluded"),
        overridden=board.total("overridden"),
        files_generated=board.files_generated,
        records_submitted=board.records_submitted,
        attention=[{"reason": a.reason, **request_json(a.request, now)} for a in board.attention],
        bureaus=board.bureaus,
        recent=[request_json(r, now) for r in board.requests[:7]],
        nbfc_status=nbfc_status,
    )


@bp.get("/attention")
def attention():
    """The notification bell on every page: what needs attention in the last 30 days, every offered type."""
    s, now = svc(), utcnow()
    items = []
    for t in _offered_types():
        board = stages.build_dashboard(s.engine, selected_tenant(), Period.LAST_30, now, t)
        items += [{"reason": a.reason, "type": t, **request_json(a.request, now)} for a in board.attention]
    items.sort(key=lambda x: (x["stage"] != Stage.FAILED, x["created_at"]), reverse=False)
    return jsonify(count=len(items), items=items)


@bp.get("/batches")
def batches():
    """The tracker: requests (a validation and the files generated from it), filtered and paged, newest first.
    Filters: stage, type, bureau (files generated for it), period, or from/to (created, YYYY-MM-DD)."""
    s, now = svc(), utcnow()
    flt = {k: request.args.get(k) or "" for k in ("stage", "type", "bureau", "from", "to")}
    try:
        stage = Stage(flt["stage"]) if flt["stage"] else None
    except ValueError:
        fail(f"Unknown stage {flt['stage']}")
    try:
        sub_type = SubmissionType(flt["type"]) if flt["type"] else None
        bureau = BureauCode(flt["bureau"]) if flt["bureau"] else None
        start = datetime.combine(date.fromisoformat(flt["from"]), time()) if flt["from"] else None
        end = datetime.combine(date.fromisoformat(flt["to"]) + timedelta(days=1), time()) if flt["to"] else None
    except ValueError:
        fail("Check the filters (dates as YYYY-MM-DD)")
    if start is None and request.args.get("period"):
        start = _period().start(now)
    found = stages.load_requests(s.engine, selected_tenant(), start, sub_type)
    found = [
        r
        for r in found
        if (stage is None or r.stage is stage)
        and (end is None or r.created_at < end)
        and (bureau is None or any(f.bureau_code is bureau for f in r.files))
    ]
    page = max(1, request.args.get("page", 1, type=int))
    return jsonify(
        total=len(found),
        page=page,
        pages=max(1, math.ceil(len(found) / TRACKER_PAGE)),
        requests=[request_json(r, now) for r in found[(page - 1) * TRACKER_PAGE : page * TRACKER_PAGE]],
    )


@bp.get("/files")
def files():
    s = svc()
    try:
        bureau = BureauCode(request.args["bureau"]) if request.args.get("bureau") else None
    except ValueError:
        fail("Unknown bureau")
    period = _period() if request.args.get("period") else Period.ALL
    page = max(1, request.args.get("page", 1, type=int))
    rows, total = s.requests.list_files(
        selected_tenant(), bureau, period.start(utcnow()), TRACKER_PAGE, (page - 1) * TRACKER_PAGE
    )
    return jsonify(
        total=total,
        page=page,
        pages=max(1, math.ceil(total / TRACKER_PAGE)),
        location=s.store.describe(),
        files=[row_json(r) for r in rows],
    )
