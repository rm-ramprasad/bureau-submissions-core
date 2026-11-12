"""Apply one upload to the L2a customer tables (ADR 0002, "Applying an upload"; sanction model, owner 2026-11-11).

The input is already in canonical shape (sources.master_load builds it from the source views). The rules:

- Keyed by the sanction reference number: a sanction in the upload gets every borrower field from it; a blank clears.
- Lists are replaced as a whole per sanction: the holders, the IDs / phones / e-mails / addresses of the borrower and
  holders, guarantors (GS), related parties (RS), securities (SS), cheque dishonours (CD).
- Loan accounts are keyed by account number and point at their sanction; their static fields are overwritten.
- Sanctions and accounts not in the upload are untouched. Nothing is deleted.
- Inside one upload an exact repeat counts once; the same key with different values: the first is used and a warning
  says so (as the upload checks report it), the upload is not blocked.
- Change log: one row per overwritten value (field, old, new, load_id); a replaced list that differs logs '*list*'.
- An upload that clears many previously filled values gets a warning (a truncated file?), not a block.

Everything happens in one transaction: an upload is applied completely or not at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, Table, delete, insert, select, update

from canonical.db import utcnow
from canonical.master.tables import (
    HOLDER_LISTS,
    SANCTION_LISTS,
    dm_change_log,
    dm_sanction,
    dm_sanction_account,
    dm_sanction_holder,
)

CHUNK = 500  # keys per IN (...): SQL Server allows about 2100 parameters
CLEAR_WARN_SHARE = 0.10  # warn when an upload clears at least this share of the values it could clear ...
CLEAR_WARN_MIN = 20  # ... and at least this many
LIST_FIELD = "*list*"
LOG_VALUE_LENGTH = 1000
EXAMPLES = 5

_SYSTEM = {
    "is_active",
    "first_load_id",
    "last_load_id",
    "updated_at",
    "load_id",
    "seq",
    "holder_seq",
    "sanction_ref_key",
}


def _fields(table: Table, *keys: str) -> tuple[str, ...]:
    return tuple(c.name for c in table.columns if c.name not in _SYSTEM and c.name not in keys)


PERSON_FIELDS = _fields(dm_sanction, "sanction_id", "tenant_id", "sanction_reference_number", "party_kind")
HOLDER_FIELDS = _fields(dm_sanction_holder, "sanction_id", "role", "party_kind")
ACCOUNT_FIELDS = _fields(dm_sanction_account, "account_id", "tenant_id", "account_number", "sanction_id")
HOLDER_LIST_FIELDS = {name: _fields(t, "sanction_id") for name, t in HOLDER_LISTS.items()}
SANCTION_LIST_FIELDS = {name: _fields(t, "sanction_id") for name, t in SANCTION_LISTS.items()}


def ref_key(ref: str | None) -> str:
    """The reference key of a sanction number: upper case, letters and digits only (as automn.fn_ref_key)."""
    return re.sub(r"[^A-Z0-9]", "", (ref or "").upper())


class UploadConflict(ValueError):
    """The upload cannot be applied (e.g. a sanction without a reference number): nothing was applied."""


@dataclass(frozen=True)
class PersonIn:
    """The borrower (on the sanction) or another holder: who they are and their contact lists."""

    party_kind: str  # INDIVIDUAL / ENTITY
    values: Mapping[str, Any] = field(default_factory=dict)  # PERSON_FIELDS; a missing field is a blank
    identifiers: Sequence[Mapping[str, Any]] = ()
    phones: Sequence[Mapping[str, Any]] = ()
    emails: Sequence[Mapping[str, Any]] = ()
    addresses: Sequence[Mapping[str, Any]] = ()
    role: str = "BORROWER"  # holders: CO_BORROWER / GUARANTOR


@dataclass(frozen=True)
class SanctionIn:
    sanction_reference_number: str
    borrower: PersonIn
    holders: Sequence[PersonIn] = ()  # other holders, in order (holder_seq 1, 2, ...)
    guarantors: Sequence[Mapping[str, Any]] = ()  # GS rows (dm_sanction_guarantor fields)
    relationships: Sequence[Mapping[str, Any]] = ()  # RS rows
    securities: Sequence[Mapping[str, Any]] = ()  # SS rows
    cheques: Sequence[Mapping[str, Any]] = ()  # CD rows


@dataclass(frozen=True)
class AccountIn:
    account_number: str
    sanction_reference_number: str
    values: Mapping[str, Any] = field(default_factory=dict)  # ACCOUNT_FIELDS


@dataclass
class ApplyResult:
    sanctions_added: int = 0
    sanctions_updated: int = 0
    sanctions_unchanged: int = 0
    accounts_added: int = 0
    accounts_updated: int = 0
    accounts_unchanged: int = 0
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


def _items(rows: Iterable[Mapping[str, Any]], allowed: tuple[str, ...], what: str) -> tuple[tuple[Any, ...], ...]:
    """A list's rows as tuples in order; an exact repeat once."""
    return tuple(dict.fromkeys(tuple(_row(r, allowed, what).values()) for r in rows))


