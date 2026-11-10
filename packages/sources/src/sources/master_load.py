"""Apply an upload to the L2a demographics master through the client's existing source views (ADR 0002 PR 6, owner
2026-11-09: rarfincare's data enters the master through the views it already runs; a configured mapping comes when a
second client needs one).

The views give canonical snapshot rows (party, identifiers, phones, e-mails, addresses, account, holders, guarantors,
related parties, securities, cheque dishonours). entities_to_master splits them the way the master holds them:

- a party per party_key (the customer key; rarfincare has no customer ID, so the views' key stays: consumer
  <account>-<row>, commercial the loan account), its lists replaced as a whole;
- a sanction per account number, with its static fields (the loan system's daily figures are L2b, not stored here);
- links party + role: consumer holders BORROWER (the first), CO_BORROWER (the others), GUARANTOR (ownership GUARANTOR);
  commercial holders BORROWER, guarantors GUARANTOR, related parties RELATED on each of the borrower's sanctions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from sqlalchemy import Engine, text

from canonical.master.apply import ApplyResult, PartyIn, SanctionIn, SanctionPartyIn, SecurityIn, apply_upload
from canonical.master.tables import dm_sanction
from canonical.position import POSITION_COLUMNS
from canonical.schema import MODEL
from sources.extract import coerce
from sources.master import ID_COLUMNS
from sources.views import bind_params, compile_views

Rows = Mapping[str, Iterable[Mapping[str, Any]]]
SANCTION_FIELDS = frozenset(dm_sanction.c.keys()) - set(POSITION_COLUMNS) - {
    "sanction_id", "tenant_id", "is_active", "first_load_id", "last_load_id", "updated_at",
}  # fmt: skip
PERSON = (
    "full_name",
    "name_prefix",
    "business_category",
    "industry_type",
    "gender",
    "birth_date",
    "incorporation_date",
)
ADDRESS = (
    "address_line_1",
    "address_line_2",
    "address_line_3",
    "city",
    "district",
    "state_code",
    "pin_code",
    "country",
)


def typed(entity: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Values in the canonical column types (drivers differ: SQLite gives dates as text)."""
    cols = {c.name: c for c in MODEL.entities[entity].columns}
    return {k: coerce(cols[k], v) if k in cols else v for k, v in row.items()}


def _without(row: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in keys and k != "row_id" and k != "snapshot_id"}


def related_party(entity: str, row: Mapping[str, Any]) -> PartyIn:
    """A guarantor / related party from its flattened row: fields, identifiers by type, address, phones."""
    t = typed(entity, row)
    phones = [
        {"phone_number": t.get("mobile"), "phone_type": "MOBILE"},
        {"phone_number": t.get("telephone"), "area_code": t.get("telephone_area_code"), "phone_type": "OFFICE"},
        {"phone_number": t.get("fax"), "area_code": t.get("fax_area_code"), "phone_type": "FAX"},
    ]
    address = {k: t.get(k) for k in ADDRESS}
    return PartyIn(
        str(t["related_key"]),
        "INDIVIDUAL" if "INDIVIDUAL" in str(t.get("related_type") or "") else "ENTITY",
        {k: t.get(k) for k in PERSON} | {"party_type": t.get("related_type")},
        identifiers=[{"id_type": i, "id_number": t[c]} for c, i in ID_COLUMNS.items() if t.get(c)],
        phones=[p for p in phones if p["phone_number"]],
        addresses=[address] if any(v is not None for v in address.values()) else [],
    )


