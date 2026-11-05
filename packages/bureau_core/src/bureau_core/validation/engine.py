"""Validate one canonical snapshot: run every enabled rule, decide each record (cascade), summarise.

Results go to validation_finding, record_outcome and validation_summary. Re-running replaces the previous
results for the snapshot, so a snapshot can be re-validated after a policy change.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from sqlalchemy import Connection, delete, func, insert, select, text

from bureau_core.enums import Outcome
from bureau_core.validation.cascades import CASCADES
from bureau_core.validation.parameters import render_message
from bureau_core.validation.python_rules import PYTHON_RULES, RuleContext
from bureau_core.validation.reference import freeze_translations
from bureau_core.validation.rules import Cascade, Rule
from bureau_core.validation.tables import record_outcome, validation_finding, validation_summary

_INSERT_SQL_FINDINGS = """INSERT INTO automn.validation_finding
    (snapshot_id, rule_id, entity, row_id, account_key, party_key, segment, field, outcome, value, message)
SELECT :snapshot_id, :rule_id, :entity, q.row_id, q.account_key, q.party_key, :segment, :field, :outcome,
       SUBSTRING(CAST(q.value AS NVARCHAR(500)), 1, 500), :message
FROM (
{sql}
) q"""


class ValidationError(Exception):
    pass


@dataclass(frozen=True)
class ValidationResult:
    snapshot_id: int
    records: int
    accepted: int
    rejected: int
    file_rejected: bool
    findings: dict[str, int]  # outcome -> count


def _params(snapshot_id: int, as_of_date: date, parameters: dict, submission_type: str) -> dict:
    """Bind values for SQL rules: the snapshot, its type (for ref_code_translation / ref_code_group), dates, and
    the rule parameters (:name)."""
    return {
        **parameters,
        "snapshot_id": snapshot_id,
        "submission_type": submission_type,
        "as_of_date": as_of_date,
        "one_year_before": as_of_date - timedelta(days=365),
    }


def _run_sql_rule(conn: Connection, rule: Rule, params: dict) -> None:
    try:
        conn.execute(
            text(_INSERT_SQL_FINDINGS.format(sql=rule.sql.strip().rstrip(";"))),
            {
                **params,
                "rule_id": rule.rule_id,
                "entity": rule.entity,
                "segment": rule.segment,
                "field": rule.field,
                "outcome": str(rule.outcome),
                "message": rule.message[:500],
            },
        )
    except Exception as exc:
        raise ValidationError(f"Rule {rule.rule_id} failed: {exc}") from exc


def _run_python_rule(conn: Connection, rule: Rule, ctx: RuleContext) -> None:
    rows = [
        dict(
            snapshot_id=ctx.snapshot_id,
            rule_id=rule.rule_id,
            entity=rule.entity,
            row_id=f.row_id,
            account_key=f.account_key,
            party_key=f.party_key,
            segment=rule.segment,
            field=rule.field,
            outcome=str(rule.outcome),
            value=None if f.value is None else str(f.value)[:500],
            message=(f"{rule.message}: {f.detail}" if f.detail else rule.message)[:500],
        )
        for f in PYTHON_RULES[rule.python](ctx)
    ]
    if rows:
        conn.execute(insert(validation_finding), rows)


def _clear(conn: Connection, snapshot_id: int) -> None:
    for t in (validation_finding, validation_summary, record_outcome):
        conn.execute(delete(t).where(t.c.snapshot_id == snapshot_id))


def run_validation(
    conn: Connection,
    snapshot_id: int,
    as_of_date: date,
    rules: list[Rule],
    parameters: dict,
    cascade: Cascade,
    submission_type: str,
) -> ValidationResult:
    _clear(conn, snapshot_id)
    freeze_translations(conn, snapshot_id, submission_type)  # the code rules below and the file use this copy
    rules = [replace(r, message=render_message(r.message, parameters)) for r in rules]  # {parameter} in messages
    params = _params(snapshot_id, as_of_date, parameters, submission_type)
    ctx = RuleContext(conn, snapshot_id, as_of_date, parameters)
    active = [r for r in rules if r.enabled]
    for rule in active:
        if rule.kind == "sql":
            _run_sql_rule(conn, rule, params)
        elif rule.kind == "python":
            _run_python_rule(conn, rule, ctx)
    decide = CASCADES.get(cascade.name)
    if decide is None:
        raise ValidationError(f"Unknown cascade {cascade.name!r} (validation/cascades.py)")
    accepted, rejected = decide(conn, snapshot_id, {r.rule_id: r for r in rules}, parameters, cascade.settings)
    f = validation_finding.c
    conn.execute(
        insert(validation_summary).from_select(
            ["snapshot_id", "rule_id", "outcome", "finding_count"],
            select(f.snapshot_id, f.rule_id, f.outcome, func.count())
            .where(f.snapshot_id == snapshot_id)
            .group_by(f.snapshot_id, f.rule_id, f.outcome),
        )
    )
    s = validation_summary.c
    by_outcome = {
        o: n
        for o, n in conn.execute(
            select(s.outcome, func.sum(s.finding_count)).where(s.snapshot_id == snapshot_id).group_by(s.outcome)
        )
    }
    return ValidationResult(
        snapshot_id,
        accepted + rejected,
        accepted,
        rejected,
        bool(by_outcome.get(str(Outcome.REJECT_FILE))),
        {k: int(v) for k, v in by_outcome.items()},
    )
