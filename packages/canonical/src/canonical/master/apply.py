"""Apply one upload to the L2a demographics master (ADR 0002, "Applying an upload").

The input is already in canonical shape (the configured mapping that turns a client's columns into it is a later
step). The rules, decided by the owner:

- Newest wins, completely: a customer or sanction in the upload gets every field from it; a blank (None or '') clears.
- Lists are replaced as a whole: a customer's identifiers, phones, e-mails and addresses; a sanction's parties (with
  their roles) and securities.
- Customers and sanctions not in the upload are untouched. Nothing is deleted.
- Change log: one row per overwritten value (field, old, new, load_id); a replaced list that differs logs '*list*'.
- Inside one upload an exact repeat counts once; the same key with different values blocks the upload.
- An upload that clears many previously filled values gets a warning (a truncated file?), not a block.

Everything happens in one transaction: an upload is applied completely or not at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, Table, delete, insert, select, update

from canonical.db import utcnow
from canonical.master.tables import (
    dm_change_log,
    dm_cheque_dishonour,
    dm_party,
    dm_party_address,
    dm_party_email,
    dm_party_identifier,
    dm_party_phone,
    dm_sanction,
    dm_sanction_party,
    dm_security,
)

CHUNK = 500  # keys per IN (...): SQL Server allows about 2100 parameters
CLEAR_WARN_SHARE = 0.10  # warn when an upload clears at least this share of the values it could clear ...
CLEAR_WARN_MIN = 20  # ... and at least this many
LIST_FIELD = "*list*"
LOG_VALUE_LENGTH = 1000

_SYSTEM = {"is_active", "first_load_id", "last_load_id", "updated_at", "load_id", "seq"}
PARTY_LISTS: dict[str, Table] = {
    "identifiers": dm_party_identifier,
    "phones": dm_party_phone,
    "emails": dm_party_email,
    "addresses": dm_party_address,
}


def _fields(table: Table, *keys: str) -> tuple[str, ...]:
    return tuple(c.name for c in table.columns if c.name not in _SYSTEM and c.name not in keys)


PARTY_FIELDS = _fields(dm_party, "party_id", "tenant_id", "customer_id", "party_kind")
SANCTION_FIELDS = _fields(dm_sanction, "sanction_id", "tenant_id", "account_number", "sanction_number")
LIST_FIELDS = {name: _fields(t, "party_id") for name, t in PARTY_LISTS.items()}
LINK_FIELDS = _fields(dm_sanction_party, "sanction_id", "party_id", "role")
SECURITY_FIELDS = _fields(dm_security, "sanction_id", "security_ref")
CHEQUE_FIELDS = _fields(dm_cheque_dishonour, "sanction_id")


class UploadConflict(ValueError):
    """The upload contradicts itself or refers to an unknown customer: nothing was applied."""


@dataclass(frozen=True)
class PartyIn:
    customer_id: str
    party_kind: str  # INDIVIDUAL / ENTITY
    values: Mapping[str, Any] = field(default_factory=dict)  # PARTY_FIELDS; a missing field is a blank
    identifiers: Sequence[Mapping[str, Any]] = ()
    phones: Sequence[Mapping[str, Any]] = ()
    emails: Sequence[Mapping[str, Any]] = ()
    addresses: Sequence[Mapping[str, Any]] = ()


@dataclass(frozen=True)
class SanctionPartyIn:
    customer_id: str
    role: str  # BORROWER, CO_BORROWER, GUARANTOR, DIRECTOR, PARTNER, ...
    values: Mapping[str, Any] = field(default_factory=dict)  # LINK_FIELDS


@dataclass(frozen=True)
class SecurityIn:
    security_ref: str
    values: Mapping[str, Any] = field(default_factory=dict)  # SECURITY_FIELDS


@dataclass(frozen=True)
class SanctionIn:
    account_number: str
    sanction_number: str = ""  # '' when the account has one sanction
    values: Mapping[str, Any] = field(default_factory=dict)  # SANCTION_FIELDS
    parties: Sequence[SanctionPartyIn] = ()
    securities: Sequence[SecurityIn] = ()
    cheques: Sequence[Mapping[str, Any]] = ()  # cheque dishonours (CHEQUE_FIELDS), in order


@dataclass
class ApplyResult:
    parties_added: int = 0
    parties_updated: int = 0
    parties_unchanged: int = 0
    sanctions_added: int = 0
    sanctions_updated: int = 0
    sanctions_unchanged: int = 0
    changes: int = 0  # change-log rows written
    cleared: int = 0  # previously filled values made blank
    clearable: int = 0  # previously filled values the upload could have cleared
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------- normalising


def _blank(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


def _row(values: Mapping[str, Any], allowed: tuple[str, ...], what: str) -> dict[str, Any]:
    unknown = set(values) - set(allowed)
    if unknown:
        raise ValueError(f"{what}: unknown field(s) {', '.join(sorted(unknown))}")
    return {f: _blank(values.get(f)) for f in allowed}


def _key(s: str, what: str) -> str:
    k = (s or "").strip()
    if not k:
        raise UploadConflict(f"{what} without a key")
    return k


@dataclass(frozen=True)
class _Party:
    customer_id: str
    row: dict[str, Any]  # party_kind + PARTY_FIELDS
    lists: dict[str, tuple[tuple[Any, ...], ...]]


@dataclass(frozen=True)
class _Sanction:
    key: tuple[str, str]
    row: dict[str, Any]
    links: tuple[tuple[Any, ...], ...]  # (customer_id, role, *LINK_FIELDS), sorted
    securities: tuple[tuple[Any, ...], ...]  # (security_ref, *SECURITY_FIELDS), sorted
    cheques: tuple[tuple[Any, ...], ...]  # CHEQUE_FIELDS, in order


def _party(p: PartyIn) -> _Party:
    cid = _key(p.customer_id, "A customer")
    row = {"party_kind": _key(p.party_kind, f"Customer {cid}: party kind"), **_row(p.values, PARTY_FIELDS, cid)}
    lists = {}
    for name in PARTY_LISTS:
        items = [tuple(_row(item, LIST_FIELDS[name], f"{cid} {name}").values()) for item in getattr(p, name)]
        lists[name] = tuple(dict.fromkeys(items))  # an exact repeat once, order kept (seq)
    return _Party(cid, row, lists)


def _sanction(s: SanctionIn) -> _Sanction:
    key = (_key(s.account_number, "An account"), (s.sanction_number or "").strip())
    what = "/".join(key).rstrip("/")
    links: dict[tuple[str, str], tuple[Any, ...]] = {}
    for link in s.parties:
        k = (_key(link.customer_id, f"{what}: a party"), _key(link.role, f"{what}: a role").upper())
        v = tuple(_row(link.values, LINK_FIELDS, f"{what} {k}").values())
        if links.setdefault(k, v) != v:
            raise UploadConflict(f"Account {what}: customer {k[0]} as {k[1]} is listed twice with different values")
    secs: dict[str, tuple[Any, ...]] = {}
    for sec in s.securities:
        ref = _key(sec.security_ref, f"{what}: a security")
        v = tuple(_row(sec.values, SECURITY_FIELDS, f"{what} {ref}").values())
        if secs.setdefault(ref, v) != v:
            raise UploadConflict(f"Account {what}: security {ref} is listed twice with different values")
    return _Sanction(
        key,
        _row(s.values, SANCTION_FIELDS, what),
        tuple(sorted(k + v for k, v in links.items())),
        tuple(sorted((r, *v) for r, v in secs.items())),
        tuple(dict.fromkeys(tuple(_row(c, CHEQUE_FIELDS, f"{what} cheque").values()) for c in s.cheques)),
    )


def _dedupe[K, V](items: Iterable[V], key: Any, what: str) -> dict[K, V]:
    out: dict[K, V] = {}
    for item in items:
        k = key(item)
        if out.setdefault(k, item) != item:
            raise UploadConflict(f"{what} {k} appears twice in the upload with different values")
    return out


def _chunks[T](items: Sequence[T]) -> Iterable[Sequence[T]]:
    for i in range(0, len(items), CHUNK):
        yield items[i : i + CHUNK]


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, tuple):
        v = "; ".join(" | ".join("" if x is None else str(x) for x in item) for item in v)
    return str(v)[:LOG_VALUE_LENGTH]


# ---------------------------------------------------------------------------------------------------- applying


class _Applier:
    def __init__(self, conn: Connection, tenant_id: str, load_id: int, now: datetime) -> None:
        self.conn, self.tenant_id, self.load_id, self.now = conn, tenant_id, load_id, now
        self.result = ApplyResult()
        self.log: list[dict[str, Any]] = []

    def _change(self, entity: str, key: str, fld: str, old: Any, new: Any) -> None:
        self.log.append(
            dict(
                tenant_id=self.tenant_id,
                entity=entity,
                entity_key=key[:150],
                field=fld,
                old_value=_text(old),
                new_value=_text(new),
                load_id=self.load_id,
                changed_at=self.now,
            )
        )

    def _compare(self, entity: str, key: str, old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
        changed = False
        for f, v in new.items():
            was = old[f]
            if was is not None and f != "party_kind":  # required, never blank
                self.result.clearable += 1
            if was != v:
                changed = True
                self._change(entity, key, f, was, v)
                if v is None:
                    self.result.cleared += 1
        return changed

    def _upsert(
        self, table: Table, id_col: str, keys: dict[Any, dict[str, Any]], key_cols: tuple[str, ...], entity: str
    ) -> dict[Any, int]:
        """Insert or overwrite master rows; returns key -> id."""
        fields = list(next(iter(keys.values())).keys()) if keys else []
        existing: dict[Any, Any] = {}
        all_keys = list(keys)
        for part in _chunks(all_keys):
            if len(key_cols) == 1:
                cond = table.c[key_cols[0]].in_(part)
            else:  # (account_number, sanction_number): filter on the first, match both
                cond = table.c[key_cols[0]].in_({k[0] for k in part})
            rows = self.conn.execute(select(table).where(table.c.tenant_id == self.tenant_id, cond)).mappings()
            for r in rows:
                k = r[key_cols[0]] if len(key_cols) == 1 else tuple(r[c] for c in key_cols)
                if k in keys:
                    existing[k] = r
        ids: dict[Any, int] = {}
        added = updated = 0
        for k, new in keys.items():
            label = k if isinstance(k, str) else "/".join(k).rstrip("/")
            old = existing.get(k)
            if old is None:
                key_values = dict(zip(key_cols, (k,) if isinstance(k, str) else k, strict=True))
                inserted = self.conn.execute(
                    insert(table).values(
                        tenant_id=self.tenant_id,
                        **key_values,
                        **new,
                        is_active=True,
                        first_load_id=self.load_id,
                        last_load_id=self.load_id,
                        updated_at=self.now,
                    )
                ).inserted_primary_key
                assert inserted is not None
                ids[k] = inserted[0]
                added += 1
                continue
            ids[k] = old[id_col]
            if self._compare(entity, label, old, {f: new[f] for f in fields}):
                self.conn.execute(
                    update(table)
                    .where(table.c[id_col] == old[id_col])
                    .values(**new, last_load_id=self.load_id, updated_at=self.now)
                )
                updated += 1
        unchanged = len(keys) - added - updated
        res = self.result
        if entity == "dm_party":
            res.parties_added, res.parties_updated, res.parties_unchanged = added, updated, unchanged
        else:
            res.sanctions_added, res.sanctions_updated, res.sanctions_unchanged = added, updated, unchanged
        return ids

    def _replace_list(
        self,
        table: Table,
        owner_col: str,
        owner_id: int,
        cols: tuple[str, ...],
        new: tuple[tuple[Any, ...], ...],
        entity: str,
        label: str,
        *,
        seq: bool,
    ) -> bool:
        query = select(*(table.c[c] for c in cols)).where(table.c[owner_col] == owner_id)
        rows = [tuple(r) for r in self.conn.execute(query.order_by(table.c.seq) if seq else query)]
        old = tuple(rows if seq else sorted(rows))  # unordered lists: sorted here, not by the database's collation
        if old == new:
            return False
        if old:
            self.result.clearable += 1
            if not new:
                self.result.cleared += 1
            self._change(entity, label, LIST_FIELD, old, new)
        self.conn.execute(delete(table).where(table.c[owner_col] == owner_id))
        if new:
            inserts = [
                {
                    owner_col: owner_id,
                    **({"seq": i} if seq else {}),
                    **dict(zip(cols, item, strict=True)),
                    "load_id": self.load_id,
                }
                for i, item in enumerate(new, start=1)
            ]
            self.conn.execute(insert(table), inserts)
        return True

    def run(self, parties: Sequence[PartyIn], sanctions: Sequence[SanctionIn]) -> ApplyResult:
        ps: dict[str, _Party] = _dedupe([_party(p) for p in parties], lambda p: p.customer_id, "Customer")
        ss: dict[tuple[str, str], _Sanction] = _dedupe([_sanction(s) for s in sanctions], lambda s: s.key, "Account")

        party_ids = self._upsert(dm_party, "party_id", {k: p.row for k, p in ps.items()}, ("customer_id",), "dm_party")
        for cid, p in ps.items():
            for name, table in PARTY_LISTS.items():
                self._replace_list(
                    table, "party_id", party_ids[cid], LIST_FIELDS[name], p.lists[name], table.name, cid, seq=True
                )

        linked = sorted({link[0] for s in ss.values() for link in s.links} - set(party_ids))
        for part in _chunks(linked):
            rows = self.conn.execute(
                select(dm_party.c.customer_id, dm_party.c.party_id).where(
                    dm_party.c.tenant_id == self.tenant_id, dm_party.c.customer_id.in_(part)
                )
            )
            party_ids.update({r.customer_id: r.party_id for r in rows})
        missing = [c for c in linked if c not in party_ids]
        if missing:
            more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
            raise UploadConflict(f"Accounts refer to unknown customer(s): {', '.join(missing[:5])}{more}")

        sanction_ids = self._upsert(
            dm_sanction,
            "sanction_id",
            {k: s.row for k, s in ss.items()},
            ("account_number", "sanction_number"),
            "dm_sanction",
        )
        for k, s in ss.items():
            label = "/".join(k).rstrip("/")
            links = tuple(sorted((party_ids[c], role, *rest) for c, role, *rest in s.links))
            self._replace_list(
                dm_sanction_party,
                "sanction_id",
                sanction_ids[k],
                ("party_id", "role", *LINK_FIELDS),
                links,
                "dm_sanction_party",
                label,
                seq=False,
            )
            self._replace_list(
                dm_security,
                "sanction_id",
                sanction_ids[k],
                ("security_ref", *SECURITY_FIELDS),
                s.securities,
                "dm_security",
                label,
                seq=False,
            )
            self._replace_list(
                dm_cheque_dishonour,
                "sanction_id",
                sanction_ids[k],
                CHEQUE_FIELDS,
                s.cheques,
                "dm_cheque_dishonour",
                label,
                seq=True,
            )

        if self.log:
            self.conn.execute(insert(dm_change_log), self.log)
        r = self.result
        r.changes = len(self.log)
        if r.cleared >= CLEAR_WARN_MIN and r.cleared >= CLEAR_WARN_SHARE * r.clearable:
            r.warnings.append(
                f"This upload clears {r.cleared} of {r.clearable} previously filled values. "
                "Is the file complete? (applied; the change log has the old values)"
            )
        return r


def apply_upload(
    engine: Engine,
    tenant_id: str,
    load_id: int,
    parties: Sequence[PartyIn] = (),
    sanctions: Sequence[SanctionIn] = (),
    *,
    now: datetime | None = None,
) -> ApplyResult:
    """Apply one upload (load_id, the L1 input_load row) to the tenant's demographics master, in one transaction.
    Raises UploadConflict (nothing applied) when the upload contradicts itself or links an unknown customer."""
    with engine.begin() as conn:
        return _Applier(conn, tenant_id, load_id, now or utcnow()).run(parties, sanctions)
