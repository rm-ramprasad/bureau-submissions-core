"""Snapshot SQL from the L2a customer tables and the L2b account position (ADR 0002, "Extract"; sanction model, owner
2026-11-11): for format X and as-of date D, take the customer tables as they are now, keep what X selects, join each
selected loan account with its position for D (the LMS wins), and copy the result into the snapshot. compile_master
returns the same per-entity SELECTs as sources.views.compile_views, so run_extract is unchanged.

Selection per format (SELECTION): accounts = active accounts of active sanctions whose borrower is of the format's kind
(INDIVIDUAL / ENTITY), with a position on D. Keys: account_key = the account number; party_key = the sanction
reference number for the borrower (one borrower per sanction: a commercial sanction with several accounts is one BS
with a CR per account), <reference>#<n> for the sanction's other holders (consumer). Guarantors (GS), securities (SS)
and cheque dishonours (CD) are reported on each account of the sanction; related parties (RS) once per borrower.
Account history (optional, per client; owner 2026-11-09: off for rarfincare): the account's earlier positions, the
latest one in each month before D.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import (
    Column,
    ColumnElement,
    Engine,
    FromClause,
    MetaData,
    Select,
    Table,
    UnaryExpression,
    Unicode,
    and_,
    case,
    cast,
    extract,
    func,
    literal,
    literal_column,
    null,
    or_,
    select,
    true,
)
from sqlalchemy.sql import operators

from canonical.db import SCHEMA
from canonical.master.tables import (
    HOLDER_LISTS,
    dm_sanction,
    dm_sanction_account,
    dm_sanction_cheque,
    dm_sanction_guarantor,
    dm_sanction_holder,
    dm_sanction_relationship,
    dm_sanction_security,
)
from canonical.position import POSITION_COLUMNS, VIEW
from canonical.schema import ENTITY_TABLES, MODEL
from sources.views import CompiledViews


@dataclass(frozen=True)
class Selection:
    borrower_kind: str  # dm_sanction.party_kind of the borrower
    holder_roles: tuple[str, ...] = ()  # other holders reported as account holders (besides the borrower)
    guarantors: bool = False  # GS rows -> the guarantor entity, per account
    relationships: bool = False  # RS rows -> the relationship entity, per borrower


SELECTION = {
    "CONSUMER": Selection("INDIVIDUAL", ("CO_BORROWER", "GUARANTOR")),
    "COMMERCIAL": Selection("ENTITY", guarantors=True, relationships=True),
    # MFI: not on the customer tables yet (owner 2026-11-11: MFI later)
}

_pos_meta = MetaData(schema=SCHEMA)
position = Table(VIEW, _pos_meta, *(Column(c) for c in POSITION_COLUMNS))  # the view, as a selectable


# The run's parameters, left in the SQL text for run_extract to bind (every other value is written into the SQL)
_AS_OF: ColumnElement[Any] = literal_column(":as_of_date")
_TENANT: ColumnElement[Any] = literal_column(":tenant_id")


class MasterError(ValueError):
    pass


def _typed_null(entity: str, column: str) -> ColumnElement[Any]:
    return cast(null(), ENTITY_TABLES[entity].c[column].type).label(column)


def _party_key(ref: ColumnElement[Any], holder_seq: ColumnElement[Any]) -> ColumnElement[Any]:
    """The borrower: the sanction reference number; another holder: <reference>#<holder_seq>."""
    return case((holder_seq == 0, ref), else_=ref + literal("#") + cast(holder_seq, Unicode(10)))