@dataclass(frozen=True)
class _Sanction:
    key: str  # sanction_ref_key
    ref: str  # as written
    row: dict[str, Any]  # party_kind + PERSON_FIELDS
    holders: tuple[tuple[Any, ...], ...]  # (holder_seq, role, party_kind, *HOLDER_FIELDS)
    holder_lists: dict[str, tuple[tuple[Any, ...], ...]]  # name -> (holder_seq, seq, *fields)
    lists: dict[str, tuple[tuple[Any, ...], ...]]  # name -> (seq, *fields)


def _sanction(s: SanctionIn) -> _Sanction:
    ref = _key(s.sanction_reference_number, "A sanction")
    key = _key(ref_key(ref), f"Sanction {ref}: a reference key (letters or digits)")
    b = s.borrower
    row = {"party_kind": _key(b.party_kind, f"Sanction {ref}: party kind"), **_row(b.values, PERSON_FIELDS, ref)}
    holders: list[tuple[Any, ...]] = []
    holder_lists: dict[str, list[tuple[Any, ...]]] = {name: [] for name in HOLDER_LISTS}
    for seq, person in enumerate((b, *s.holders)):
        if seq:
            values = _row(person.values, HOLDER_FIELDS, f"{ref} holder {seq}")
            holders.append((seq, _key(person.role, f"{ref} holder {seq}: role").upper(), person.party_kind,
                            *values.values()))  # fmt: skip
        for name in HOLDER_LISTS:
            items = _items(getattr(person, name), HOLDER_LIST_FIELDS[name], f"{ref} {name}")
            holder_lists[name].extend((seq, i, *item) for i, item in enumerate(items, start=1))
    lists = {
        name: tuple(
            (i, *item)
            for i, item in enumerate(_items(getattr(s, name), SANCTION_LIST_FIELDS[name], f"{ref} {name}"), start=1)
        )
        for name in SANCTION_LISTS
    }
    return _Sanction(key, ref, row, tuple(holders), {k: tuple(v) for k, v in holder_lists.items()}, lists)


