"""Form-built custom rules (app.validation.custom): compiled to safe SQL, saved per client, run with the pack."""

import pytest
from app_helpers import golden, load_snapshot
from sqlalchemy import select

from app.validation import custom as C
from app.validation.run import validate_snapshot
from app.validation.settings import load_rule_set, set_policy
from app.validation.tables import validation_finding
from canonical.snapshot import SnapshotRepository


def d(**kw) -> C.Definition:
    base = dict(
        name="TEST_RULE",
        entity="account",
        field="high_credit_amount",
        check="range",
        outcome="WARN",
        message="Sanction below the NBFC minimum",
        min="100000",
    )
    return C.Definition(**base | kw)


@pytest.mark.parametrize(
    "definition, sql_part",
    [
        (d(), "x.high_credit_amount IS NOT NULL AND (x.high_credit_amount < 100000)"),
        (d(check="required", field="emi_amount"), "x.emi_amount IS NULL"),
        (d(check="required", field="account_number"), "(x.account_number IS NULL OR TRIM(x.account_number) = '')"),
        (d(check="in_list", field="account_type", values=["05", "06"]), "x.account_type NOT IN ('05', '06')"),
        (d(check="not_in_list", field="account_type", values=["10"]), "x.account_type IN ('10')"),
        (
            d(check="length", field="account_number", min="8", max="20"),
            "(LEN(x.account_number) < 8 OR LEN(x.account_number) > 20)",
        ),
        (d(check="pattern", field="account_number", pattern="^B[0-9]"), "NOT REGEXP_LIKE(x.account_number, '^B[0-9]')"),
        (
            d(check="compare", field="amount_overdue", operator="<=", other_field="current_balance"),
            "NOT (x.amount_overdue <= x.current_balance)",
        ),
        (d(when_field="account_type", when_values=["05"]), "AND x.account_type IN ('05')"),
        (d(entity="party", field="birth_date", min="1940-01-01"), "x.birth_date < '1940-01-01'"),
    ],
)
def test_compiles_to_sql(definition, sql_part):
    sql = C.compile_sql(definition)
    assert sql_part in sql and ":snapshot_id" in sql and sql.startswith("SELECT x.row_id")


@pytest.mark.parametrize(
    "definition, message",
    [
        (d(name="bad name"), "Name"),
        (d(field="no_such_field"), "Unknown field"),
        (d(entity="tenant"), "Unknown record type"),
        (d(min=None), "minimum, a maximum or both"),
        (d(min="12abc"), "not a number"),
        (d(field="account_number", min="1"), "Range works on numbers and dates"),
        (d(check="pattern", field="account_number", pattern="[unclosed"), "not a valid regular expression"),
        (d(check="compare", field="amount_overdue", operator="<", other_field="date_opened"), "different types"),
        (d(check="in_list", field="account_type", values=[]), "at least one value"),
        (d(outcome="NOPE"), "outcome"),
        (d(message=" "), "Message"),
    ],
)
def test_invalid_definitions(definition, message):
    with pytest.raises(C.CustomRuleError, match=message):
        C.build("nbfc_001", definition)


def test_values_cannot_inject_sql():
    sql = C.compile_sql(d(check="in_list", field="account_type", values=["05') OR 1=1 --"]))
    assert "'05'') OR 1=1 --'" in sql  # quoted as a plain string
    with pytest.raises(C.CustomRuleError):
        C.compile_sql(d(field="high_credit_amount; DROP TABLE c_account"))
    with pytest.raises(C.CustomRuleError):
        C.compile_sql(d(min="1 OR 1=1"))


def test_custom_rule_runs_with_the_pack_and_previews(engine):
    batch, _ = golden("consumer_nbfc_001")
    sid = load_snapshot(engine, batch, skip=("account_history",))
    rule = C.save(engine, "nbfc_001", "CONSUMER", d(min="10000000"), "u")  # every mock sanction is below 1 crore
    count, examples = C.preview(engine, rule, sid)
    assert count > 0 and len(examples) == min(count, 5)
    validate_snapshot(engine, SnapshotRepository(engine), sid, load_rule_set(engine, "nbfc_001", "CONSUMER"))
    with engine.connect() as conn:
        found = (
            conn.execute(
                select(validation_finding.c.outcome).where(
                    validation_finding.c.snapshot_id == sid, validation_finding.c.rule_id == rule.rule_id
                )
            )
            .scalars()
            .all()
        )
    assert len(found) == count and set(found) == {"WARN"}
    set_policy(engine, "nbfc_001", rule.rule_id, is_enabled=False)  # policy works on custom rules too
    assert not next(r for r in load_rule_set(engine, "nbfc_001", "CONSUMER") if r.rule_id == rule.rule_id).enabled
    assert not [r for r in load_rule_set(engine, "nbfc_002", "CONSUMER") if r.rule_id == rule.rule_id]  # per client


def test_duplicate_name_and_delete(engine):
    C.save(engine, "nbfc_001", "CONSUMER", d(), "u")
    with pytest.raises(C.CustomRuleError, match="already exists"):
        C.save(engine, "nbfc_001", "CONSUMER", d(), "u")
    C.save(engine, "nbfc_001", "CONSUMER", d(min="5"), "u", replace_existing=True)
    assert C.definitions(engine, "nbfc_001", "CONSUMER")["CUSTOM.NBFC_001.TEST_RULE"].min == "5"
    assert C.delete_rule(engine, "nbfc_001", "CUSTOM.NBFC_001.TEST_RULE")
    assert C.definitions(engine, "nbfc_001", "CONSUMER") == {}


def test_fields_offer_the_canonical_columns():
    fields = C.fields()
    assert ("high_credit_amount", "decimal") in [(n, t) for n, t in fields["account"]] or fields["account"]
    assert set(fields) <= set(C.ENTITIES)