class _Builder:
    def __init__(self, selection: Selection, submission_type: str) -> None:
        self.sel, self.submission_type = selection, submission_type
        a, s, p = dm_sanction_account.alias("acc"), dm_sanction.alias("s"), position.alias("p")
        # the selected accounts (active, of a borrower of the format's kind) with their position on D
        self.selected: FromClause = (
            select(a, s.c.sanction_reference_number, s.c.ownership_indicator.label("borrower_ownership"),
                   *(p.c[c].label(f"pos_{c}") for c in POSITION_COLUMNS), a.c.account_number.label("account_key"))
            .select_from(
                a.join(s, s.c.sanction_id == a.c.sanction_id).join(p, and_(
                    p.c.account_number == a.c.account_number,
                    p.c.as_of_date == _AS_OF,
                    p.c.lms_type.in_((self.submission_type, "")),  # the format's own LMS view, or an upload
                ))
            )
            .where(a.c.tenant_id == _TENANT, a.c.is_active == true(), s.c.is_active == true(),
                   s.c.party_kind == selection.borrower_kind)
            .subquery("a")
        )  # fmt: skip

    def _sanction_ids(self) -> Select[Any]:
        return select(self.selected.c.sanction_id).distinct()

    def _holder_seqs(self, sanction_id: ColumnElement[Any]) -> Select[Any]:
        h = dm_sanction_holder
        return select(h.c.holder_seq).where(
            h.c.sanction_id == sanction_id, h.c.role.in_(self.sel.holder_roles or ("",))
        )

    # ------------------------------------------------------------------ accounts and holders
    def account(self) -> Select[Any]:
        a = self.selected
        cols: list[ColumnElement[Any]] = []
        for c in MODEL.entities["account"].columns:
            name = c.name
            if name == "account_key":
                cols.append(a.c.account_key)
            elif name in POSITION_COLUMNS and name in dm_sanction_account.c and name != "account_number":
                cols.append(func.coalesce(a.c[f"pos_{name}"], a.c[name]).label(name))  # the LMS wins
            elif name in POSITION_COLUMNS and name != "account_number":
                cols.append(a.c[f"pos_{name}"].label(name))
            elif name in dm_sanction_account.c:
                cols.append(a.c[name].label(name))
            else:
                cols.append(_typed_null("account", name))
        return select(*cols).order_by(a.c.account_key)

    def account_holder(self) -> Select[Any]:
        a, h = self.selected, dm_sanction_holder.alias("h")
        borrower = select(a.c.account_key, a.c.sanction_reference_number.label("party_key"),
                          a.c.borrower_ownership.label("ownership_indicator"))  # fmt: skip
        holders = (
            select(a.c.account_key, _party_key(a.c.sanction_reference_number, h.c.holder_seq).label("party_key"),
                   h.c.ownership_indicator)
            .select_from(a.join(h, h.c.sanction_id == a.c.sanction_id))
            .where(h.c.role.in_(self.sel.holder_roles or ("",)))
        )  # fmt: skip
        u = borrower.union(holders).subquery("u")
        return select(u.c.account_key, u.c.party_key, u.c.ownership_indicator).order_by(u.c.account_key, u.c.party_key)

    # ------------------------------------------------------------------ parties and their lists
    def party(self) -> Select[Any]:
        s, h = dm_sanction.alias("s"), dm_sanction_holder.alias("h")

        def cols(src: FromClause, key: ColumnElement[Any]) -> list[ColumnElement[Any]]:
            return [key.label("party_key") if c.name == "party_key" else
                    (src.c[c.name].label(c.name) if c.name in src.c else _typed_null("party", c.name))
                    for c in MODEL.entities["party"].columns]  # fmt: skip

        borrowers = select(*cols(s, s.c.sanction_reference_number)).where(s.c.sanction_id.in_(self._sanction_ids()))
        holders = (
            select(*cols(h, _party_key(s.c.sanction_reference_number, h.c.holder_seq)))
            .select_from(h.join(s, s.c.sanction_id == h.c.sanction_id))
            .where(h.c.sanction_id.in_(self._sanction_ids()), h.c.role.in_(self.sel.holder_roles or ("",)))
        )  # fmt: skip
        u = borrowers.union_all(holders).subquery("u")
        return select(*(u.c[c.name] for c in MODEL.entities["party"].columns)).order_by(u.c.party_key)

    def party_list(self, entity: str, table: Table) -> Select[Any]:
        s = dm_sanction.alias("s")
        key = _party_key(s.c.sanction_reference_number, table.c.holder_seq)
        cols: list[ColumnElement[Any]] = [
            key.label("party_key")
            if c.name == "party_key"
            else (table.c[c.name].label(c.name) if c.name in table.c else _typed_null(entity, c.name))
            for c in MODEL.entities[entity].columns
        ]
        return (
            select(*cols)
            .select_from(table.join(s, s.c.sanction_id == table.c.sanction_id))
            .where(table.c.sanction_id.in_(self._sanction_ids()),
                   or_(table.c.holder_seq == 0, table.c.holder_seq.in_(self._holder_seqs(table.c.sanction_id))))
            .order_by(key, table.c.seq)
        )  # fmt: skip

    # ------------------------------------------------------------------ GS, RS, SS, CD
    def per_account(self, entity: str, table: Table, *order: str) -> Select[Any]:
        a = self.selected
        cols: list[ColumnElement[Any]] = [
            a.c.account_key
            if c.name == "account_key"
            else (table.c[c.name].label(c.name) if c.name in table.c else _typed_null(entity, c.name))
            for c in MODEL.entities[entity].columns
        ]
        return (
            select(*cols)
            .select_from(table.join(a, a.c.sanction_id == table.c.sanction_id))
            .order_by(a.c.account_key, *(table.c[o] for o in order))
        )

    def relationship(self) -> Select[Any]:
        """The borrower's related parties, once per sanction."""
        s, r = dm_sanction.alias("s"), dm_sanction_relationship
        cols: list[ColumnElement[Any]] = [
            s.c.sanction_reference_number.label("party_key")
            if c.name == "party_key"
            else (r.c[c.name].label(c.name) if c.name in r.c else _typed_null("relationship", c.name))
            for c in MODEL.entities["relationship"].columns
        ]
        return (
            select(*cols)
            .select_from(r.join(s, s.c.sanction_id == r.c.sanction_id))
            .where(r.c.sanction_id.in_(self._sanction_ids()))
            .order_by(s.c.sanction_reference_number, r.c.related_key, r.c.seq)
        )

    def account_history(self) -> Select[Any]:
        """Earlier positions of the selected accounts: the latest in each month before D."""
        a, h = self.selected, position.alias("h")
        ranked = (
            select(
                a.c.account_key,
                h,
                func.row_number()
                .over(
                    partition_by=(a.c.account_key, extract("year", h.c.as_of_date), extract("month", h.c.as_of_date)),
                    order_by=h.c.as_of_date.desc(),
                )
                .label("rn"),
            )
            .select_from(
                a.join(
                    h,
                    and_(
                        h.c.account_number == a.c.account_number,
                        h.c.as_of_date < _AS_OF,
                        h.c.lms_type.in_((self.submission_type, "")),
                    ),
                )
            )
            .subquery("x")
        )
        cols: list[ColumnElement[Any]] = []
        for c in MODEL.entities["account_history"].columns:
            if c.name == "account_key":
                cols.append(ranked.c.account_key)
            elif c.name == "history_month":
                cols.append(ranked.c.as_of_date.label("history_month"))
            elif c.name in POSITION_COLUMNS:
                cols.append(ranked.c[c.name])
            else:
                cols.append(_typed_null("account_history", c.name))
        return select(*cols).where(ranked.c.rn == 1).order_by(ranked.c.account_key, ranked.c.as_of_date.desc())

    def statements(self, history: bool) -> dict[str, Select[Any]]:
        out: dict[str, Select[Any]] = {
            "party": self.party(),
            **{entity: self.party_list(entity, HOLDER_LISTS[name]) for name, entity in
               (("identifiers", "party_identifier"), ("phones", "party_phone"), ("emails", "party_email"),
                ("addresses", "party_address"))},
            "account": self.account(),
            "account_holder": self.account_holder(),
        }  # fmt: skip
        if history:
            out["account_history"] = self.account_history()
        if self.sel.relationships:
            out["relationship"] = self.relationship()
        if self.sel.guarantors:
            out["guarantor"] = self.per_account("guarantor", dm_sanction_guarantor, "related_key", "seq")
        out["security"] = self.per_account("security", dm_sanction_security, "seq")
        out["cheque_dishonour"] = self.per_account("cheque_dishonour", dm_sanction_cheque, "seq")
        return out