def _first_wins[K, V](items: Iterable[V], key: Any, what: str, warnings: list[str]) -> dict[K, V]:
    out: dict[K, V] = {}
    differ: list[str] = []
    for item in items:
        k = key(item)
        if out.setdefault(k, item) != item and str(k) not in differ:
            differ.append(str(k))
    if differ:
        more = f" and {len(differ) - EXAMPLES} more" if len(differ) > EXAMPLES else ""
        warnings.append(f"{what} listed more than once with different values (the first is used): "
                        f"{', '.join(differ[:EXAMPLES])}{more}")  # fmt: skip
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
        self.log.append(dict(tenant_id=self.tenant_id, entity=entity, entity_key=key[:150], field=fld,
                             old_value=_text(old), new_value=_text(new), load_id=self.load_id,
                             changed_at=self.now))  # fmt: skip

    def _compare(self, entity: str, key: str, old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
        changed = False
        for f, v in new.items():
            was = old[f]
            if was is not None and f not in ("party_kind", "sanction_id"):  # required, never blank
                self.result.clearable += 1
            if was != v:
                changed = True
                self._change(entity, key, f, was, v)
                if v is None:
                    self.result.cleared += 1
        return changed

    def _upsert(self, table: Table, id_col: str, key_col: str, rows: dict[str, dict[str, Any]]) -> dict[str, int]:
        """Insert or overwrite master rows by key; returns key -> id and counts (added, updated, unchanged)."""
        existing: dict[str, Any] = {}
        for part in _chunks(list(rows)):
            q = select(table).where(table.c.tenant_id == self.tenant_id, table.c[key_col].in_(part))
            existing.update({r[key_col]: r for r in self.conn.execute(q).mappings()})
        ids: dict[str, int] = {}
        added = updated = 0
        for k, new in rows.items():
            old = existing.get(k)
            if old is None:
                inserted = self.conn.execute(
                    insert(table).values(tenant_id=self.tenant_id, **{key_col: k}, **new, is_active=True,
                                         first_load_id=self.load_id, last_load_id=self.load_id, updated_at=self.now)
                ).inserted_primary_key  # fmt: skip
                assert inserted is not None
                ids[k], added = inserted[0], added + 1
                continue
            ids[k] = old[id_col]
            if self._compare(table.name, k, old, new):
                self.conn.execute(update(table).where(table.c[id_col] == old[id_col])
                                  .values(**new, last_load_id=self.load_id, updated_at=self.now))  # fmt: skip
                updated += 1
        self._counts = (added, updated, len(rows) - added - updated)
        return ids

    def _replace(self, table: Table, sanction_id: int, cols: tuple[str, ...], new: tuple[tuple[Any, ...], ...],
                 label: str, key: str) -> None:  # fmt: skip
        """Replace one sanction's rows of a list table (cols in key order first) if they differ."""
        query = select(*(table.c[c] for c in cols)).where(table.c.sanction_id == sanction_id)
        old = tuple(sorted((tuple(r) for r in self.conn.execute(query)), key=lambda t: t[:2]))
        if old == tuple(sorted(new, key=lambda t: t[:2])):
            return
        if old:
            self.result.clearable += 1
            if not new:
                self.result.cleared += 1
            self._change(table.name, label, LIST_FIELD, old, new)
        self.conn.execute(delete(table).where(table.c.sanction_id == sanction_id))
        if new:
            self.conn.execute(insert(table), [{"sanction_id": sanction_id, "sanction_ref_key": key,
                                               **dict(zip(cols, item, strict=True)),
                                               "load_id": self.load_id} for item in new])  # fmt: skip

    def run(self, sanctions: Sequence[SanctionIn], accounts: Sequence[AccountIn]) -> ApplyResult:
        res = self.result
        ss: dict[str, _Sanction] = _first_wins([_sanction(s) for s in sanctions], lambda s: s.key, "Sanction(s)",
                                               res.warnings)  # fmt: skip
        sanction_ids = self._upsert(dm_sanction, "sanction_id", "sanction_ref_key",
                                    {k: {"sanction_reference_number": s.ref, **s.row} for k, s in ss.items()})  # fmt: skip
        res.sanctions_added, res.sanctions_updated, res.sanctions_unchanged = self._counts
        for key, s in ss.items():
            sid = sanction_ids[key]
            self._replace(dm_sanction_holder, sid, ("holder_seq", "role", "party_kind", *HOLDER_FIELDS), s.holders,
                          key, key)  # fmt: skip
            for name, table in HOLDER_LISTS.items():
                self._replace(table, sid, ("holder_seq", "seq", *HOLDER_LIST_FIELDS[name]), s.holder_lists[name], key,
                              key)  # fmt: skip
            for name, table in SANCTION_LISTS.items():
                self._replace(table, sid, ("seq", *SANCTION_LIST_FIELDS[name]), s.lists[name], key, key)

        accs: dict[str, tuple[str, str, tuple[Any, ...]]] = _first_wins(
            [(_key(a.account_number, "An account"), _key(ref_key(a.sanction_reference_number), f"Account "
              f"{a.account_number}: a sanction"), tuple(_row(a.values, ACCOUNT_FIELDS, a.account_number).values()))
             for a in accounts],
            lambda a: a[0], "Account(s)", res.warnings,
        )  # fmt: skip
        keys = sorted({a[1] for a in accs.values()} - set(sanction_ids))
        for part in _chunks(keys):
            q = select(dm_sanction.c.sanction_ref_key, dm_sanction.c.sanction_id).where(
                dm_sanction.c.tenant_id == self.tenant_id, dm_sanction.c.sanction_ref_key.in_(part)
            )
            sanction_ids.update({r.sanction_ref_key: r.sanction_id for r in self.conn.execute(q)})
        unknown = sorted({a[0] for a in accs.values() if a[1] not in sanction_ids})
        if unknown:
            more = f" and {len(unknown) - EXAMPLES} more" if len(unknown) > EXAMPLES else ""
            res.warnings.append(f"{len(unknown)} account(s) belong to a sanction with no borrower; not saved: "
                                f"{', '.join(unknown[:EXAMPLES])}{more}")  # fmt: skip
        rows = {k: {"sanction_id": sanction_ids[key], "sanction_ref_key": key,
                    **dict(zip(ACCOUNT_FIELDS, values, strict=True))}
                for k, (_, key, values) in accs.items() if key in sanction_ids}  # fmt: skip
        self._upsert(dm_sanction_account, "account_id", "account_number", rows)
        res.accounts_added, res.accounts_updated, res.accounts_unchanged = self._counts

        if self.log:
            self.conn.execute(insert(dm_change_log), self.log)
        res.changes = len(self.log)
        if res.cleared >= CLEAR_WARN_MIN and res.cleared >= CLEAR_WARN_SHARE * res.clearable:
            res.warnings.append(f"This upload clears {res.cleared} of {res.clearable} previously filled values. "
                                "Is the file complete? (applied; the change log has the old values)")  # fmt: skip
        return res


def apply_upload(
    engine: Engine,
    tenant_id: str,
    load_id: int,
    sanctions: Sequence[SanctionIn] = (),
    accounts: Sequence[AccountIn] = (),
    *,
    now: datetime | None = None,
) -> ApplyResult:
    """Apply one upload (load_id, the L1 input_load row) to the tenant's customer tables, in one transaction."""
    with engine.begin() as conn:
        return _Applier(conn, tenant_id, load_id, now or utcnow()).run(sanctions, accounts)
