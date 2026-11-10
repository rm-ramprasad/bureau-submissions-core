"""Snapshot SQL from the L2a demographics master and the L2b account position (ADR 0002, "Extract"): for format X and
as-of date D, take the master as it is now, keep what X selects, join each selected account with its position for D
(the LMS wins), and copy the result into the snapshot. compile_master returns the same per-entity SELECTs as
sources.views.compile_views, so run_extract (streaming, INSERT ... SELECT, exclusions and overrides) is unchanged.

Selection per format (SELECTION):
  - accounts: active sanctions with a position on D and a BORROWER of the format's kind (INDIVIDUAL / ENTITY);
  - holders: the links whose role the format reports as an account holder; parties: those holders;
  - guarantors and related parties (directors, partners, ...) where the format has them: party + role, flattened
    into the format's guarantor / relationship shape (identifiers, first address, phones).
Keys: party_key = the customer ID; account_key = the account number ("<account>/<sanction>" when the account has
several sanctions), as the source views key accounts today.
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
    and_,
    case,
    cast,
    exists,
    extract,
    func,
    literal,
    literal_column,
    null,
    select,
    true,
)
from sqlalchemy.sql import operators

from canonical.db import SCHEMA
from canonical.master.tables import (
    dm_cheque_dishonour,
    dm_group,
    dm_party,
    dm_party_address,
    dm_party_email,
    dm_party_identifier,
    dm_party_phone,
    dm_party_relative,
    dm_sanction,
    dm_sanction_party,
    dm_security,
)
from canonical.position import POSITION_COLUMNS, VIEW
from canonical.schema import ENTITY_TABLES, MODEL
from sources.views import CompiledViews


@dataclass(frozen=True)
class Selection:
    borrower_kind: str  # dm_party.party_kind of the BORROWER
    holder_roles: tuple[str, ...]  # roles reported as account holders
    guarantors: bool = False  # GUARANTOR links -> the guarantor entity
    relationships: bool = False  # RELATED links -> the relationship entity (borrower x related party)
    groups: bool = False  # MFI: members' relatives and the borrower groups (SHG / JLG) of the selected accounts


SELECTION = {
    "CONSUMER": Selection("INDIVIDUAL", ("BORROWER", "CO_BORROWER", "GUARANTOR")),
    "COMMERCIAL": Selection("ENTITY", ("BORROWER",), guarantors=True, relationships=True),
    "MFI": Selection("INDIVIDUAL", ("BORROWER", "CO_BORROWER"), groups=True),  # ADR 0003
}
GUARANTOR, RELATED, BORROWER = "GUARANTOR", "RELATED", "BORROWER"

# Identifier columns of the guarantor / relationship shapes -> dm_party_identifier.id_type
ID_COLUMNS = {
    "pan": "PAN",
    "voter_id": "VOTER_ID",
    "passport": "PASSPORT",
    "driving_licence": "DRIVING_LICENCE",
    "aadhaar": "AADHAAR",
    "ration_card": "RATION_CARD",
    "company_registration": "COMPANY_REGISTRATION",
    "cin": "CIN",
    "din": "DIN",
    "tin": "TIN",
    "service_tax": "SERVICE_TAX",
    "ckyc": "CKYC",
    "udyam": "UDYAM",
}
MOBILE, FAX, TELEPHONE_TYPES = "MOBILE", "FAX", ("OFFICE", "HOME", "NOT_CLASSIFIED")

_pos_meta = MetaData(schema=SCHEMA)
position = Table(VIEW, _pos_meta, *(Column(c) for c in POSITION_COLUMNS))  # the view, as a selectable


# The run's parameters, left in the SQL text for run_extract to bind (every other value is written into the SQL)
_AS_OF: ColumnElement[Any] = literal_column(":as_of_date")
_TENANT: ColumnElement[Any] = literal_column(":tenant_id")


class MasterError(ValueError):
    pass


def _typed_null(entity: str, column: str) -> ColumnElement[Any]:
    return cast(null(), ENTITY_TABLES[entity].c[column].type).label(column)


class _Builder:
    def __init__(self, selection: Selection, submission_type: str) -> None:
        self.sel, self.submission_type = selection, submission_type
        s, p = dm_sanction.alias("s"), position.alias("p")
        self.s, self.p = s, p
        borrower = exists().where(
            dm_sanction_party.c.sanction_id == s.c.sanction_id,
            dm_sanction_party.c.role == BORROWER,
            dm_party.c.party_id == dm_sanction_party.c.party_id,
            dm_party.c.party_kind == selection.borrower_kind,
        )
        self.account_key = case(
            (s.c.sanction_number == "", s.c.account_number),
            else_=s.c.account_number + literal("/") + s.c.sanction_number,
        )
        # the selected sanctions with their position on D
        self.selected: FromClause = (
            select(s, *(p.c[c].label(f"pos_{c}") for c in POSITION_COLUMNS), self.account_key.label("account_key"))
            .select_from(
                s.join(
                    p,
                    and_(
                        p.c.account_number == s.c.account_number,
                        p.c.sanction_number == s.c.sanction_number,
                        p.c.as_of_date == _AS_OF,
                        p.c.lms_type.in_((self.submission_type, "")),  # the format's own LMS view, or an upload
                    ),
                )
            )
            .where(s.c.tenant_id == _TENANT, s.c.is_active == true(), borrower)
            .subquery("a")
        )

    # ------------------------------------------------------------------ accounts and holders
    def account(self) -> Select[Any]:
        a = self.selected
        cols: list[ColumnElement[Any]] = []
        for c in MODEL.entities["account"].columns:
            name = c.name
            if name == "account_key":
                cols.append(a.c.account_key)
            elif name in POSITION_COLUMNS and name in dm_sanction.c and name != "account_number":
                cols.append(func.coalesce(a.c[f"pos_{name}"], a.c[name]).label(name))  # the LMS wins
            elif name in POSITION_COLUMNS and name != "account_number":
                cols.append(a.c[f"pos_{name}"].label(name))
            elif name in dm_sanction.c:
                cols.append(a.c[name].label(name))
            else:
                cols.append(_typed_null("account", name))
        return select(*cols).order_by(a.c.account_key)

    def _links(self, roles: tuple[str, ...], name: str = "l") -> FromClause:
        a, sp, pt = self.selected, dm_sanction_party.alias("sp"), dm_party.alias("pt")
        return (
            select(a.c.account_key, a.c.sanction_id, sp, pt.c.customer_id)
            .select_from(a.join(sp, sp.c.sanction_id == a.c.sanction_id).join(pt, pt.c.party_id == sp.c.party_id))
            .where(sp.c.role.in_(roles))
            .subquery(name)
        )

    def account_holder(self) -> Select[Any]:
        h = self._links(self.sel.holder_roles)
        return (
            select(h.c.account_key, h.c.customer_id.label("party_key"), h.c.ownership_indicator)
            .distinct()
            .order_by(h.c.account_key, h.c.customer_id)
        )

    def _holder_ids(self) -> Select[Any]:
        h = self._links(self.sel.holder_roles)
        return select(h.c.party_id).distinct()

    # ------------------------------------------------------------------ parties and their lists
    def party(self) -> Select[Any]:
        pt = dm_party
        cols: list[ColumnElement[Any]] = [
            pt.c.customer_id.label("party_key")
            if c.name == "party_key"
            else (pt.c[c.name].label(c.name) if c.name in pt.c else _typed_null("party", c.name))
            for c in MODEL.entities["party"].columns
        ]
        return select(*cols).where(pt.c.party_id.in_(self._holder_ids())).order_by(pt.c.customer_id)

    def party_list(self, entity: str, table: Table) -> Select[Any]:
        pt = dm_party
        cols: list[ColumnElement[Any]] = [
            pt.c.customer_id.label("party_key")
            if c.name == "party_key"
            else (table.c[c.name].label(c.name) if c.name in table.c else _typed_null(entity, c.name))
            for c in MODEL.entities[entity].columns
        ]
        return (
            select(*cols)
            .select_from(table.join(pt, pt.c.party_id == table.c.party_id))
            .where(pt.c.party_id.in_(self._holder_ids()))
            .order_by(pt.c.customer_id, table.c.seq)
        )

    # ------------------------------------------------------------------ guarantors and related parties
    def _person(self, entity: str, party_id: ColumnElement[Any], rp: FromClause) -> dict[str, ColumnElement[Any]]:
        """The guarantor / relationship shape's columns of one party (rp): its own fields, identifiers by type,
        first address, phones."""
        i, ph = dm_party_identifier, dm_party_phone
        ad = (
            select(dm_party_address, func.row_number().over(
                partition_by=dm_party_address.c.party_id,
                order_by=(dm_party_address.c.priority, dm_party_address.c.seq),
            ).label("rn"))
            .subquery("ad")
        )  # fmt: skip

        def first_id(id_type: str) -> ColumnElement[Any]:
            q = select(i.c.id_number).where(i.c.party_id == party_id, i.c.id_type == id_type).order_by(i.c.seq)
            return q.limit(1).scalar_subquery()

        def first_phone(col: str, types: tuple[str, ...]) -> ColumnElement[Any]:
            q = select(ph.c[col]).where(ph.c.party_id == party_id, ph.c.phone_type.in_(types))
            return q.order_by(ph.c.priority, ph.c.seq).limit(1).scalar_subquery()

        def address(col: str) -> ColumnElement[Any]:
            return select(ad.c[col]).where(ad.c.party_id == party_id, ad.c.rn == 1).scalar_subquery()

        out: dict[str, ColumnElement[Any]] = {
            "related_key": rp.c.customer_id,
            "related_type": rp.c.party_type,
            "mobile": first_phone("phone_number", (MOBILE,)),
            "telephone": first_phone("phone_number", TELEPHONE_TYPES),
            "telephone_area_code": first_phone("area_code", TELEPHONE_TYPES),
            "fax": first_phone("phone_number", (FAX,)),
            "fax_area_code": first_phone("area_code", (FAX,)),
        }
        for c in MODEL.entities[entity].columns:
            if c.name in out:
                continue
            if c.name in ID_COLUMNS:
                out[c.name] = first_id(ID_COLUMNS[c.name])
            elif c.name in ("address_line_1", "address_line_2", "address_line_3", "city", "district", "state_code",
                            "pin_code", "country"):  # fmt: skip
                out[c.name] = address(c.name)
            elif c.name in dm_party.c:
                out[c.name] = rp.c[c.name]
        return out

    def guarantor(self) -> Select[Any]:
        g, rp = self._links((GUARANTOR,)), dm_party.alias("rp")
        person = self._person("guarantor", rp.c.party_id, rp)
        cols: list[ColumnElement[Any]] = []
        for c in MODEL.entities["guarantor"].columns:
            if c.name == "account_key":
                cols.append(g.c.account_key)
            elif c.name == "guarantee_invocation_date":
                cols.append(g.c.guarantee_invocation_date)
            elif c.name in person:
                cols.append(person[c.name].label(c.name))
            else:
                cols.append(_typed_null("guarantor", c.name))
        return (
            select(*cols)
            .select_from(g.join(rp, rp.c.party_id == g.c.party_id))
            .order_by(g.c.account_key, rp.c.customer_id)
        )

    def relationship(self) -> Select[Any]:
        """borrower x related party, once per pair (the link is kept per sanction of the borrower)."""
        b, r, rp = self._links((BORROWER,), "lb"), self._links((RELATED,), "lr"), dm_party.alias("rp")
        person = self._person("relationship", rp.c.party_id, rp)
        cols: list[ColumnElement[Any]] = []
        for c in MODEL.entities["relationship"].columns:
            if c.name == "party_key":
                cols.append(b.c.customer_id.label("party_key"))
            elif c.name in ("relationship_type", "control_percentage"):
                cols.append(r.c[c.name])
            elif c.name in person:
                cols.append(person[c.name].label(c.name))
            else:
                cols.append(_typed_null("relationship", c.name))
        return (
            select(*cols)
            .distinct()
            .select_from(b.join(r, r.c.sanction_id == b.c.sanction_id).join(rp, rp.c.party_id == r.c.party_id))
            .order_by(b.c.customer_id, rp.c.customer_id)
        )

    # ------------------------------------------------------------------ account children
    def account_rows(self, entity: str, table: Table, order: str) -> Select[Any]:
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
            .order_by(a.c.account_key, table.c[order])
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
                        h.c.sanction_number == a.c.sanction_number,
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

    def borrower_group(self) -> Select[Any]:
        """The groups (SHG / JLG) the selected accounts belong to (MFI)."""
        a, g = self.selected, dm_group
        cols: list[ColumnElement[Any]] = [
            g.c[c.name].label(c.name) if c.name in g.c else _typed_null("borrower_group", c.name)
            for c in MODEL.entities["borrower_group"].columns
        ]
        in_selection = select(a.c.group_key).where(a.c.group_key.is_not(None))
        return select(*cols).where(g.c.tenant_id == _TENANT, g.c.group_key.in_(in_selection)).order_by(g.c.group_key)

    def statements(self, history: bool) -> dict[str, Select[Any]]:
        out: dict[str, Select[Any]] = {
            "party": self.party(),
            "party_identifier": self.party_list("party_identifier", dm_party_identifier),
            "party_phone": self.party_list("party_phone", dm_party_phone),
            "party_email": self.party_list("party_email", dm_party_email),
            "party_address": self.party_list("party_address", dm_party_address),
            "account": self.account(),
            "account_holder": self.account_holder(),
        }
        if history:
            out["account_history"] = self.account_history()
        if self.sel.relationships:
            out["relationship"] = self.relationship()
        if self.sel.guarantors:
            out["guarantor"] = self.guarantor()
        if self.sel.groups:
            out["party_relative"] = self.party_list("party_relative", dm_party_relative)
            out["borrower_group"] = self.borrower_group()
        out["security"] = self.account_rows("security", dm_security, "security_ref")
        out["cheque_dishonour"] = self.account_rows("cheque_dishonour", dm_cheque_dishonour, "seq")
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
