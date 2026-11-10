"""Adjustments in the extract: all set-based SQL, run on the app server next to the tenant's LMS schema.

For a snapshot, in order:
  1. FREEZE   the request's (batch's) active submission_adjustment rows into snapshot_adjustment (audit trail);
  2. RESOLVE  them against the tenant's account / account_holder SELECTs into snapshot_adjustment_effect:
              EXCLUDE per loan (customer exclusions expand to every loan the customer holds), EXCLUDE_PARTY per
              borrower left with no loan, OVERRIDE per loan field with the source value as old_value;
  3. EXTRACT  each entity with its mapping SELECT wrapped by `final_select`: excluded loans / borrowers are
              filtered with NOT EXISTS and overridden fields come from COALESCE(override, source value).
The c_* tables then hold the final data; validation and the formatter read them unchanged.
"""

from __future__ import annotations

from app.enums import OverrideField
from bureau_core.canonical import canonical_model

_FREEZE = """INSERT INTO automn.snapshot_adjustment
    (snapshot_id, adjustment_id, kind, account_number, customer_id, field, override_value, reason, created_by,
     created_at)
SELECT s.snapshot_id, a.adjustment_id, a.kind, a.account_number, a.customer_id, a.field, a.override_value,
       a.reason, a.created_by, a.created_at
FROM automn.extract_snapshot s
JOIN automn.submission_adjustment a ON a.batch_id = s.batch_id AND a.tenant_id = s.tenant_id
    AND a.submission_type = s.submission_type AND a.removed_at IS NULL
WHERE s.snapshot_id = :snapshot_id"""

_EFFECT_INSERT = """INSERT INTO automn.snapshot_adjustment_effect
    (snapshot_id, adjustment_id, action, account_key, party_key, field, old_value, new_value)
"""


def _strip(sql: str) -> str:
    return sql.strip().rstrip(";")


def _excluded(action: str, key_expr: str) -> str:
    column = "party_key" if action == "EXCLUDE_PARTY" else "account_key"
    return (
        f"EXISTS (SELECT 1 FROM automn.snapshot_adjustment_effect e WHERE e.snapshot_id = :snapshot_id "
        f"AND e.action = '{action}' AND e.{column} = {key_expr})"
    )


# Typed NULLs: Postgres types a bare NULL in SELECT DISTINCT as text and refuses it for a numeric column (found by
# the Database job, 2026-11-06). Valid T-SQL too, so Azure SQL is unaffected.
_NO_TEXT = "CAST(NULL AS VARCHAR(50))"
_NO_AMOUNT = "CAST(NULL AS DECIMAL(20, 2))"


def freeze_sql() -> str:
    return _FREEZE


def resolve_sql(account_sql: str, holder_sql: str) -> list[str]:
    """Statements filling snapshot_adjustment_effect from the frozen adjustments and the tenant's SELECTs."""
    account, holder = f"(\n{_strip(account_sql)}\n)", f"(\n{_strip(holder_sql)}\n)"
    old_value = "CASE x.field " + " ".join(f"WHEN '{f.value}' THEN acc.{f.value}" for f in OverrideField) + " END"
    return [
        # loans excluded by account number
        _EFFECT_INSERT
        + f"""SELECT DISTINCT x.snapshot_id, x.adjustment_id, 'EXCLUDE', acc.account_key, {_NO_TEXT}, {_NO_TEXT},
       {_NO_AMOUNT}, {_NO_AMOUNT}
FROM automn.snapshot_adjustment x JOIN {account} acc ON acc.account_number = x.account_number
WHERE x.snapshot_id = :snapshot_id AND x.kind = 'EXCLUDE_ACCOUNT'""",
        # every loan of an excluded customer
        _EFFECT_INSERT
        + f"""SELECT DISTINCT x.snapshot_id, x.adjustment_id, 'EXCLUDE', h.account_key, {_NO_TEXT}, {_NO_TEXT},
       {_NO_AMOUNT}, {_NO_AMOUNT}
FROM automn.snapshot_adjustment x JOIN {holder} h ON h.party_key = x.customer_id
WHERE x.snapshot_id = :snapshot_id AND x.kind = 'EXCLUDE_CUSTOMER'""",
        # overridden loan fields, with the source value
        _EFFECT_INSERT
        + f"""SELECT x.snapshot_id, x.adjustment_id, 'OVERRIDE', acc.account_key, {_NO_TEXT}, x.field,
       {old_value}, x.override_value
FROM automn.snapshot_adjustment x JOIN {account} acc ON acc.account_number = x.account_number
WHERE x.snapshot_id = :snapshot_id AND x.kind = 'OVERRIDE'""",
        # borrowers of excluded loans who hold no other loan in this extract
        _EFFECT_INSERT
        + f"""SELECT :snapshot_id, MIN(ex.adjustment_id), 'EXCLUDE_PARTY', {_NO_TEXT}, h.party_key, {_NO_TEXT},
       {_NO_AMOUNT}, {_NO_AMOUNT}
FROM {holder} h
JOIN automn.snapshot_adjustment_effect ex ON ex.snapshot_id = :snapshot_id AND ex.action = 'EXCLUDE'
    AND ex.account_key = h.account_key
WHERE NOT EXISTS (
    SELECT 1 FROM {holder} h2
    WHERE h2.party_key = h.party_key AND NOT {_excluded("EXCLUDE", "h2.account_key")})
GROUP BY h.party_key""",
    ]


def final_select(entity: str, sql: str, columns: list[str]) -> str:
    """The tenant's SELECT for one entity, joined to the snapshot's adjustment effects (same columns)."""
    overrides = {f.value for f in OverrideField} if entity == "account" else set()
    cols = ",\n       ".join(
        f"COALESCE((SELECT o.new_value FROM automn.snapshot_adjustment_effect o WHERE o.snapshot_id = :snapshot_id "
        f"AND o.action = 'OVERRIDE' AND o.field = '{c}' AND o.account_key = q.account_key), q.{c}) AS {c}"
        if c in overrides
        else f"q.{c}"
        for c in columns
    )
    edef = canonical_model().entities[entity]
    if edef.account_key:  # rows of a loan: account, holders, history, guarantors ...
        where = f"NOT {_excluded('EXCLUDE', 'q.' + edef.account_key)}"
    elif edef.party_key:  # rows of a borrower: party, identifiers ..., relationships
        where = f"NOT {_excluded('EXCLUDE_PARTY', 'q.' + edef.party_key)}"
    elif entity == "extension_attribute":
        where = (
            f"NOT ((q.entity = 'account' AND {_excluded('EXCLUDE', 'q.entity_key')}) "
            f"OR (q.entity = 'party' AND {_excluded('EXCLUDE_PARTY', 'q.entity_key')}))"
        )
    elif entity == "borrower_group":  # MFI: written only on its member accounts' lines, which exclusions drop
        where = "1 = 1"
    else:
        raise ValueError(f"No adjustment rule for entity {entity!r}: give it keys in canonical/model.yaml")
    return f"SELECT {cols}\nFROM (\n{_strip(sql)}\n) q\nWHERE {where}"
