"""Parity before the switch-over (ADR 0002 PR 6): for one upload and as-of date, the snapshot rows the current path
builds (the type's source views) next to the rows the new path builds (the upload applied to the demographics master,
then the master + account position). Equal rows mean equal files. Writes only the master (nothing reads it yet);
no snapshot is created.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine, select, text

from canonical.schema import MODEL
from sources.extract import coerce
from sources.master import compile_master
from sources.master_load import entities_to_master, rows_from_views
from sources.views import bind_params

EXAMPLES = 5


def _value(col: Any, value: Any) -> str:
    """A value as compared: numbers by value (480038 = 480038.00, 15.00 = 15.0000), the rest as text."""
    if value is None:
        return ""
    v = coerce(col, value)
    return format(v.normalize(), "f") if isinstance(v, Decimal) else str(v)


def _row(entity: str, r: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_value(c, r.get(c.name)) for c in MODEL.entities[entity].columns)


def _why_missing(engine: Engine, tenant_id: str, submission_type: str, as_of_date: date, account: str) -> str:
    """Why the new path does not report an account the current path reports (in plain words)."""
    from canonical.master.tables import dm_party, dm_sanction, dm_sanction_party
    from canonical.position import VIEW
    from sources.master import SELECTION

    s, sp, pt = dm_sanction, dm_sanction_party, dm_party
    with engine.connect() as conn:
        found = conn.execute(
            select(s.c.sanction_id, s.c.is_active).where(s.c.tenant_id == tenant_id, s.c.account_number == account)
        ).first()
        if found is None:
            return "not in the customer tables"
        if not found.is_active:
            return "marked inactive in the customer tables"
        links = conn.execute(
            select(sp.c.role, pt.c.party_kind).select_from(sp.join(pt, pt.c.party_id == sp.c.party_id))
            .where(sp.c.sanction_id == found.sanction_id)
        ).all()  # fmt: skip
        kinds = sorted({r.party_kind for r in links if r.role == "BORROWER"})
        if not kinds:
            return f"no borrower (roles: {', '.join(sorted({r.role for r in links})) or 'none'})"
        need = SELECTION[submission_type].borrower_kind
        if need not in kinds:
            return f"the borrower is {'/'.join(kinds)}, the format reports {need} borrowers"
        rows = conn.execute(
            text(f"SELECT lms_type, COUNT(*) AS n FROM automn.{VIEW} WHERE account_number = :a AND as_of_date = :d "  # noqa: S608
                 "GROUP BY lms_type"),
            {"a": account, "d": as_of_date},
        ).all()  # fmt: skip
        types = {r.lms_type or "upload": r.n for r in rows}
        if not types:
            return "no LMS figures for the date"
        if not ({submission_type, "upload"} & set(types)):
            return f"LMS figures for the date only in the {'/'.join(sorted(types))} LMS view"
        return "selected: differs in its values"


def _example(entity: str, row: tuple[str, ...]) -> dict[str, str]:
    return {c.name: v for c, v in zip(MODEL.entities[entity].columns, row, strict=True) if v != ""}


ACCOUNT_CHILDREN = ("account_history", "guarantor", "security", "cheque_dishonour")
PARTY_CHILDREN = ("party_identifier", "party_phone", "party_email", "party_address", "relationship")


def reportable(rows: dict[str, list[dict[str, Any]]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """The rows that can become a bureau record: a record needs an account AND its borrower (the files are written
    from account + borrower pairs). An account without a holder, or a holder whose account has no LMS row for the
    date, cannot reach a file in either path; they are set aside and counted. Returns (rows kept, counts)."""
    accounts = {str(r["account_key"]) for r in rows.get("account", [])}
    holders = [h for h in rows.get("account_holder", []) if str(h["account_key"]) in accounts]
    held = {str(h["account_key"]) for h in holders}
    parties = {str(h["party_key"]) for h in holders}
    all_parties = {str(p["party_key"]) for p in rows.get("party", [])}
    kept = dict(rows)
    kept["account"] = [a for a in rows.get("account", []) if str(a["account_key"]) in held]
    kept["account_holder"] = holders
    kept["party"] = [p for p in rows.get("party", []) if str(p["party_key"]) in parties]
    for e in ACCOUNT_CHILDREN:
        kept[e] = [r for r in rows.get(e, []) if str(r["account_key"]) in held]
    for e in PARTY_CHILDREN:
        kept[e] = [r for r in rows.get(e, []) if str(r["party_key"]) in parties]
    return kept, {
        "accounts without a borrower": len(accounts - held),
        "borrowers without an account": len(all_parties - parties),
    }


def compare(
    app_engine: Engine,
    source_engine: Engine,
    tenant_id: str,
    submission_type: str,
    as_of_date: date,
    load_id: int,
    *,
    history: bool = False,
) -> dict[str, Any]:
    """{"same": bool, "summary": [plain lines], "entities": {entity: {views, master, only_in_views, only_in_master}}}"""
    from canonical.master.apply import apply_upload

    submission_type = submission_type.upper()
    old = rows_from_views(source_engine, submission_type, as_of_date, load_id)
    parties, sanctions = entities_to_master(old, submission_type)
    applied = apply_upload(app_engine, tenant_id, load_id, parties, sanctions)
    compiled = compile_master(app_engine, submission_type, history=history)
    new: dict[str, list[dict[str, Any]]] = {}
    with app_engine.connect() as conn:
        for entity, sql in compiled.sql.items():
            params = bind_params(sql, {"as_of_date": as_of_date, "tenant_id": tenant_id})
            new[entity] = [dict(r._mapping) for r in conn.execute(text(sql), params)]

    old, left_out = reportable(old)  # the same rule on both sides: only what can reach a file is compared
    new, _ = reportable(new)
    entities: dict[str, Any] = {}
    summary: list[str] = []
    for entity in MODEL.entities:
        a = Counter(_row(entity, r) for r in old.get(entity, []))
        b = Counter(_row(entity, r) for r in new.get(entity, []))
        if not a and not b:
            continue
        only_old, only_new = a - b, b - a
        entities[entity] = {
            "views": sum(a.values()),
            "master": sum(b.values()),
            "only_in_views": [_example(entity, r) for r in sorted(only_old.elements())[:EXAMPLES]],
            "only_in_master": [_example(entity, r) for r in sorted(only_new.elements())[:EXAMPLES]],
        }
        if only_old or only_new:
            summary.append(
                f"{entity}: {sum(a.values())} rows today, {sum(b.values())} new; "
                f"{sum(only_old.values())} only today, {sum(only_new.values())} only new"
            )
    old_keys = {str(r["account_key"]) for r in old.get("account", [])}
    new_keys = {str(r["account_key"]) for r in new.get("account", [])}
    missing = sorted(old_keys - new_keys)
    extra = sorted(new_keys - old_keys)
    reasons = Counter(_why_missing(app_engine, tenant_id, submission_type, as_of_date, k) for k in missing)
    accounts = {
        "only_today": [
            {"account_key": k, "why": _why_missing(app_engine, tenant_id, submission_type, as_of_date, k)}
            for k in missing[: EXAMPLES * 2]
        ],
        "only_new": extra[: EXAMPLES * 2],  # in the customer tables with LMS figures for the date, not in this upload
    }
    if missing:
        summary.append(
            f"accounts only today: {len(missing)} (" + "; ".join(f"{n} {why}" for why, n in reasons.most_common()) + ")"
        )
    if extra:
        summary.append(
            f"accounts only new: {len(extra)} (in the customer tables from earlier uploads, not in this one)"
        )
    same = not summary
    head = (
        f"{submission_type} {as_of_date} upload {load_id}: "
        + ("IDENTICAL - every row the same" if same else f"{len(summary)} part(s) differ")
        + f" (master: {applied.parties_added} parties added, {applied.parties_updated} updated; "
        f"{applied.sanctions_added} accounts added, {applied.sanctions_updated} updated)"
    )
    aside = [f"{n} {what}" for what, n in left_out.items() if n]
    note = [f"set aside (cannot reach a file in either path): {', '.join(aside)}"] if aside else []
    return {
        "same": same,
        "summary": [head, *note, *summary],
        "left_out": left_out,
        "accounts": accounts,
        "entities": entities,
    }
