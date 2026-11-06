"""Custom rules without SQL (K.8): a per-NBFC check built from a few building blocks, compiled to a SQL rule.

A definition names one canonical entity and field, a check, an outcome and a message, and optionally a condition
("only when <field> is one of ..."). It is compiled to the same SQL shape as the built-in rules (row_id,
account_key, party_key, value; filtered on :snapshot_id) and saved in validation_rule with the definition, so it
runs, reports findings and takes per-NBFC policy like any other rule. Field names are taken only from the
canonical model and every value is validated and quoted, so a definition cannot inject SQL.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import Engine, delete, select, text

from app.enums import Outcome, SubmissionType
from app.validation.tables import validation_rule
from bureau_core.canonical import canonical_model
from bureau_core.validation.rules import Rule, _check_rule
from canonical.db import utcnow

CHECKS = {
    "required": "Must not be empty",
    "in_list": "Must be one of a list of values",
    "not_in_list": "Must not be one of a list of values",
    "range": "Must be between a minimum and a maximum",
    "length": "Length (characters) between a minimum and a maximum",
    "pattern": "Must match a pattern (regular expression)",
    "compare": "Compared with another field of the same record",
}
OPERATORS = {
    "<": "less than",
    "<=": "at most",
    "=": "equal to",
    "<>": "different from",
    ">=": "at least",
    ">": "greater than",
}
ENTITIES = (
    "account",
    "account_holder",
    "account_history",
    "party",
    "party_identifier",
    "party_phone",
    "party_email",
    "party_address",
)
ID_RE = re.compile(r"^[A-Z0-9_]{3,40}$")
MAX_VALUES = 200


def quote(value: str) -> str:
    """A standard SQL string literal (T-SQL and SQLite): only the quote is escaped. No N'' prefix (SQLite rejects
    it): rule literals are plain ASCII."""
    return "'" + value.replace("'", "''") + "'"


class CustomRuleError(ValueError):
    """Invalid definition; the message is shown to the user."""


@dataclass(frozen=True)
class Definition:
    name: str  # short name, becomes CUSTOM.<TENANT>.<NAME>
    entity: str
    field: str
    check: str
    outcome: str
    message: str
    values: list[str] = field(default_factory=list)  # in_list / not_in_list
    min: str | None = None  # range / length
    max: str | None = None
    pattern: str | None = None
    operator: str | None = None  # compare
    other_field: str | None = None
    when_field: str | None = None  # optional condition
    when_values: list[str] = field(default_factory=list)

    def rule_id(self, tenant_id: str) -> str:
        return f"CUSTOM.{tenant_id.upper()}.{self.name}"

    def describe(self) -> str:
        what = {
            "required": "is required",
            "in_list": f"is one of {', '.join(self.values)}",
            "not_in_list": f"is not one of {', '.join(self.values)}",
            "range": f"is between {self.min or '…'} and {self.max or '…'}",
            "length": f"has {self.min or 0}–{self.max or '…'} characters",
            "pattern": f"matches {self.pattern}",
            "compare": f"is {OPERATORS.get(self.operator or '', self.operator)} {self.other_field}",
        }[self.check]
        cond = f" (only when {self.when_field} is {', '.join(self.when_values)})" if self.when_field else ""
        return f"{self.entity}.{self.field} {what}{cond}"


def _column(entity: str, name: str):
    edef = canonical_model().entities.get(entity)
    col = next((c for c in edef.columns if c.name == name), None) if edef else None
    if col is None:
        raise CustomRuleError(f"Unknown field {entity}.{name}")
    return col


def fields() -> dict[str, list[tuple[str, str]]]:
    """entity -> [(field, type)] offered in the form."""
    model = canonical_model()
    return {e: [(c.name, c.type) for c in model.entities[e].columns] for e in ENTITIES}


def _literal(col, raw: str, label: str) -> str:
    """A validated SQL literal for a value of this column's type."""
    raw = (raw or "").strip()
    if col.type in ("decimal", "integer"):
        try:
            number = Decimal(raw.replace(",", ""))
        except InvalidOperation:
            raise CustomRuleError(f"{label}: {raw!r} is not a number") from None
        if not number.is_finite() or (col.type == "integer" and number != number.to_integral_value()):
            raise CustomRuleError(f"{label}: {raw!r} is not a {'whole ' if col.type == 'integer' else ''}number")
        return str(number)
    if col.type == "date":
        try:
            return quote(date.fromisoformat(raw).isoformat())
        except ValueError:
            raise CustomRuleError(f"{label}: enter a date as YYYY-MM-DD") from None
    if len(raw) > 100:
        raise CustomRuleError(f"{label}: at most 100 characters")
    return quote(raw)