def entities_to_master(entities: Rows, submission_type: str) -> tuple[list[PartyIn], list[SanctionIn]]:
    """Snapshot-shaped rows (one upload) -> the master's parties and sanctions."""
    e = {k: [typed(k, r) for r in v] for k, v in entities.items()}
    consumer = str(submission_type).upper() == "CONSUMER"

    def of_party(entity: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for r in e.get(entity, []):
            out.setdefault(str(r["party_key"]), []).append(_without(r, "party_key"))
        return out

    def of_account(entity: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for r in e.get(entity, []):
            out.setdefault(str(r["account_key"]), []).append(r)
        return out

    lists = {name: of_party(f"party_{name[:-1] if name != 'addresses' else 'address'}") for name in
             ("identifiers", "phones", "emails", "addresses")}  # fmt: skip
    parties = [
        PartyIn(
            str(p["party_key"]),
            "INDIVIDUAL" if consumer else "ENTITY",
            _without(p, "party_key"),
            **{name: lists[name].get(str(p["party_key"]), []) for name in lists},
        )
        for p in e.get("party", [])
    ]
    related: dict[str, PartyIn] = {}
    for entity in ("relationship", "guarantor"):
        for r in e.get(entity, []):
            related.setdefault(str(r["related_key"]), related_party(entity, r))

    holders, guarantors = of_account("account_holder"), of_account("guarantor")
    securities, cheques = of_account("security"), of_account("cheque_dishonour")
    borrower_of: dict[str, list[str]] = {}
    links: dict[str, list[SanctionPartyIn]] = {}
    for a in e.get("account", []):
        key = str(a["account_key"])
        out = links.setdefault(key, [])
        for i, h in enumerate(holders.get(key, [])):
            own = h.get("ownership_indicator")
            role = (
                "GUARANTOR"
                if consumer and own == "GUARANTOR"
                else ("BORROWER" if i == 0 or not consumer else "CO_BORROWER")
            )
            out.append(SanctionPartyIn(str(h["party_key"]), role, {"ownership_indicator": own}))
            if role == "BORROWER":
                borrower_of.setdefault(str(h["party_key"]), []).append(key)
        for g in guarantors.get(key, []):
            out.append(SanctionPartyIn(str(g["related_key"]), "GUARANTOR",
                                       {"guarantee_invocation_date": g.get("guarantee_invocation_date")}))  # fmt: skip
    for r in e.get("relationship", []):  # a related party is linked on each of the borrower's sanctions
        for key in borrower_of.get(str(r["party_key"]), []):
            values = {
                "relationship_type": r.get("relationship_type"),
                "control_percentage": r.get("control_percentage"),
            }
            links[key].append(SanctionPartyIn(str(r["related_key"]), "RELATED", values))

    sanctions = [
        SanctionIn(
            str(a["account_number"] or a["account_key"]),
            "",
            {k: v for k, v in a.items() if k in SANCTION_FIELDS and k not in ("account_number", "sanction_number")},
            links[str(a["account_key"])],
            [
                SecurityIn(f"S{i:04d}", _without(s, "account_key"))
                for i, s in enumerate(securities.get(str(a["account_key"]), []))
            ],
            [_without(c, "account_key") for c in cheques.get(str(a["account_key"]), [])],
        )
        for a in e.get("account", [])
    ]
    return parties + list(related.values()), sanctions


def rows_from_views(
    source_engine: Engine, submission_type: str, as_of_date: date, load_id: int
) -> dict[str, list[dict[str, Any]]]:
    """One upload as the type's source views give it (no exclusions or overrides: the master holds the source)."""
    compiled = compile_views(source_engine, submission_type)
    out: dict[str, list[dict[str, Any]]] = {}
    with source_engine.connect() as conn:
        for entity, sql in compiled.sql.items():
            params = bind_params(sql, {"as_of_date": as_of_date, "load_id": load_id})
            out[entity] = [dict(r._mapping) for r in conn.execute(text(sql), params)]
    return out


def load_upload(
    app_engine: Engine, source_engine: Engine, tenant_id: str, submission_type: str, as_of_date: date, load_id: int
) -> ApplyResult:
    """Apply one upload to the tenant's master through its source views (one transaction)."""
    parties, sanctions = entities_to_master(rows_from_views(source_engine, submission_type, as_of_date, load_id),
                                            submission_type)  # fmt: skip
    return apply_upload(app_engine, tenant_id, load_id, parties, sanctions)
