"""MFI step 5 (ADR 0003): the MFI rule pack (types/mfi/rules.yaml) run by the shared validation engine on sample
data. A clean batch passes; each rule is shown firing on one change. MFI is still a planned type, so its pack is
checked with its own type files (check_files), as every pack is before its type goes live."""

import copy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from bureau_core.contract import CanonicalBatch
from bureau_core.types import TypeDefinition, check_files
from bureau_core.validation.engine import run_validation
from bureau_core.validation.parameters import parse_parameters
from bureau_core.validation.rules import load_pack_file
from bureau_core.validation.validate import SNAPSHOT_ID, load_database, packaged_reference, read_result

MFI = Path(__file__).resolve().parents[1] / "src" / "bureau_core" / "types" / "mfi"
AS_OF = date(2027, 7, 31)


@pytest.fixture(scope="module")
def mfi():
    name, translations, groups, required = check_files(MFI)
    tdef = TypeDefinition(name, MFI, translations, groups, required)  # type: ignore[arg-type]  (planned: no enum)
    pack = load_pack_file(MFI / "rules.yaml", tdef)
    params = {n: p.default for n, p in parse_parameters(yaml.safe_load((MFI / "rules.yaml").read_text())).items()}
    reference = packaged_reference()
    reference["ref_code_translation"] = reference["ref_code_translation"] + [
        dict(submission_type="MFI", code_list=lst, code=v, output_code=t.output_code, is_active=t.active)
        for lst, values in translations.items()
        for v, t in values.items()
    ]
    return pack, params, reference


def batch() -> dict:
    """One SHG loan (member M1, group G1) and one individual loan (member M2), all valid."""
    return {
        "party": [
            dict(
                row_id=1,
                party_key="M1",
                full_name="Lakshmi Devi",
                birth_date=date(1990, 1, 2),
                gender="FEMALE",
                family_income=Decimal("12000"),
                group_key="G1",
            ),
            dict(
                row_id=2,
                party_key="M2",
                full_name="Sita Ram",
                birth_date=date(1985, 5, 5),
                gender="FEMALE",
                family_income=Decimal("9000"),
            ),
        ],
        "party_identifier": [
            dict(row_id=1, party_key="M1", id_type="PAN", id_number="ABCPD1234F"),
            dict(row_id=2, party_key="M2", id_type="VOTER_ID", id_number="ABC1234567"),
        ],
        "party_phone": [dict(row_id=1, party_key="M1", phone_number="9845012367", phone_type="MOBILE", priority=1)],
        "party_email": [dict(row_id=1, party_key="M1", email="lakshmi@example.com", priority=1)],
        "party_address": [
            dict(
                row_id=1,
                party_key="M1",
                address_line_1="12, 2nd Cross, Jayanagar",
                state_code="KARNATAKA",
                pin_code="560011",
                address_category="PERMANENT",
                priority=1,
            ),
            dict(
                row_id=2,
                party_key="M2",
                address_line_1="4 Gandhi Road, Madurai",
                state_code="TAMIL_NADU",
                pin_code="625001",
                address_category="PERMANENT",
                priority=1,
            ),
        ],
        "party_relative": [
            dict(row_id=1, party_key="M1", relative_role="NOMINEE", priority=1, full_name="Anu", relation="DAUGHTER")
        ],
        "account": [
            dict(
                row_id=1,
                account_key="S1",
                account_number="S1",
                loan_category="SHG_INDIVIDUAL",
                group_key="G1",
                date_opened=date(2026, 1, 15),
                high_credit_amount=Decimal("50000"),
                current_balance=Decimal("20000"),
                amount_overdue=Decimal("0"),
                days_past_due=0,
                repayment_tenure=52,
                payment_frequency="WEEKLY",
                emi_amount=Decimal("1100"),
                loan_type="TERM_LOAN",
            ),
            dict(
                row_id=2,
                account_key="I1",
                account_number="I1",
                loan_category="INDIVIDUAL",
                date_opened=date(2026, 3, 1),
                high_credit_amount=Decimal("30000"),
                current_balance=Decimal("10000"),
                amount_overdue=Decimal("0"),
                days_past_due=0,
                repayment_tenure=24,
                payment_frequency="MONTHLY",
                emi_amount=Decimal("1400"),
            ),
        ],
        "account_holder": [
            dict(row_id=1, account_key="S1", party_key="M1"),
            dict(row_id=2, account_key="I1", party_key="M2"),
        ],
        "borrower_group": [
            dict(
                row_id=1,
                group_key="G1",
                full_name="Jyothi SHG",
                group_account_number="GL-1",
                disbursed_amount=Decimal("500000"),
                current_balance=Decimal("200000"),
                date_opened=date(2026, 1, 1),
                days_past_due=0,
                amount_overdue=Decimal("0"),
                loan_type="TERM_LOAN",
                repayment_tenure=24,
                payment_frequency="MONTHLY",
                emi_amount=Decimal("22000"),
                address_line_1="Ward 4, Hosur Road",
                state_code="KARNATAKA",
                pin_code="560068",
                phone_number="9876500001",
            ),
        ],
    }