def _list(col, values: list[str], label: str) -> str:
    values = [v.strip() for v in values if v and v.strip()]
    if not values:
        raise CustomRuleError(f"{label}: enter at least one value")
    if len(values) > MAX_VALUES:
        raise CustomRuleError(f"{label}: at most {MAX_VALUES} values")
    return ", ".join(_literal(col, v, label) for v in dict.fromkeys(values))


def compile_sql(d: Definition) -> str:
    if d.entity not in ENTITIES:
        raise CustomRuleError(f"Unknown record type {d.entity!r}")
    if d.check not in CHECKS:
        raise CustomRuleError(f"Unknown check {d.check!r}")
    col, x = _column(d.entity, d.field), f"x.{d.field}"
    text_type = col.type in ("string", "code")
    if d.check == "required":
        bad = f"({x} IS NULL OR TRIM({x}) = '')" if text_type else f"{x} IS NULL"
    elif d.check in ("in_list", "not_in_list"):
        bad = f"{x} IS NOT NULL AND {x} {'NOT IN' if d.check == 'in_list' else 'IN'} ({_list(col, d.values, 'Values')})"
    elif d.check == "range":
        if not (d.min or d.max):
            raise CustomRuleError("Range: enter a minimum, a maximum or both")
        if text_type:
            raise CustomRuleError("Range works on numbers and dates; use Length or Pattern for text")
        parts = ([f"{x} < {_literal(col, d.min, 'Minimum')}"] if d.min else []) + (
            [f"{x} > {_literal(col, d.max, 'Maximum')}"] if d.max else []
        )
        bad = f"{x} IS NOT NULL AND ({' OR '.join(parts)})"
    elif d.check == "length":
        if not text_type:
            raise CustomRuleError("Length works on text fields")
        limits = []
        for raw, op, label in ((d.min, "<", "Minimum"), (d.max, ">", "Maximum")):
            if raw:
                if not str(raw).strip().isdigit():
                    raise CustomRuleError(f"{label} length: enter a whole number")
                limits.append(f"LEN({x}) {op} {int(raw)}")
        if not limits:
            raise CustomRuleError("Length: enter a minimum, a maximum or both")
        bad = f"{x} IS NOT NULL AND ({' OR '.join(limits)})"
    elif d.check == "pattern":
        pattern = (d.pattern or "").strip()
        if not pattern or len(pattern) > 300:
            raise CustomRuleError("Pattern: enter a regular expression (at most 300 characters)")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise CustomRuleError(f"Pattern: not a valid regular expression ({exc})") from None
        bad = f"{x} IS NOT NULL AND NOT REGEXP_LIKE({x}, {quote(pattern)})"
    else:  # compare
        if d.operator not in OPERATORS:
            raise CustomRuleError("Compare: choose an operator")
        other = _column(d.entity, d.other_field or "")
        if (col.type in ("decimal", "integer")) != (other.type in ("decimal", "integer")) or (col.type == "date") != (
            other.type == "date"
        ):
            raise CustomRuleError(f"Compare: {d.field} and {d.other_field} are of different types")
        y = f"x.{d.other_field}"
        bad = f"{x} IS NOT NULL AND {y} IS NOT NULL AND NOT ({x} {d.operator} {y})"
    if d.when_field:
        when_col = _column(d.entity, d.when_field)
        bad += f" AND x.{d.when_field} IN ({_list(when_col, d.when_values, 'Condition values')})"
    acct, party = canonical_model().entities[d.entity].key_exprs()
    table = canonical_model().entities[d.entity].table
    return (
        f"SELECT x.row_id, {acct} AS account_key, {party} AS party_key, {x} AS value\n"
        f"FROM automn.{table} x\nWHERE x.snapshot_id = :snapshot_id AND {bad}"
    )


