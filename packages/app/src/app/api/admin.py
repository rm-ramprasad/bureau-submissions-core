"""Admin: bureau profiles; the audit trail. Routes on the API blueprint (/api/...)."""

from __future__ import annotations

import re

from flask import jsonify, request

from app import audit
from app.api.auth import admin_only, current_user
from app.api.common import (
    _tenant_required,
    body,
    bp,
    fail,
    last_month_end,
    message,
    selected_tenant,
    svc,
)
from app.api.config import versioned
from app.enums import (
    ReportingCycle,
)
from bureau_core.types import descriptor
from canonical.db import utcnow

# ---------------------------------------------------------------- audit
# ---------------------------------------------------------------- bureau profiles (admin)
PLACEHOLDER_MEMBER = re.compile(r"^(EXP|CIB|CRF|EQF)C?\d{6,7}$")  # the seed's dummy member IDs
FILE_NAME_FORBIDDEN = re.compile(r'[\\/:*?"<>|]')  # outside the {placeholders}


def _profile_json(p) -> dict:
    from bureau_core.bureau import InvalidFileNamePatternError

    try:
        example = p.file_name(utcnow(), last_month_end(), ReportingCycle.MONTH_END)
    except InvalidFileNamePatternError:
        example = None
    return {
        "bureau_code": p.bureau_code,
        "submission_type": p.submission_type,
        "display_name": p.display_name,
        "member_id": p.member_id,
        "member_short_name": p.member_short_name,
        "header_segment_tag": p.header_segment_tag,
        "file_name_pattern": p.file_name_pattern,
        "is_active": p.is_active,
        "example_file_name": example,
        "placeholder": bool(PLACEHOLDER_MEMBER.match(p.member_id or "")),
    }


@bp.get("/bureaus")
def bureau_profiles():
    """The bureau values used in file names and headers, per bureau and submission type (everyone may read them)."""
    from bureau_core.bureau import PATTERN_FIELDS

    tenant_id = _tenant_required("bureau profiles")
    profiles = svc().bureaus.list(tenant_id, active_only=False)
    return jsonify(tenant_id=tenant_id, pattern_fields=PATTERN_FIELDS, profiles=[_profile_json(p) for p in profiles])


@bp.post("/bureaus/<submission_type>/<bureau_code>")
@admin_only
def save_bureau_profile(submission_type: str, bureau_code: str):
    """Change one bureau profile: member ID, short name, header tag, file-name pattern, active. Needs a reason."""
    from dataclasses import replace

    from app.bureaus import BureauProfileNotFoundError
    from bureau_core.bureau import InvalidFileNamePatternError

    s, b = svc(), body()
    tenant_id = _tenant_required("bureau profiles")
    try:
        current = s.bureaus.get(tenant_id, bureau_code, submission_type)
    except BureauProfileNotFoundError, ValueError:
        fail(f"No bureau profile {bureau_code} / {submission_type}", 404)
    member_id = (b.get("member_id") or "").strip()
    short_name = (b.get("member_short_name") or "").strip() or None
    tag = (b.get("header_segment_tag") or "").strip()
    pattern = (b.get("file_name_pattern") or "").strip()
    reason = (b.get("reason") or "").strip()
    if not member_id or len(member_id) > 30 or not re.fullmatch(r"[A-Za-z0-9]+", member_id):
        fail("Member ID: letters and digits only, up to 30 characters.")
    if short_name and len(short_name) > 16:
        fail("Member short name: up to 16 characters (the file header has room for 16).")
    if len(tag) > 4 or (tag and not re.fullmatch(r"[A-Za-z0-9]+", tag)):
        fail("Header tag: up to 4 letters or digits.")
    if descriptor(current.submission_type).header_tag_required and not tag:
        fail(f"Header tag: required for {descriptor(current.submission_type).label.lower()} files.")
    if not pattern or len(pattern) > 200 or FILE_NAME_FORBIDDEN.search(re.sub(r"\{[^}]*\}", "", pattern)):
        fail(r'File name pattern: up to 200 characters, without \ / : * ? " < > |.')
    updated = replace(
        current,
        member_id=member_id,
        member_short_name=short_name,
        header_segment_tag=tag,
        file_name_pattern=pattern,
        is_active=bool(b.get("is_active", current.is_active)),
    )
    if updated == current:
        return message("Nothing changed.", profile=_profile_json(current))
    if not reason:
        fail("Give a reason for the change (it is kept in the audit trail).")
    try:
        s.bureaus.upsert(updated)
    except InvalidFileNamePatternError as exc:
        fail(str(exc))
    fields = ("member_id", "member_short_name", "header_segment_tag", "file_name_pattern", "is_active")
    audit.record(
        s.engine,
        current_user(),
        "BUREAU_PROFILE_CHANGED",
        tenant_id=tenant_id,
        target=f"{bureau_code}/{submission_type}",
        reason=reason[:500],
        before={f: getattr(current, f) for f in fields if getattr(current, f) != getattr(updated, f)},
        after={f: getattr(updated, f) for f in fields if getattr(current, f) != getattr(updated, f)},
    )
    versioned(tenant_id, "BUREAU_PROFILE_CHANGED", f"{bureau_code}/{submission_type}", reason[:500])
    return message(
        f"{updated.display_name} ({submission_type.title()}) saved. Applies to files generated from now on.",
        profile=_profile_json(updated),
    )


@bp.get("/audit")
def audit_log():
    s = svc()
    tenant_id = selected_tenant()
    flt = {k: request.args.get(k) or None for k in ("action", "actor", "target")}
    actions, actors = audit.choices(s.engine, tenant_id)
    return jsonify(
        events=audit.recent(s.engine, 200, tenant_id, flt["action"], flt["actor"], flt["target"]),
        actions=actions,
        actors=actors,
    )
