"""Rules that SQL cannot express well. Each reads canonical rows for one snapshot and yields findings.

Register a function in PYTHON_RULES and reference it by name from a rule pack (`python: <name>`).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import Connection, select

from bureau_core.checks.identifiers import id_number_problem
from bureau_core.checks.phones import valid_numbers
from bureau_core.validation.checks.addresses import pin_format_problem, pin_ranges, pin_state_problem
from bureau_core.validation.checks.emails import email_problem
from bureau_core.validation.checks.names import DEFAULT_SALUTATION_PATTERN, check_name
from bureau_core.validation.tables import ENTITY_TABLES


@dataclass(frozen=True)
class PyFinding:
    row_id: int
    party_key: str | None
    account_key: str | None = None
    value: object = None
    detail: str | None = None  # appended to the rule message


@dataclass
class RuleContext:
    conn: Connection
    snapshot_id: int
    as_of_date: date
    params: dict = field(default_factory=dict)  # rule parameters (pack defaults + tenant overrides)
    _cache: dict = field(default_factory=dict)

    def rows(self, entity: str) -> list:
        if entity not in self._cache:
            t = ENTITY_TABLES[entity]
            self._cache[entity] = self.conn.execute(select(t).where(t.c.snapshot_id == self.snapshot_id)).all()
        return self._cache[entity]


RuleFunc = Callable[[RuleContext], Iterator[PyFinding]]
PYTHON_RULES: dict[str, RuleFunc] = {}


def register(name: str):
    def wrap(fn: RuleFunc) -> RuleFunc:
        PYTHON_RULES[name] = fn
        return fn

    return wrap


def _names(ctx: RuleContext, predicate) -> Iterator[PyFinding]:
    salutations = ctx.params.get("name_salutation_pattern", DEFAULT_SALUTATION_PATTERN)
    for p in ctx.rows("party"):
        if p.full_name and predicate(check_name(p.full_name, salutations)):
            yield PyFinding(p.row_id, p.party_key, value=p.full_name)


@register("name_digits")
def name_digits(ctx):
    yield from _names(ctx, lambda c: c.has_digits)


@register("name_disallowed_chars")
def name_disallowed_chars(ctx):
    yield from _names(ctx, lambda c: bool(c.disallowed_chars))


@register("name_no_valid_token")
def name_no_valid_token(ctx):
    yield from _names(ctx, lambda c: not c.has_valid_token)


@register("name_starts_with_descriptor")
def name_starts_with_descriptor(ctx):
    yield from _names(ctx, lambda c: c.starts_with_descriptor)


@register("name_single_word")
def name_single_word(ctx):
    yield from _names(ctx, lambda c: c.word_count == 1)


@register("id_number_format")
def id_number_format(ctx):
    for i in ctx.rows("party_identifier"):
        problem = id_number_problem(i.id_type, i.id_number, ctx.params) if i.id_number else None
        if problem:
            yield PyFinding(i.row_id, i.party_key, value=i.id_number, detail=problem)


@register("phone_number_invalid")
def phone_number_invalid(ctx):
    for ph in ctx.rows("party_phone"):
        if ph.phone_number and not valid_numbers(ph.phone_number, ph.phone_type):
            yield PyFinding(ph.row_id, ph.party_key, value=ph.phone_number)


@register("email_invalid")
def email_invalid(ctx):
    for e in ctx.rows("party_email"):
        problem = email_problem(e.email) if e.email else None
        if problem:
            yield PyFinding(e.row_id, e.party_key, value=e.email, detail=problem)


@register("pin_format")
def pin_format(ctx):
    for a in ctx.rows("party_address"):
        problem = pin_format_problem(a.pin_code, a.state_code) if a.pin_code else None
        if problem:
            yield PyFinding(a.row_id, a.party_key, value=a.pin_code, detail=problem)


@register("pin_state_mismatch")
def pin_state_mismatch(ctx):
    ranges = pin_ranges(ctx.conn)
    for a in ctx.rows("party_address"):
        problem = pin_state_problem(a.pin_code, a.state_code, ranges) if a.pin_code else None
        if problem:
            yield PyFinding(a.row_id, a.party_key, value=a.pin_code, detail=problem)


# ---------------------------------------------------------------- commercial (types/commercial/rules.yaml)
# Canonical values the logic depends on (decided to stay in code, like MOBILE / FOREIGN above):
INDIVIDUAL_CONSTITUTION = "INDIVIDUAL"  # an individual reported commercially (8.1 code 90)
INDIVIDUAL_RELATED_TYPES = {"INDIAN_INDIVIDUAL", "FOREIGN_INDIVIDUAL"}
RELATED_ENTITIES = ("relationship", "guarantor")
RELATED_IDS = {
    "pan": "PAN",
    "voter_id": "VOTER_ID",
    "passport": "PASSPORT",
    "cin": "CIN",
    "din": "DIN",
    "ckyc": "CKYC",
    "udyam": "UDYAM",
}  # inline column -> canonical id_type for its format
MEANINGLESS_ADDRESS = re.compile(r"^(NULL|NA|N\.A\.?|[\W_]|[0-9])*$", re.IGNORECASE)


def _name_problem(name: str, salutations: str) -> str | None:
    """Annexure C part C, for individuals: digits, disallowed characters, no 2-letter word, starts with S/O ..."""
    c = check_name(name, salutations)
    if c.has_digits:
        return "digits in the name"
    if c.disallowed_chars:
        return f"disallowed characters {c.disallowed_chars}"
    if not c.has_valid_token:
        return "no word with 2 or more letters"
    if c.starts_with_descriptor:
        return "starts with a relationship descriptor (S/O, W/O, H/O, D/O)"
    return None


def _party_account(ctx: RuleContext, entity: str, row) -> tuple[str | None, str | None]:
    """(party_key, account_key) a related-party row points at: the borrower of a relationship, the facility of a
    guarantor."""
    return (row.party_key, None) if entity == "relationship" else (None, row.account_key)


@register("borrower_name")
def borrower_name(ctx):
    """Business names need 2 alphanumeric characters; an individual reported commercially gets the name rules."""
    salutations = ctx.params.get("name_salutation_pattern", DEFAULT_SALUTATION_PATTERN)
    for p in ctx.rows("party"):
        name = p.full_name or ""
        if p.legal_constitution == INDIVIDUAL_CONSTITUTION:
            problem = _name_problem(name, salutations) if name.strip() else None
        else:
            problem = (
                None if sum(ch.isalnum() for ch in name) >= 2 or not name.strip() else "fewer than 2 letters or digits"
            )
        if problem:
            yield PyFinding(p.row_id, p.party_key, value=name, detail=problem)


def _address_meaningless(entity: str):
    def rule(ctx):
        for r in ctx.rows(entity):
            lines = " ".join(x for x in (r.address_line_1, r.address_line_2, r.address_line_3) if x)
            if lines.strip() and MEANINGLESS_ADDRESS.match(lines.replace(" ", "")):
                party, account = _party_account(ctx, entity, r) if entity in RELATED_ENTITIES else (r.party_key, None)
                yield PyFinding(r.row_id, party, account, value=lines)

    return rule


def _related_name(entity: str):
    def rule(ctx):
        salutations = ctx.params.get("name_salutation_pattern", DEFAULT_SALUTATION_PATTERN)
        for r in ctx.rows(entity):
            if r.related_type in INDIVIDUAL_RELATED_TYPES and r.full_name:
                problem = _name_problem(r.full_name, salutations)
                if problem:
                    yield PyFinding(r.row_id, *_party_account(ctx, entity, r), value=r.full_name, detail=problem)

    return rule


def _related_id(entity: str, column: str, id_type: str):
    def rule(ctx):
        for r in ctx.rows(entity):
            number = getattr(r, column)
            problem = id_number_problem(id_type, number, ctx.params) if number else None
            if problem:
                yield PyFinding(r.row_id, *_party_account(ctx, entity, r), value=number, detail=problem)

    return rule


def _related_pin(entity: str, check: str):
    def rule(ctx):
        ranges = pin_ranges(ctx.conn) if check == "state" else None
        for r in ctx.rows(entity):
            if not r.pin_code:
                continue
            problem = (
                pin_state_problem(r.pin_code, r.state_code, ranges)
                if check == "state"
                else pin_format_problem(r.pin_code, r.state_code)
            )
            if problem:
                yield PyFinding(r.row_id, *_party_account(ctx, entity, r), value=r.pin_code, detail=problem)

    return rule


for _entity in ("party_address", *RELATED_ENTITIES):
    register(f"{_entity}_address_meaningless")(_address_meaningless(_entity))
for _entity in RELATED_ENTITIES:
    register(f"{_entity}_name")(_related_name(_entity))
    register(f"{_entity}_pin_format")(_related_pin(_entity, "format"))
    register(f"{_entity}_pin_state_mismatch")(_related_pin(_entity, "state"))
    for _column, _id_type in RELATED_IDS.items():
        register(f"{_entity}_{_column}_format")(_related_id(_entity, _column, _id_type))


# ---------------------------------------------------------------- MFI (ADR 0003 step 5); generic where possible
def _age(born: date, on: date) -> int:
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


@register("min_age_at_as_of")
def min_age_at_as_of(ctx):
    """Borrowers younger than the parameter min_member_age on the as-of date (MFI CNSCRD #10: 16)."""
    minimum = int(ctx.params.get("min_member_age", 16))
    for p in ctx.rows("party"):
        if p.birth_date is not None and _age(p.birth_date, ctx.as_of_date) < minimum:
            yield PyFinding(
                p.row_id, p.party_key, value=p.birth_date, detail=f"age {_age(p.birth_date, ctx.as_of_date)}"
            )


NAME_FIELD_WIDTHS = (100, 50, 50)  # MFI CNSCRD #6-#8


@register("name_fits_100_50_50")
def name_fits_100_50_50(ctx):
    """A name that cannot be packed word by word into fields of 100, 50 and 50 characters (MFI 7.1: never split a
    word, never truncate)."""
    for p in ctx.rows("party"):
        if not p.full_name:
            continue
        parts, i = [""] * len(NAME_FIELD_WIDTHS), 0
        fits = True
        for word in str(p.full_name).split():
            while i < len(parts) and len(f"{parts[i]} {word}".strip()) > NAME_FIELD_WIDTHS[i]:
                i += 1
            if i == len(parts):
                fits = False
                break
            parts[i] = f"{parts[i]} {word}".strip()
        if not fits:
            yield PyFinding(p.row_id, p.party_key, value=p.full_name)


@register("group_name_no_valid_token")
def group_name_no_valid_token(ctx):
    """Accounts whose borrower group's name has no word of 2 or more letters (MFI GRPCRD #3): the group's records
    are rejected with it (2.7), so the finding is on the account."""
    groups = {g.group_key: g for g in ctx.rows("borrower_group")}
    for a in ctx.rows("account"):
        g = groups.get(a.group_key)
        if g is not None and not any(len(re.findall(r"[A-Za-z]", w)) >= 2 for w in str(g.full_name or "").split()):
            yield PyFinding(a.row_id, None, account_key=a.account_key, value=g.full_name)