def build(tenant_id: str, d: Definition) -> Rule:
    """The validated rule for a definition (raises CustomRuleError)."""
    if not ID_RE.match(d.name or ""):
        raise CustomRuleError("Name: 3-40 characters, A-Z, 0-9 and _ (e.g. MIN_SANCTION)")
    if not (d.message or "").strip() or len(d.message) > 500:
        raise CustomRuleError("Message: enter the text users see in the findings (at most 500 characters)")
    try:
        outcome = Outcome(d.outcome)
    except ValueError:
        raise CustomRuleError("Choose an outcome") from None
    sql = compile_sql(d)  # validates the record type, field and values first
    segment = {
        "party": "PN",
        "party_identifier": "ID",
        "party_phone": "PT",
        "party_email": "EC",
        "party_address": "PA",
        "account": "TL",
        "account_holder": "TL",
        "account_history": "TH",
    }[d.entity]
    rule = Rule(
        rule_id=d.rule_id(tenant_id),
        source="CUSTOM",
        entity=d.entity,
        outcome=outcome,
        message=d.message.strip(),
        segment=segment,
        field=d.field,
        sql=sql,
    )
    return _check_rule(rule)


# ---------------------------------------------------------------- storage
def save(
    engine: Engine,
    tenant_id: str,
    submission_type: SubmissionType | str,
    d: Definition,
    user: str,
    replace_existing: bool = False,
) -> Rule:
    rule = build(tenant_id, d)
    values = dict(
        tenant_id=tenant_id,
        submission_type=str(SubmissionType(submission_type)),
        entity=rule.entity,
        segment=rule.segment,
        field=rule.field,
        outcome=str(rule.outcome),
        message=rule.message,
        sql_text=rule.sql,
        is_enabled=True,
        updated_at=utcnow(),
        definition=json.dumps(asdict(d)),
        created_by=user,
    )
    t = validation_rule
    with engine.begin() as conn:
        exists = conn.execute(select(t.c.rule_id).where(t.c.rule_id == rule.rule_id)).scalar()
        if exists and not replace_existing:
            raise CustomRuleError(f"A rule named {d.name} already exists for this NBFC")
        if exists:
            conn.execute(t.update().where(t.c.rule_id == rule.rule_id).values(**values))
        else:
            conn.execute(t.insert().values(rule_id=rule.rule_id, **values))
    return rule


def delete_rule(engine: Engine, tenant_id: str, rule_id: str) -> bool:
    t = validation_rule
    with engine.begin() as conn:
        return conn.execute(delete(t).where(t.c.rule_id == rule_id, t.c.tenant_id == tenant_id)).rowcount > 0


def definitions(engine: Engine, tenant_id: str, submission_type: SubmissionType | str) -> dict[str, Definition]:
    """rule_id -> definition, for the tenant's form-built rules (hand-written SQL rules have none)."""
    t = validation_rule
    with engine.connect() as conn:
        rows = conn.execute(
            select(t.c.rule_id, t.c.definition).where(
                t.c.tenant_id == tenant_id,
                t.c.submission_type == str(SubmissionType(submission_type)),
                t.c.definition.is_not(None),
            )
        ).all()
    return {r.rule_id: Definition(**json.loads(r.definition)) for r in rows}


def preview(engine: Engine, rule: Rule, snapshot_id: int, parameters: dict | None = None) -> tuple[int, list]:
    """How many findings the rule gives on an existing snapshot, with a few examples (nothing is written)."""
    params = {**(parameters or {}), "snapshot_id": snapshot_id}
    with engine.connect() as conn:
        sql = f"SELECT q.account_key, q.party_key, q.value FROM (\n{rule.sql}\n) q"  # noqa: S608 (a checked rule)
        rows = conn.execute(text(sql), params).all()
    return len(rows), rows[:5]
