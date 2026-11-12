"""Apply an upload to the L2a customer tables through the client's source views (ADR 0002 PR 6; sanction model, owner
2026-11-11: one data model for consumer and commercial, keyed by the sanction reference number).

The views give canonical snapshot rows (party, identifiers, phones, e-mails, addresses, account, holders, guarantors,
related parties, securities, cheque dishonours). entities_to_master groups them per sanction:

- consumer: the sanction reference number is the account number (rarfincare's consumer data has no sanction number).
  The account's holders in workbook order: the first that is not a guarantor is the borrower (on the sanction), the
  others are holders (GUARANTOR or CO_BORROWER).
- commercial: each loan account belongs to a sanction (automn.vw_commercial_account_ref: account -> Sanction Reference
  Number). One borrower per sanction (BS), with its IDs, phones, addresses (AS), guarantors (GS), related parties (RS),
  securities (SS) and cheque dishonours (CD): the rows of all its accounts, each once (the views repeat a sanction's
  rows on every account).
- loan accounts: the account rows the views give for the upload's date (static fields; the LMS figures are L2b).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from sqlalchemy import Engine, text

from canonical.db import SCHEMA
from canonical.master.apply import (
    ACCOUNT_FIELDS,
    HOLDER_LIST_FIELDS,
    PERSON_FIELDS,
    SANCTION_LIST_FIELDS,
    AccountIn,
    ApplyResult,
    PersonIn,
    SanctionIn,
    apply_upload,
)
from canonical.schema import MODEL
from sources.extract import coerce
from sources.views import bind_params, compile_views

Rows = Mapping[str, Iterable[Mapping[str, Any]]]
ACCOUNT_REF_VIEW = "vw_commercial_account_ref"  # commercial: account -> sanction reference number, per upload
LISTS = {"identifiers": "party_identifier", "phones": "party_phone", "emails": "party_email",
         "addresses": "party_address"}  # fmt: skip


def typed(entity: str, row: Mapping[str, Any]) -> dict[str, Any]:
    """Values in the canonical column types (drivers differ: SQLite gives dates as text)."""
    cols = {c.name: c for c in MODEL.entities[entity].columns}
    return {k: coerce(cols[k], v) if k in cols else v for k, v in row.items()}


def _pick(row: Mapping[str, Any], allowed: tuple[str, ...]) -> dict[str, Any]:
    return {k: row.get(k) for k in allowed if k in row}


def _row_no(party_key: str) -> int:
    """The workbook row of a consumer holder (<account>-<row>), for ordering; 0 when the key has none."""
    tail = str(party_key).rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


def entities_to_master(
    entities: Rows, submission_type: str, account_refs: Mapping[str, str] | None = None
) -> tuple[list[SanctionIn], list[AccountIn]]:
    """Snapshot-shaped rows (one upload) -> sanctions and accounts. account_refs: account -> sanction reference
    number (commercial; consumer uses the account number)."""
    e = {k: [typed(k, r) for r in v] for k, v in entities.items()}
    commercial = str(submission_type).upper() == "COMMERCIAL"
    refs = dict(account_refs or {})

    def ref_of(account: str) -> str | None:
        return refs.get(account) if commercial else account

    def by(entity: str, key: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for r in e.get(entity, []):
            out.setdefault(str(r[key]), []).append(r)
        return out

    parties, lists = by("party", "party_key"), {name: by(entity, "party_key") for name, entity in LISTS.items()}

    def person(party_key: str, role: str, ownership: Any, kind: str) -> PersonIn:
        p = (parties.get(party_key) or [{}])[0]
        values = _pick(p, PERSON_FIELDS) | {"ownership_indicator": ownership}
        return PersonIn(kind, values, role=role, **{
            name: [_pick(r, HOLDER_LIST_FIELDS[name]) for r in lists[name].get(party_key, [])] for name in LISTS
        })  # fmt: skip

    # the holders of each sanction, in account order (commercial: the sanction's first account gives them)
    holders_of: dict[str, list[dict[str, Any]]] = {}
    accounts_of: dict[str, list[str]] = {}
    for account, hs in sorted(by("account_holder", "account_key").items()):
        ref = ref_of(account)
        if ref is None:
            continue
        accounts_of.setdefault(ref, []).append(account)
        holders_of.setdefault(ref, sorted(hs, key=lambda h: _row_no(h["party_key"])))

    def of_sanction(rows: dict[str, list[dict[str, Any]]], ref: str, name: str) -> list[dict[str, Any]]:
        """The sanction's rows from all its accounts, each once (the views repeat a sanction's rows per account)."""
        out = [_pick(r, SANCTION_LIST_FIELDS[name]) for a in accounts_of[ref] for r in rows.get(a, [])]
        return [dict(t) for t in dict.fromkeys(tuple(r.items()) for r in out)]

    kind = "ENTITY" if commercial else "INDIVIDUAL"
    guarantors, relationships = by("guarantor", "account_key"), by("relationship", "party_key")
    securities, cheques = by("security", "account_key"), by("cheque_dishonour", "account_key")
    sanctions = []
    for ref, hs in holders_of.items():
        borrower = next((h for h in hs if h.get("ownership_indicator") != "GUARANTOR"), hs[0])
        others = [
            person(str(h["party_key"]), "GUARANTOR" if h.get("ownership_indicator") == "GUARANTOR" else "CO_BORROWER",
                   h.get("ownership_indicator"), kind)
            for h in hs if h is not borrower
        ]  # fmt: skip
        b_key = str(borrower["party_key"])
        sanctions.append(SanctionIn(
            ref,
            person(b_key, "BORROWER", borrower.get("ownership_indicator"), kind),
            others,
            guarantors=of_sanction(guarantors, ref, "guarantors"),
            relationships=[_pick(r, SANCTION_LIST_FIELDS["relationships"]) for r in relationships.get(b_key, [])],
            securities=of_sanction(securities, ref, "securities"),
            cheques=of_sanction(cheques, ref, "cheques"),
        ))  # fmt: skip

    accounts = [
        AccountIn(str(a["account_number"] or a["account_key"]), ref, _pick(a, ACCOUNT_FIELDS))
        for a in e.get("account", [])
        if (ref := ref_of(str(a["account_key"]))) is not None
    ]
    return sanctions, accounts


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


def account_refs(source_engine: Engine, submission_type: str, load_id: int) -> dict[str, str]:
    """Commercial: each account of the upload -> its sanction reference number (automn.vw_commercial_account_ref)."""
    if str(submission_type).upper() != "COMMERCIAL":
        return {}
    sql = f"SELECT account_number, sanction_reference_number FROM {SCHEMA}.{ACCOUNT_REF_VIEW} WHERE load_id = :l"  # noqa: S608 (names from code, value bound)
    with source_engine.connect() as conn:
        return {str(a): str(r) for a, r in conn.execute(text(sql), {"l": load_id}) if a and r}


def load_upload(
    app_engine: Engine, source_engine: Engine, tenant_id: str, submission_type: str, as_of_date: date, load_id: int
) -> ApplyResult:
    """Apply one upload to the tenant's customer tables through its source views (one transaction)."""
    rows = rows_from_views(source_engine, submission_type, as_of_date, load_id)
    sanctions, accounts = entities_to_master(rows, submission_type, account_refs(source_engine, submission_type,
                                                                                  load_id))  # fmt: skip
    return apply_upload(app_engine, tenant_id, load_id, sanctions, accounts)