def run(mfi, entities: dict) -> tuple[set[str], dict[tuple[str, str], tuple[str, str | None]]]:
    from bureau_core.canonical import canonical_model

    pack, params, reference = mfi
    model = canonical_model()
    full = {  # every row with all of its entity's columns (empty where not given)
        e: [{"row_id": r["row_id"], **{c.name: r.get(c.name) for c in model.entities[e].columns}} for r in rows]
        for e, rows in entities.items()
    }
    b = CanonicalBatch("MFI", AS_OF, "1.2", full)
    engine = load_database(b, reference)
    try:
        with engine.begin() as conn:
            run_validation(conn, SNAPSHOT_ID, AS_OF, list(pack.rules), params, pack.cascade, "MFI")
            result = read_result(conn, SNAPSHOT_ID)
    finally:
        engine.dispose()
    fired = {f["rule_id"] for f in result.findings}
    outcomes = {(a, p): (o, r) for a, p, o, r in result.outcomes}
    return fired, outcomes


def test_the_pack_loads_with_generated_rules_parameters_and_the_cascade(mfi):
    pack, params, _ = mfi
    ids = {r.rule_id for r in pack.rules}
    assert {"MFI.REQUIRED.account.loan_category", "MFI.ENUM.account.loan_category", "MFI.ENUM.party.gender",
            "MFI.REC.NO_PHONE_OR_ID", "MFI.GRP.GROUP_MISSING"} <= ids  # fmt: skip
    assert all(r.rule_id.startswith("MFI.") for r in pack.rules)
    assert pack.cascade.name == "account_holder_minimums"
    assert (params["min_member_age"], params["group_address_required_from"]) == (16, date(2027, 1, 1))


def test_a_clean_batch_is_accepted(mfi):
    fired, outcomes = run(mfi, batch())
    assert all(o == "ACCEPTED" for o, _ in outcomes.values()), outcomes
    rejecting = {r.rule_id for r in mfi[0].rules if r.outcome.value != "WARN"}
    assert not fired & rejecting, fired


def change(path, value):
    """A change to the batch: entity, row index, column -> value (None removes the value)."""

    def apply(b):
        entity, i, col = path
        b[entity][i][col] = value

    return apply


def remove(entity):
    def apply(b):
        b[entity] = []

    return apply


def single_word_without_id(b):
    b["party"][1].update(full_name="Sita")
    b["party_identifier"].pop(1)