def compile_master(engine: Engine, submission_type: str, *, history: bool = False) -> CompiledViews:
    """The per-entity SELECTs for one format over the demographics master and the account position, with the
    parameters :tenant_id and :as_of_date. `history`: account history from earlier positions (a client setting)."""
    selection = SELECTION.get(str(submission_type).upper())
    if selection is None:
        raise MasterError(f"No selection defined for {submission_type} (sources.master.SELECTION)")
    sql, order = {}, {}
    for entity, stmt in _Builder(selection, str(submission_type).upper()).statements(history).items():
        unordered, order[entity] = _order_outside(stmt)
        sql[entity] = str(unordered.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
    fingerprint = hashlib.sha256("\n".join(f"{e}\n{q}" for e, q in sql.items()).encode()).hexdigest()[:16]
    return CompiledViews(sql, f"master-{fingerprint}", order)


def _order_outside(stmt: Select[Any]) -> tuple[Select[Any], str]:
    """The SELECT without its ORDER BY, its sort keys added as columns _o0, _o1, ..., and the ORDER BY over them
    (SQL Server allows no ORDER BY in the derived table run_extract wraps around each SELECT)."""
    keys, parts = [], []
    for i, clause in enumerate(stmt._order_by_clauses):
        desc = isinstance(clause, UnaryExpression) and clause.modifier is operators.desc_op
        expr = clause.element if desc and isinstance(clause, UnaryExpression) else clause
        keys.append(expr.label(f"_o{i}"))
        parts.append(f"q._o{i}" + (" DESC" if desc else ""))
    return stmt.order_by(None).add_columns(*keys), ", ".join(parts)
