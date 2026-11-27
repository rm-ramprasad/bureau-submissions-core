"""A client's settings for the upload (input) checks (issue #19 part 2, owner 2026-11-27; migration 026).

The checks themselves are the product's (canonical/types/<type>/input_checks.yaml). Per NBFC and type, an
administrator may switch a check off or change its severity (warn / block submit), with a reason (audited by the API).
Checks that protect what the bureau file needs are locked: their kind is in LOCKED_KINDS (a required value, a valid
code or label that maps to a file code, no duplicate or conflicting records). Settings saved for a check that is
locked or no longer exists are ignored.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy import Engine, delete, select, update

from canonical.db import utcnow
from canonical.staging.checks import Check, CheckSet, SheetChecks
from canonical.staging.tables import input_check_policy

LOCKED_KINDS = frozenset(
    {"required", "any_of", "duplicate", "unique_where", "consistent", "code", "label", "label_matches_code"}
)
SEVERITIES = ("warn", "block")


class CheckPolicyError(ValueError):
    pass


def is_locked(check: Check) -> bool:
    return check.check in LOCKED_KINDS


def policies(engine: Engine, tenant_id: str, submission_type: str) -> dict[str, dict[str, Any]]:
    """check_id -> {is_enabled, severity, reason, updated_by, updated_at} for one NBFC and type."""
    t = input_check_policy.c
    with engine.connect() as conn:
        rows = conn.execute(
            select(input_check_policy).where(t.tenant_id == tenant_id, t.submission_type == submission_type.upper())
        ).all()
    return {r.check_id: dict(r._mapping) for r in rows}


def effective(check: Check, policy: dict[str, Any] | None) -> Check | None:
    """The check as it runs for the client: None when switched off. A locked check always runs as defined."""
    if policy is None or is_locked(check):
        return check
    if policy["is_enabled"] is False:
        return None
    return replace(check, severity=policy["severity"]) if policy["severity"] else check


def apply(checks: CheckSet, pol: dict[str, dict[str, Any]]) -> CheckSet:
    sheets = []
    for s in checks.sheets:
        kept = tuple(c for c in (effective(c, pol.get(c.id)) for c in s.checks) if c is not None)
        sheets.append(SheetChecks(s.sheet, kept))
    return replace(checks, sheets=tuple(sheets))


def set_policy(
    engine: Engine,
    tenant_id: str,
    submission_type: str,
    check: Check,
    *,
    enabled: bool,
    severity: str,
    reason: str,
    user: str,
    clock=utcnow,
) -> bool:
    """Save the client's setting for a check. Back to the check's own values removes the setting. True if changed."""
    if is_locked(check):
        raise CheckPolicyError(f"{check.id} protects what the bureau file needs: it cannot be switched off or changed.")
    if severity not in SEVERITIES:
        raise CheckPolicyError(f"Severity must be one of {', '.join(SEVERITIES)}")
    t, key = input_check_policy, _key(tenant_id, submission_type, check.id)
    current = effective(check, policies(engine, tenant_id, submission_type).get(check.id))
    if (current is not None, current.severity if current else check.severity) == (enabled, severity):
        return False
    if (enabled, severity) == (True, check.severity):
        clear_policy(engine, tenant_id, submission_type, check.id)
        return True
    if not reason.strip():
        raise CheckPolicyError(f"{check.id}: give a reason for the change (it is kept in the audit trail).")
    values: dict[str, Any] = dict(
        is_enabled=None if enabled else False,
        severity=None if severity == check.severity else severity,
        reason=reason.strip()[:500],
        updated_by=user[:100],
        updated_at=clock(),
    )
    with engine.begin() as conn:
        if conn.execute(update(t).where(*key).values(**values)).rowcount == 0:
            conn.execute(
                t.insert().values(
                    tenant_id=tenant_id, submission_type=submission_type.upper(), check_id=check.id, **values
                )
            )
    return True


def clear_policy(engine: Engine, tenant_id: str, submission_type: str, check_id: str) -> bool:
    with engine.begin() as conn:
        return conn.execute(delete(input_check_policy).where(*_key(tenant_id, submission_type, check_id))).rowcount > 0


def _key(tenant_id: str, submission_type: str, check_id: str) -> tuple:
    t = input_check_policy.c
    return (t.tenant_id == tenant_id, t.submission_type == submission_type.upper(), t.check_id == check_id)


def replace_all(
    engine: Engine, tenant_id: str, submission_type: str, entries: dict[str, dict[str, Any]], user: str, reason: str
) -> None:
    """An imported configuration: the type's settings become exactly `entries` (check_id -> is_enabled, severity).
    The caller has checked the ids (known, not locked)."""
    t = input_check_policy
    with engine.begin() as conn:
        conn.execute(delete(t).where(t.c.tenant_id == tenant_id, t.c.submission_type == submission_type.upper()))
        for check_id, e in entries.items():
            conn.execute(
                t.insert().values(
                    tenant_id=tenant_id, submission_type=submission_type.upper(), check_id=check_id,
                    is_enabled=e["is_enabled"], severity=e["severity"], reason=(reason or "configuration imported")[:500],
                    updated_by=user[:100], updated_at=utcnow(),
                )
            )  # fmt: skip