CASES = [
    # member
    ("MFI.CNS.NAME_DIGITS", change(("party", 0, "full_name"), "Lakshmi 2"), ("S1", "M1")),
    ("MFI.CNS.NAME_STARTS_WITH_DESCRIPTOR", change(("party", 0, "full_name"), "S/O Ravi Kumar"), ("S1", "M1")),
    (
        "MFI.CNS.NAME_TOO_LONG",
        change(("party", 0, "full_name"), "A" * 60 + " " + "B" * 60 + " " + "C" * 60),
        ("S1", "M1"),
    ),
    ("MFI.CNS.UNDER_AGE", change(("party", 0, "birth_date"), date(2015, 1, 1)), ("S1", "M1")),
    ("MFI.CNS.BIRTH_DATE_INVALID", change(("party", 1, "birth_date"), date(2027, 8, 1)), ("I1", "M2")),
    ("MFI.CNS.FAMILY_INCOME_REQUIRED", change(("party", 1, "family_income"), None), ("I1", "M2")),
    ("MFI.REQUIRED.party.gender", change(("party", 1, "gender"), None), ("I1", "M2")),
    ("MFI.ENUM.party.gender", change(("party", 1, "gender"), "ROBOT"), ("I1", "M2")),
    # IDs, phones, the minimums cascade
    ("MFI.REC.NO_PHONE_OR_ID", change(("party_identifier", 1, "id_number"), "12"), ("I1", "M2")),
    ("MFI.REC.NO_VALID_ADDRESS", change(("party_address", 1, "pin_code"), "999000"), ("I1", "M2")),
    (
        "MFI.REC.SINGLE_WORD_NAME",
        lambda b: single_word_without_id(b),
        None,
    ),
    # account
    ("MFI.ACT.DATE_OPENED_INVALID", change(("account", 1, "date_opened"), date(2027, 8, 2)), ("I1", "M2")),
    ("MFI.ACT.DISBURSED_NOT_POSITIVE", change(("account", 1, "high_credit_amount"), Decimal("0")), ("I1", "M2")),
    ("MFI.ACT.GROUP_REQUIRED", change(("account", 0, "group_key"), None), ("S1", "M1")),
    ("MFI.ACT.INSTALMENTS_REQUIRED", change(("account", 1, "repayment_tenure"), None), ("I1", "M2")),
    ("MFI.ACT.INSTALMENT_AMOUNT_REQUIRED", change(("account", 1, "emi_amount"), Decimal("0")), ("I1", "M2")),
    ("MFI.ACT.OVERDUE_DPD_MISMATCH", change(("account", 1, "days_past_due"), 12), ("I1", "M2")),
    (
        "MFI.ACT.WRITE_OFF_AMOUNT_REQUIRED",
        change(("account", 1, "credit_facility_status"), "WRITTEN_OFF"),
        ("I1", "M2"),
    ),
    (
        "MFI.ACT.WRITE_OFF_NOT_ALLOWED",
        lambda b: b["account"][1].update(written_off_total=Decimal("5"), write_off_date=date(2027, 1, 1)),
        ("I1", "M2"),
    ),
    ("MFI.ACT.CLOSE_DATE_REQUIRED", change(("account", 1, "credit_facility_status"), "POST_WRITE_OFF_CLOSED"), None),
    ("MFI.ACT.BALANCE_ON_CLOSED", change(("account", 1, "date_closed"), date(2027, 7, 1)), ("I1", "M2")),
    ("MFI.ENUM.account.loan_category", change(("account", 1, "loan_category"), "PAYDAY"), ("I1", "M2")),
    # group: the group's records are rejected with it
    ("MFI.GRP.GROUP_MISSING", remove("borrower_group"), ("S1", "M1")),
    ("MFI.GRP.NAME_INVALID", change(("borrower_group", 0, "full_name"), "S 1"), ("S1", "M1")),
    ("MFI.GRP.LOAN_DETAILS_INVALID", change(("borrower_group", 0, "repayment_tenure"), None), ("S1", "M1")),
    (
        "MFI.GRP.PHONE_REQUIRED",
        lambda b: b["borrower_group"][0].update(date_opened=date(2027, 2, 1), phone_number=None),
        ("S1", "M1"),
    ),
]


@pytest.mark.parametrize(("rule_id", "mutate", "rejected"), CASES, ids=[c[0] for c in CASES])
def test_each_rule_fires(mfi, rule_id, mutate, rejected):
    b = copy.deepcopy(batch())
    mutate(b)
    fired, outcomes = run(mfi, b)
    assert rule_id in fired, sorted(fired)
    if rejected:
        assert outcomes[rejected][0] == "REJECTED", outcomes
        others = [k for k in outcomes if k != rejected]
        assert all(outcomes[k][0] == "ACCEPTED" for k in others), outcomes  # only the affected record


def test_the_group_address_is_a_warning_before_the_date_gate_and_a_rejection_after(mfi):
    b = copy.deepcopy(batch())
    b["borrower_group"][0].update(address_line_1=None)
    fired, outcomes = run(mfi, b)
    assert "MFI.GRP.ADDRESS_MISSING" in fired and outcomes[("S1", "M1")][0] == "ACCEPTED"
    b["borrower_group"][0].update(date_opened=date(2027, 3, 1))
    fired, outcomes = run(mfi, b)
    assert "MFI.GRP.ADDRESS_INVALID" in fired and outcomes[("S1", "M1")][0] == "REJECTED"


def test_a_cancelled_loan_may_report_zero_disbursed(mfi):
    b = copy.deepcopy(batch())
    b["account"][1].update(high_credit_amount=Decimal("0"), credit_facility_status="CANCELLED")
    fired, _ = run(mfi, b)
    assert "MFI.ACT.DISBURSED_NOT_POSITIVE" not in fired


def add(entity, row):
    def apply(b):
        b[entity].append(row)

    return apply


def phones(b):
    for i in range(2, 4):
        b["party_phone"].append(dict(row_id=i, party_key="M1", phone_number=f"98450123{60 + i}", phone_type="MOBILE",
                                     priority=i))  # fmt: skip


def relatives(b):
    b["party_relative"].append(dict(row_id=2, party_key="M1", relative_role="NOMINEE", priority=2, full_name="Ravi",
                                    relation="HUSBAND"))  # fmt: skip


