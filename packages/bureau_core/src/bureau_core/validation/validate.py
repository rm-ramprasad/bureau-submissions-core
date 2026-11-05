"""Validate a CanonicalBatch on any platform (option A, user decision 2026-11-02): the batch is loaded into a private
in-memory SQLite database and the type's rules run there unchanged (the same SQL as on Azure SQL; make_engine gives
SQLite the T-SQL functions they use), followed by the type's cascade. Returns the ValidationResult.

Deterministic: the same batch, rules, parameters and reference data give the same result. The reference data (code
lists, translations, code groups, state PIN ranges) default to the packaged copy (model.yaml, codes.yaml); a host
whose database is the master of its reference data passes its own rows (`reference`), as the Azure app does
(the app).

This is the one core module that uses SQLAlchemy, with SQLite only (no server, no driver, nothing to install).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, insert, select

from bureau_core.canonical import canonical_model
from bureau_core.contract import CanonicalBatch, ValidationResult, check_batch
from bureau_core.enums import Outcome
from bureau_core.validation.engine import run_validation
from bureau_core.validation.parameters import defaults
from bureau_core.validation.reference import (
    group_rows,
    ref_code_rows,
    ref_state_pin_rows,
    snapshot_translations,
    translation_rows,
)
from bureau_core.validation.rules import Rule, builtin_pack, builtin_rules
from bureau_core.validation.tables import (
    ENTITY_TABLES,
    extract_snapshot,
    metadata,
    record_outcome,
    ref_code,
    ref_code_group,
    ref_code_translation,
    ref_state_pin,
    snapshot_adjustment,
    snapshot_adjustment_effect,
    sqlite_engine,
    submission_adjustment,
    tenant,
    validation_finding,
)

SNAPSHOT_ID = 1
TENANT = "core"
_FIXED = datetime(2000, 1, 1)  # timestamps the database needs; never part of a result
REFERENCE_TABLES = {t.name: t for t in (ref_code, ref_state_pin, ref_code_translation, ref_code_group)}


def packaged_reference() -> dict[str, list[dict]]:
    """The reference data shipped with the core: table name -> rows."""
    return {
        "ref_code": ref_code_rows(),
        "ref_state_pin": ref_state_pin_rows(),
        "ref_code_translation": translation_rows(),
        "ref_code_group": group_rows(),
    }


class BatchContractError(ValueError):
    """The batch breaks the contract (check_batch problems that are not data findings), so it cannot be validated."""


def read_result(conn: Connection, snapshot_id: int) -> ValidationResult:
    """A validated snapshot's result, from the database it was validated in (here, or the app's)."""
    f = validation_finding.c
    fields, segments = [], []
    for x in conn.execute(
        select(f.entity, f.row_id, f.field, f.account_key, f.outcome)
        .where(
            f.snapshot_id == snapshot_id,
            f.outcome.in_([str(Outcome.REJECT_FIELD), str(Outcome.REJECT_SEGMENT)]),
            f.row_id.is_not(None),
        )
        .order_by(f.entity, f.row_id, f.field, f.account_key)
    ):
        if x.outcome == Outcome.REJECT_FIELD and x.field:
            fields.append((x.entity, x.row_id, x.field, x.account_key))
        elif x.outcome == Outcome.REJECT_SEGMENT:
            segments.append((x.entity, x.row_id, x.account_key))
    o = record_outcome.c
    outcomes = [
        (x.account_key, x.party_key, x.outcome, x.reason_rule_id)
        for x in conn.execute(
            select(o.account_key, o.party_key, o.outcome, o.reason_rule_id)
            .where(o.snapshot_id == snapshot_id)
            .order_by(o.account_key, o.party_key)
        )
    ]
    cols = [
        f.rule_id,
        f.entity,
        f.row_id,
        f.account_key,
        f.party_key,
        f.segment,
        f.field,
        f.outcome,
        f.value,
        f.message,
    ]
    findings = [
        dict(r._mapping)
        for r in conn.execute(
            select(*cols)
            .where(f.snapshot_id == snapshot_id)
            .order_by(f.rule_id, f.entity, f.row_id, f.account_key, f.party_key, f.field, f.value, f.message)
        )
    ]
    return ValidationResult(outcomes, fields, segments, snapshot_translations(conn, snapshot_id), findings)


# ---------------------------------------------------------------- finding values as Azure SQL writes them
# SQLite has no decimal type: a rule's value from a decimal column is written 6401 where Azure SQL writes 6401.00
# (engine comparison on real snapshots, 2026-11-02; the only difference found). The value's column is read from the
# rule's SQL ("<alias>.<column> AS value", alias -> its automn.c_* table); a rule's `field` is not always that column
# (UCRF.TL.DPD_WITHOUT_OVERDUE is filed under amount_overdue and reports days_past_due).
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
_VALUE = re.compile(r"\b(\w+)\.(\w+)\s+AS\s+value\b", re.I)
_TABLE = re.compile(r"\bautomn\.(c_\w+)\s+(?:AS\s+)?(\w+)", re.I)


def value_scale(rule: Rule) -> int | None:
    """The decimal scale of the column the rule reports as its value, or None (not a decimal column, or not known)."""
    if not rule.sql:
        return None
    found = _VALUE.findall(rule.sql)
    if len({m for m in found}) != 1:  # no column, or several: leave the value as it is
        return None
    alias, column = found[0]
    tables = {a.lower(): t.lower() for t, a in _TABLE.findall(rule.sql)}
    entity = next((e for e in canonical_model().entities.values() if e.table == tables.get(alias.lower())), None)
    col = next((c for c in entity.columns if c.name == column), None) if entity else None
    return col.scale if col is not None and col.type == "decimal" else None


def finding_value(scale: int | None, value: str | None) -> str | None:
    """The value with the decimal column's scale (6401 -> 6401.00); anything else unchanged."""
    if scale is None or value is None or not _NUMBER.match(value):
        return value
    return str(Decimal(value).quantize(Decimal(1).scaleb(-scale)))


