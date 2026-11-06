"""Session, metadata and health. Routes on the API blueprint (/api/...)."""

from __future__ import annotations

from datetime import date

from flask import g, jsonify, request

from app import audit
from app.api.auth import current_user
from app.api.common import (
    CYCLE_LABELS,
    QUEUE_SLOW,
    bp,
    last_month_end,
    message,
    options,
    svc,
)
from app.enums import (
    BatchStatus,
    BureauCode,
    OverrideField,
    ReportingCycle,
    RunMode,
    SubmissionType,
)
from app.pipeline import available_types, enabled_types
from app.requests.stages import Period, Stage
from app.users import UserRepository
from app.validation import results
from canonical.db import utcnow


# ---------------------------------------------------------------- session, metadata, health
@bp.get("/me")
def me():
    i = g.identity
    user = UserRepository(svc().engine).find(i.username) if i.email else None
    return jsonify(
        username=i.username,
        display_name=i.display_name,
        email=i.email,
        is_admin=i.is_admin,
        last_login_at=user.last_login_at if user else None,
    )  # the previous sign-in, for the welcome


@bp.post("/session")
def session_start():
    """Called by the frontend right after sign-in: records the login."""
    if g.identity.email:
        UserRepository(svc().engine).record_login(g.identity.username)
    audit.record(svc().engine, current_user(), "LOGIN", target=current_user(), ip=request.remote_addr)
    return message("Signed in.")


@bp.post("/session/end")
def session_end():
    audit.record(svc().engine, current_user(), "LOGOUT", target=current_user())
    return message("Signed out.")


def type_list(offered: list[SubmissionType]) -> list[dict]:
    """The registered types for the pages (descriptor order): wording and flags from each type.yaml."""
    from bureau_core.types import descriptor

    out = []
    for t in SubmissionType:
        d = descriptor(t)
        out.append(
            {
                "value": t.value,
                "label": d.label,
                "enabled": t in offered,
                "engine": d.engine,
                "file_label": d.file_label,
                "record_noun": d.record_noun,
                "header_tag_required": d.header_tag_required,
                "planned": False,
            }
        )
    from bureau_core.types import planned_types

    out += [{"value": d.submission_type, "label": d.label, "enabled": False, "planned": True} for d in planned_types()]
    return out


@bp.get("/types")
def types():
    """The registered submission types (enabled: the client can run it, i.e. its source views exist)."""
    s = svc()
    return jsonify(types=type_list(enabled_types() if not s.tenant else available_types(s.engine)))


@bp.get("/meta")
def meta():
    """Choices the pages need: NBFCs, submission types, bureaus, cycles, stages, periods, outcomes, fields."""
    s = svc()
    offered = enabled_types() if not s.tenant else available_types(s.engine)
    dates = ReportingCycle.dates(date.today())  # as-of dates a request may use, newest first
    return jsonify(
        tenant=s.tenant,
        tenants=[
            {"tenant_id": t.tenant_id, "display_name": t.display_name, "is_active": t.is_active}
            for t in s.tenants.list(active_only=False)
            if not s.tenant or t.tenant_id == s.tenant
        ],
        submission_types=type_list(offered),
        bureaus=options(BureauCode),
        cycles=[{"value": c.value, "label": CYCLE_LABELS[c]} for c in ReportingCycle if c.manual],
        stages=options(Stage),
        periods=options(Period),
        statuses=options(BatchStatus),
        modes=options(RunMode),
        outcomes=results.OUTCOME_ORDER,
        override_fields=options(OverrideField),
        default_as_of=dates[0] if dates else last_month_end(),
        as_of_dates=[
            {
                "value": d,
                "cycle": ReportingCycle.for_date(d),
                "label": f"{d:%d-%m-%Y} · {CYCLE_LABELS.get(ReportingCycle.for_date(d))}",
            }
            for d in dates
        ],
    )


@bp.get("/health")
def health():
    """Queue health: how long the oldest PENDING request has waited (replaces the worker heartbeat banner)."""
    oldest = svc().requests.oldest_pending()
    waited = utcnow() - oldest if oldest else None
    return jsonify(oldest_pending_at=oldest, waiting_seconds=waited, slow=bool(waited and waited > QUEUE_SLOW))