MORE = [  # the rule fires; the record decision follows from its outcome (tested above for the rejecting kinds)
    ("MFI.CNS.NAME_DISALLOWED_CHARS", change(("party", 1, "full_name"), "Sita & Ram")),
    ("MFI.CNS.NAME_NO_VALID_TOKEN", change(("party", 1, "full_name"), "S R")),
    ("MFI.CNS.AGE_UNDER_16", change(("party", 1, "age"), 12)),
    ("MFI.CNS.AMOUNT_NEGATIVE", change(("party", 1, "family_expenses"), Decimal("-1"))),
    ("MFI.CNS.RELATIVE_INCOMPLETE", change(("party_relative", 0, "relation"), None)),
    ("MFI.CNS.TOO_MANY_RELATIVES", relatives),
    ("MFI.CNS.ID_FORMAT", change(("party_identifier", 0, "id_number"), "ABCCD1234F")),  # 4th letter not P / H
    (
        "MFI.CNS.ID_TYPE_NOT_REPORTABLE",
        add("party_identifier", dict(row_id=3, party_key="M1", id_type="CIN", id_number="L01110MH1993PLC072842")),
    ),
    ("MFI.CNS.PHONE_INVALID", change(("party_phone", 0, "phone_number"), "1111111111")),
    ("MFI.CNS.MORE_THAN_TWO_PHONES", phones),
    ("MFI.ADR.ADDRESS_TOO_SHORT", change(("party_address", 0, "address_line_1"), "AB")),
    ("MFI.ADR.ADDRESS_MEANINGLESS", change(("party_address", 0, "address_line_1"), "#### ////")),
    ("MFI.ADR.STATE_REQUIRED", change(("party_address", 0, "state_code"), None)),
    ("MFI.ADR.PIN_FORMAT", change(("party_address", 0, "pin_code"), "5600")),
    ("MFI.ADR.PIN_STATE_MISMATCH", change(("party_address", 0, "pin_code"), "110001")),
    ("MFI.ADR.NOT_PERMANENT_OR_CURRENT", change(("party_address", 0, "address_category"), "OFFICE")),
    ("MFI.ADR.EMAIL_INVALID", change(("party_email", 0, "email"), "lakshmi@@example..com")),
    ("MFI.ACT.DATE_CLOSED_INVALID", change(("account", 1, "date_closed"), date(2027, 8, 5))),
    ("MFI.ACT.LAST_PAYMENT_INVALID", change(("account", 1, "date_last_payment"), date(2025, 1, 1))),
    (
        "MFI.ACT.CLOSE_DATE_NOT_ALLOWED",
        lambda b: b["account"][1].update(
            credit_facility_status="RESTRUCTURED_DUE_TO_COVID_19", date_closed=date(2027, 6, 1)
        ),
    ),
    (
        "MFI.ACT.WRITE_OFF_DATE_REQUIRED",
        lambda b: b["account"][1].update(credit_facility_status="WRITTEN_OFF", written_off_total=Decimal("100")),
    ),
    ("MFI.ACT.WRITE_OFF_REASON_NOT_ALLOWED", change(("account", 1, "write_off_reason"), "DEATH")),
    (
        "MFI.ACT.SETTLEMENT_AMOUNT_MISSING",
        lambda b: b["account"][1].update(
            credit_facility_status="SETTLED", written_off_total=Decimal("100"), write_off_date=date(2027, 1, 1)
        ),
    ),
    ("MFI.ACT.AMOUNT_NEGATIVE", change(("account", 1, "sanctioned_amount"), Decimal("-5"))),
    ("MFI.ACT.AMOUNT_TOO_LARGE", change(("account", 1, "high_credit_amount"), Decimal("1000000000"))),
    ("MFI.REC.ACCOUNT_MISSING", add("account_holder", dict(row_id=3, account_key="NOPE", party_key="M1"))),
    ("MFI.REC.PARTY_MISSING", add("account_holder", dict(row_id=3, account_key="I1", party_key="NOPE"))),
    ("MFI.REC.DUPLICATE_ACCOUNT_KEY", lambda b: b["account"].append({**b["account"][1], "row_id": 3})),
    ("MFI.GRP.ADDRESS_MISSING", change(("borrower_group", 0, "state_code"), None)),
]


@pytest.mark.parametrize(("rule_id", "mutate"), MORE, ids=[c[0] for c in MORE])
def test_each_remaining_rule_fires(mfi, rule_id, mutate):
    b = copy.deepcopy(batch())
    mutate(b)
    fired, _ = run(mfi, b)
    assert rule_id in fired, sorted(fired)


def test_every_written_rule_has_a_test(mfi):
    """Every rule written in rules.yaml (not generated) is shown firing above; the generated ones are the engine's."""
    written = {r.rule_id for r in mfi[0].rules if not r.generated and r.rule_id != "MFI.ADJ.NOT_FOUND"}
    tested = {c[0] for c in CASES} | {c[0] for c in MORE} | {"MFI.CNS.SINGLE_WORD_NAME", "MFI.GRP.ADDRESS_INVALID"}
    assert written - tested == set()