def load_database(batch: CanonicalBatch, reference: dict[str, list[dict]] | None = None) -> Engine:
    """A private in-memory SQLite holding the batch as snapshot SNAPSHOT_ID, with its exceptions and the reference
    data (`reference`: table -> rows, each table given replaces the packaged one). The caller disposes of it.
    Refuses a batch that breaks the contract (check_batch problems that are not data findings)."""
    structural = [p for p in check_batch(batch) if p.kind not in ("CODE", "LENGTH")]  # data problems: rules report
    if structural:
        raise BatchContractError("; ".join(f"{p.entity}#{p.row_id} {p.column}: {p.message}" for p in structural[:20]))
    unknown = set(reference or {}) - set(REFERENCE_TABLES)
    if unknown:
        raise ValueError(f"unknown reference tables {sorted(unknown)}")
    engine = sqlite_engine()
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            insert(tenant),
            [dict(tenant_id=TENANT, display_name=TENANT, source_schema=TENANT, created_at=_FIXED, updated_at=_FIXED)],
        )
        for name, rows in {**packaged_reference(), **(reference or {})}.items():
            if rows:
                conn.execute(insert(REFERENCE_TABLES[name]), rows)
        conn.execute(
            insert(extract_snapshot),
            [
                dict(
                    snapshot_id=SNAPSHOT_ID,
                    tenant_id=TENANT,
                    submission_type=batch.submission_type,
                    as_of_date=batch.as_of_date,
                    canonical_version=batch.canonical_version,
                    status="EXTRACTED",
                    created_at=_FIXED,
                    updated_at=_FIXED,
                )
            ],
        )
        for entity, rows in batch.entities.items():
            if rows:
                conn.execute(insert(ENTITY_TABLES[entity]), [{**r, "snapshot_id": SNAPSHOT_ID} for r in rows])
        adjustments = batch.exceptions.get("adjustments") or []
        if adjustments:  # the frozen copy refers to its request's adjustment (foreign key)
            conn.execute(
                insert(submission_adjustment),
                [
                    dict(
                        adjustment_id=a["adjustment_id"],
                        tenant_id=TENANT,
                        as_of_date=batch.as_of_date,
                        submission_type=batch.submission_type,
                        kind=a["kind"],
                        created_by=a["created_by"],
                        created_at=_FIXED,
                    )
                    for a in adjustments
                ],
            )
            conn.execute(insert(snapshot_adjustment), [{**a, "snapshot_id": SNAPSHOT_ID} for a in adjustments])
        effects = batch.exceptions.get("effects") or []
        if effects:
            conn.execute(insert(snapshot_adjustment_effect), [{**e, "snapshot_id": SNAPSHOT_ID} for e in effects])
    return engine


def validate(
    batch: CanonicalBatch,
    rules: list[Rule] | None = None,
    parameters: dict | None = None,
    reference: dict[str, list[dict]] | None = None,
) -> ValidationResult:
    """Validate the batch: `rules` default to the type's built-in pack, `parameters` to the pack's defaults, and
    `reference` (table -> rows) to the packaged reference data."""
    submission_type = batch.submission_type
    rules = list(builtin_rules(submission_type)) if rules is None else rules
    parameters = defaults(submission_type) if parameters is None else parameters
    engine = load_database(batch, reference)
    try:
        with engine.begin() as conn:
            run_validation(
                conn,
                SNAPSHOT_ID,
                batch.as_of_date,
                rules,
                parameters,
                builtin_pack(submission_type).cascade,
                submission_type,
            )
            result = read_result(conn, SNAPSHOT_ID)
    finally:
        engine.dispose()
    scales = {r.rule_id: value_scale(r) for r in rules}
    for x in result.findings:
        x["value"] = finding_value(scales.get(x["rule_id"]), x["value"])
    return result
