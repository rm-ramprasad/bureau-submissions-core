"""Admin page, part 1 (step 6): which bureaus are on per submission type, which reporting cycles new requests may
use. Routes on the API blueprint (/api/...). Every change needs a reason, is audited and makes a configuration
version. Switching something off affects new requests only: past requests and files are untouched.

Configuration versions, export and import are app.api.config; bureau profile details stay on the Bureaus page.
"""

from __future__ import annotations

from flask import jsonify

from app import audit
from app.api.auth import admin_only, current_user
from app.api.common import CYCLE_LABELS, _tenant_required, body, bp, fail, message, svc
from app.api.config import versioned
from app.configstore import settings as client_settings
from app.enums import ReportingCycle, SubmissionType

CYCLE_HELP = {
    ReportingCycle.WEEKLY_1: "as-of date the 9th",
    ReportingCycle.WEEKLY_2: "as-of date the 16th",
    ReportingCycle.WEEKLY_3: "as-of date the 23rd",
    ReportingCycle.MONTH_END: "as-of date the last day of the month",
    ReportingCycle.DAILY: "chosen by the user, any date",
    ReportingCycle.DATA_CORRECTION: "chosen by the user, any date",
    ReportingCycle.AD_HOC: "chosen by the user, any date",
    ReportingCycle.REJECTION_RESUBMISSION: "chosen by the user, any date",
}


def _reason() -> str:
    reason = (body().get("reason") or "").strip()
    if not reason:
        fail("Give a reason for the change (it is kept in the audit trail).")
    return reason[:500]


@bp.get("/admin/settings")
def admin_settings():
    """The bureau switches per type and the reporting cycles, for the Admin page (everyone may read them)."""
    from bureau_core.types import descriptor

    s = svc()
    tenant_id = _tenant_required("settings")
    profiles = s.bureaus.list(tenant_id, active_only=False)
    on = set(client_settings.enabled_cycles(s.engine, tenant_id))
    return jsonify(
        tenant_id=tenant_id,
        types=[{"value": str(t), "label": descriptor(t).label} for t in SubmissionType],
        bureaus=sorted({str(p.bureau_code) for p in profiles}),
        switches=[
            {
                "submission_type": str(p.submission_type),
                "bureau_code": str(p.bureau_code),
                "display_name": p.display_name,
                "is_active": p.is_active,
            }
            for p in profiles
        ],
        cycles=[
            {"value": c.value, "label": CYCLE_LABELS.get(c, c.value), "help": CYCLE_HELP[c], "enabled": c in on}
            for c in ReportingCycle
        ],
    )


@bp.post("/admin/bureaus/<submission_type>/<bureau_code>/active")
@admin_only
def switch_bureau(submission_type: str, bureau_code: str):
    """Switch one bureau on or off for one type: {active, reason}. Off: not offered when generating new files."""
    from dataclasses import replace

    from app.bureaus import BureauProfileNotFoundError

    s, b = svc(), body()
    tenant_id = _tenant_required("bureau switches")
    try:
        current = s.bureaus.get(tenant_id, bureau_code, submission_type)
    except BureauProfileNotFoundError, ValueError:
        fail(f"No bureau profile {bureau_code} / {submission_type}", 404)
    active = bool(b.get("active"))
    if active == current.is_active:
        return message("Nothing changed.")
    reason = _reason()
    s.bureaus.upsert(replace(current, is_active=active))
    target = f"{bureau_code}/{submission_type}"
    audit.record(
        s.engine, current_user(), "BUREAU_SWITCHED", tenant_id=tenant_id, target=target, reason=reason,
        before=current.is_active, after=active,
    )  # fmt: skip
    versioned(tenant_id, "BUREAU_SWITCHED", target, reason)
    state = "on" if active else "off (past files are kept)"
    return message(f"{current.display_name} for {submission_type.title()} is {state}. Applies to new files.")


@bp.post("/admin/cycles")
@admin_only
def save_cycles():
    """The reporting cycles new requests may use: {cycles: [codes], reason}. All of them = the default."""
    s, b = svc(), body()
    tenant_id = _tenant_required("reporting cycles")
    try:
        chosen = client_settings.check_cycles(b.get("cycles"))
    except ValueError as exc:
        fail(str(exc))
    before = [c.value for c in client_settings.enabled_cycles(s.engine, tenant_id)]
    if chosen == before:
        return message("Nothing changed.")
    reason = _reason()
    every = len(chosen) == len(ReportingCycle)
    client_settings.put(s.engine, tenant_id, "reporting_cycles", None if every else chosen, current_user())
    audit.record(
        s.engine, current_user(), "CYCLES_CHANGED", tenant_id=tenant_id, target="reporting_cycles", reason=reason,
        before=before, after=chosen,
    )  # fmt: skip
    versioned(tenant_id, "CYCLES_CHANGED", "reporting_cycles", reason)
    return message(f"Reporting cycles for new requests: {', '.join(chosen)}. Past requests are untouched.")
