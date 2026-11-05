"""Data-quality checks on uploaded workbooks (canonical.staging.checks, canonical/types/*/input_checks.yaml)."""

from datetime import date, datetime

import pytest
from sqlalchemy import select, text, update
from src_helpers import workbook

from canonical.schema import ref_code_translation
from canonical.staging.checks import CheckDefinitionError, as_date, date_problem, load_checks, parse, run_checks
from canonical.staging.tables import input_issue, input_load
from canonical.testing import add_view_accounts
from sources.excel import load_workbook

AS_OF = date(2026, 10, 26)
# A clean 'Consumer' row. Labels are the canonical value in words (tests/conftest.seed_labels).
ROW = {
    "curr_new_account_no": "A1",
    "consumer_name": "ASHA K",
    "date_of_birth": "23031981",
    "gender_label": "Female",
    "income_tax_id_number": "BIVPA9062Q",
    "address_line_1": "12 MAIN ROAD",
    "state_code_1_label": "Tamil Nadu",
    "pin_code_1": "600005",
    "account_type": "02",
    "account_type_label": "Housing Loan",
    "ownership_indicator_label": "Individual",
}


def load(engine, rows):
    data = workbook("CONSUMER", {"Consumer": rows})
    return load_workbook(
        engine,
        "CONSUMER",
        data,
        tenant_id="nbfc_001",
        file_name="c.xlsx",
        loaded_by="t",
        batch_id="b",
        clock=lambda: datetime(2026, 10, 28),
    ).load_id


def checks(engine, rows, accounts=("A1", "A2", "A3")):
    add_view_accounts(engine, AS_OF, *accounts)
    return run_checks(engine, load(engine, rows), "CONSUMER", AS_OF, record=False)


def found(result):
    return {(i["code"].removeprefix("DQ."), i["source_row"], i["column_name"]) for i in result.issues}


def test_a_clean_workbook_passes(engine):
    result = checks(
        engine,
        [
            ROW,
            {
                **ROW,
                "curr_new_account_no": "A2",
                "consumer_name": "RAVI M",
                "ownership_indicator_label": "Authorised User",
            },
        ],
    )
    assert result.passed and result.warnings == 0 and result.issues == []


def test_labels_match_ignoring_case_and_spaces(engine):
    result = checks(engine, [{**ROW, "gender_label": "  female ", "state_code_1_label": "TAMIL NADU"}])
    assert result.passed and result.issues == []


def test_unknown_labels_block_with_row_and_column(engine):
    result = checks(
        engine,
        [{**ROW, "gender_label": "F", "state_code_1_label": "Tamilnadu", "payment_frequency_label": "Every month"}],
    )
    assert {
        ("GENDER_LABEL", 2, "gender_label"),
        ("STATE_LABEL", 2, "state_code_1_label"),
        ("PAYMENT_FREQUENCY_LABEL", 2, "payment_frequency_label"),
    } <= found(result) and not result.passed
    message = next(i["message"] for i in result.issues if i["code"] == "DQ.STATE_LABEL")
    assert "'Tamilnadu' is not a label of state_code" in message


def test_labels_come_from_the_reference_table(engine):
    t = ref_code_translation.c
    with engine.begin() as conn:  # the NBFC names MALE "Gents" in its workbooks
        conn.execute(
            update(ref_code_translation)
            .where(t.submission_type == "CONSUMER", t.code_list == "gender", t.code == "MALE")
            .values(label="Gents")
        )
    assert checks(engine, [{**ROW, "gender_label": "Gents"}]).passed
    with engine.begin() as conn:  # no labels at all for a list: said once, not per row
        conn.execute(
            update(ref_code_translation)
            .where(t.submission_type == "CONSUMER", t.code_list == "occupation")
            .values(label=None)
        )
    result = checks(engine, [{**ROW, "occupation_code_label": "Salaried"}] * 2)
    assert [
        (i["source_row"], "fill its label column" in i["message"])
        for i in result.issues
        if i["code"] == "DQ.OCCUPATION_LABEL"
    ] == [(None, True)]


def test_account_type_code_and_label_must_agree(engine):
    result = checks(engine, [{**ROW, "account_type_label": "Personal Loan"}])  # code 02 = Housing Loan
    assert found(result) == {("ACCOUNT_TYPE_AGREES", 2, "account_type")} and result.passed


def test_a_code_shared_by_several_types_agrees_with_each_of_their_labels(engine):
    rows = [
        {**ROW, "account_type": "39", "account_type_label": label}  # Mudra loan / term / working capital
        for label in ("Mudra Loan", "Mudra Term Loan", "Mudra Working Capital")
    ]
    rows = [{**r, "curr_new_account_no": f"A{i}"} for i, r in enumerate(rows)]
    assert not {f for f in found(checks(engine, rows)) if f[0] == "ACCOUNT_TYPE_AGREES"}


def test_double_entries(engine):
    co = {**ROW, "consumer_name": "RAVI M", "date_of_birth": "01011980"}  # second primary owner
    twin = {**ROW, "consumer_name": "ASHA K", "date_of_birth": "23031981", "gender_label": "Male"}  # same person
    result = checks(engine, [ROW, twin, co])
    assert ("DUPLICATE_BORROWER", 3, None) in found(result)
    assert ("TWO_PRIMARY_OWNERS", 3, "curr_new_account_no") in found(result)
    assert ("TWO_PRIMARY_OWNERS", 4, "curr_new_account_no") in found(result)


def test_account_must_be_in_the_view_for_the_date(engine):
    add_view_accounts(engine, date(2026, 9, 30), "A9")  # another date: not enough
    result = checks(engine, [ROW, {**ROW, "curr_new_account_no": "A9"}], accounts=("A1",))
    assert found(result) == {("ACCOUNT_NOT_IN_VIEW", 3, "curr_new_account_no")} and not result.passed


def test_required_fields_and_identity(engine):
    bare = {k: v for k, v in ROW.items() if k not in ("gender_label", "income_tax_id_number")}
    result = checks(engine, [bare])
    assert ("REQUIRED", 2, "gender_label") in found(result) and ("ID_OR_PHONE", 2, None) in found(result)
    assert not result.passed


def test_formats_and_codes(engine):
    bad = {
        **ROW,
        "income_tax_id_number": "BIVPA906Q",
        "telephone_no_mobile": "1234567890",
        "email_id_1": "x@y",
        "pin_code_1": "60005",
        "account_type": "99",
    }
    f = found(checks(engine, [bad]))
    assert {
        ("PAN_FORMAT", 2, "income_tax_id_number"),
        ("MOBILE_FORMAT", 2, "telephone_no_mobile"),
        ("EMAIL_FORMAT", 2, "email_id_1"),
        ("PIN_FORMAT", 2, "pin_code_1"),
        ("ACCOUNT_TYPE_CODE", 2, "account_type"),
    } <= f


def test_dates_and_consistency(engine):
    rows = [
        {**ROW, "date_of_birth": "2404199"},  # truncated year
        {**ROW, "curr_new_account_no": "A2", "date_of_birth": "1062010"},  # leading zero lost; under 18
        {
            **ROW,
            "curr_new_account_no": "A3",
            "date_of_birth": "01012030",
            "pin_code_1": "515311",
            "state_code_1_label": "Karnataka",
            "passport_issue_date": "01012020",
            "passport_expiry_date": "01012019",
        },
    ]
    result = checks(engine, rows)
    f = found(result)
    assert {
        ("DATE_FORMAT", 2, "date_of_birth"),
        ("DATE_FORMAT", 3, "date_of_birth"),
        ("DATE_IN_FUTURE", 4, "date_of_birth"),
        ("PIN_STATE", 4, "pin_code_1"),
        ("PASSPORT_EXPIRY_ORDER", 4, "passport_expiry_date"),
    } <= f
    messages = {i["source_row"]: i["message"] for i in result.issues if i["code"] == "DQ.DATE_FORMAT"}
    assert "leading zero was lost" in messages[3] and "leading zero" not in messages[2]


def test_under_18_is_a_warning(engine):
    result = checks(engine, [{**ROW, "date_of_birth": "01012010"}])
    assert found(result) == {("UNDER_18", 2, "date_of_birth")} and result.passed and result.warnings == 1


def test_results_are_recorded_and_block_the_load(engine):
    add_view_accounts(engine, AS_OF, "A1")
    load_id = load(engine, [{**ROW, "gender_label": None}])
    result = run_checks(engine, load_id, "CONSUMER", AS_OF)
    assert result.blocking == 1
    with engine.connect() as conn:
        status = conn.execute(select(input_load.c.status).where(input_load.c.load_id == load_id)).scalar()
        codes = conn.execute(
            select(input_issue.c.code, input_issue.c.severity).where(input_issue.c.load_id == load_id)
        ).all()
    assert status == "DQ_FAILED" and ("DQ.REQUIRED", "ERROR") in [tuple(c) for c in codes]


def test_dates_are_read_as_ddmmyyyy_or_iso():
    assert as_date("23031981") == date(1981, 3, 23) and as_date("1981-03-23") == date(1981, 3, 23)
    assert as_date("31022020") is None and as_date("abc") is None and as_date(None) is None
    assert date_problem("23031981") is None


@pytest.mark.parametrize(
    "raw, error",
    [
        ({"sheet": "S"}, "non-empty 'checks'"),
        ({"sheet": "S", "checks": [{"id": "X1", "check": "pattern", "severity": "warn"}]}, "needs id, check"),
        (
            {
                "sheet": "S",
                "checks": [{"id": "XYZ", "check": "nope", "severity": "warn", "message": "m", "columns": ["a"]}],
            },
            "unknown check",
        ),
        (
            {
                "sheet": "S",
                "checks": [{"id": "XYZ", "check": "required", "severity": "maybe", "message": "m", "columns": ["a"]}],
            },
            "block or warn",
        ),
        (
            {
                "sheet": "S",
                "checks": [{"id": "XYZ", "check": "required", "severity": "warn", "message": "m", "columns": ["zz"]}],
            },
            "not columns",
        ),
        (
            {
                "sheet": "S",
                "checks": [
                    {"id": "XYZ", "check": "required", "severity": "warn", "message": "m", "columns": ["a"]},
                    {"id": "XYZ", "check": "required", "severity": "warn", "message": "m", "columns": ["a"]},
                ],
            },
            "unique",
        ),
    ],
)
def test_definitions_are_checked(raw, error):
    with pytest.raises(CheckDefinitionError, match=error):
        parse(raw, "CONSUMER", {"a", "b"})


def test_packaged_checks():
    c = load_checks("CONSUMER")
    assert c.sheet == "Consumer" and len(c.checks) >= 25
    assert {x.id for x in c.checks if x.severity == "block"} >= {
        "DUPLICATE_BORROWER",
        "ACCOUNT_NOT_IN_VIEW",
        "REQUIRED",
        "GENDER_LABEL",
        "STATE_LABEL",
    }
    c = load_checks("COMMERCIAL")
    assert {s.sheet for s in c.sheets} == {"Sanction", "BS", "AS", "RS", "GS", "SS", "CD"}
    assert {x.id for x in c.checks if x.severity == "block"} >= {"BS_LINKED", "CREDIT_TYPE_LABEL", "BS_DUPLICATE"}


def test_several_sheets_and_a_link_between_them():
    raw = {
        "sheets": [
            {
                "sheet": "A",
                "checks": [{"id": "A_REQ", "check": "required", "columns": ["a"], "severity": "block", "message": "m"}],
            },
            {
                "sheet": "B",
                "checks": [
                    {
                        "id": "B_LINK",
                        "check": "in_sheet",
                        "columns": ["b"],
                        "other_sheet": "A",
                        "other_column": "a",
                        "severity": "block",
                        "message": "m",
                    }
                ],
            },
        ]
    }
    c = parse(raw, "COMMERCIAL", {"A": {"a"}, "B": {"b"}})
    assert [s.sheet for s in c.sheets] == ["A", "B"] and [x.id for x in c.checks] == ["A_REQ", "B_LINK"]
    bad = {"sheets": [{"sheet": "B", "checks": [{**raw["sheets"][1]["checks"][0], "other_column": "zz"}]}]}
    with pytest.raises(CheckDefinitionError, match="other_sheet / other_column"):
        parse(bad, "COMMERCIAL", {"A": {"a"}, "B": {"b"}})
    with pytest.raises(CheckDefinitionError, match=r"not in inputs\.yaml"):
        parse({"sheets": [{"sheet": "Q", "checks": raw["sheets"][0]["checks"]}]}, "COMMERCIAL", {"A": {"a"}})


def test_account_level_fields_must_agree_across_an_accounts_rows(engine):
    co = {
        **ROW,
        "consumer_name": "RAVI M",
        "date_of_birth": "01011980",
        "ownership_indicator_label": "Guarantor",
        "payment_frequency_label": "Bullet",
    }
    result = checks(engine, [{**ROW, "payment_frequency_label": "Monthly"}, co])
    assert ("ACCOUNT_FIELDS_DIFFER", 3, "payment_frequency_label") in found(result) and result.passed
    message = next(i["message"] for i in result.issues if i["code"] == "DQ.ACCOUNT_FIELDS_DIFFER")
    assert "Bullet here, Monthly on row 2 (used) for A1" in message


# ---------------------------------------------------------------- commercial: every sheet keyed by the reference
REF = "301/2024-25"
SANCTION = {
    "sanction_reference_number": REF,
    "loan_account_no": "L1",
    "credit_type_label": "Medium Term Loan",
    "repayment_frequency_label": "Monthly",
    "suit_filed_status": "00",
}
BS = {
    "sanction_reference_number": REF,
    "member_branch_code": "01",
    "borrower_name": "SRI TRADERS",
    "borrowers_legal_constitution_label": "Private Limited",
}


def commercial(engine, sheets, refs=(REF, "302/2024-25")):
    with engine.begin() as conn:
        for ref in refs:
            conn.execute(text("INSERT INTO wip.VwCommercialSubmissionCR VALUES ('L', :r, :d)"), {"r": ref, "d": AS_OF})
    data = workbook("COMMERCIAL", sheets)
    load_id = load_workbook(
        engine,
        "COMMERCIAL",
        data,
        tenant_id="nbfc_001",
        file_name="m.xlsx",
        loaded_by="t",
        batch_id="b",
        clock=lambda: datetime(2026, 10, 28),
    ).load_id
    return run_checks(engine, load_id, "COMMERCIAL", AS_OF, record=False)


def test_a_linked_commercial_workbook_passes(engine):
    result = commercial(engine, {"Sanction": [SANCTION], "BS": [BS]})
    assert result.passed, result.issues


def test_commercial_problems(engine):
    result = commercial(
        engine,
        {
            "Sanction": [
                SANCTION,
                {**SANCTION, "loan_account_no": "L2", "repayment_frequency_label": "Quarterly"},
                {**SANCTION, "sanction_reference_number": "999/2020-21"},  # not in the view
                {**SANCTION, "sanction_reference_number": "302/2024-25", "credit_type_label": None},
            ],
            "BS": [
                BS,
                {**BS, "borrower_name": "SRI TRADERS PVT"},  # two borrowers
                {**BS, "sanction_reference_number": "777/2025-26"},
            ],  # no loan
            "AS": [
                {
                    "sanction_reference_number": REF,
                    "address_line_1": "1 MAIN RD",
                    "state_label": "Tamilnadu",
                    "pin_code": "600001",
                    "fax_number_s": "owner@example.com",
                }
            ],
        },
    )
    got = found(result)
    assert {
        ("SANCTION_ROWS_DIFFER", 3, "repayment_frequency_label"),
        ("SANCTION_REF_NOT_IN_VIEW", 4, "sanction_reference_number"),  # a note
        ("SANCTION_REQUIRED", 5, "credit_type_label"),
        ("BS_DUPLICATE", 3, None),
        ("BS_LINKED", 4, "sanction_reference_number"),
        ("AS_STATE_LABEL", 2, "state_label"),
        ("AS_FAX_HOLDS_EMAIL", 2, "fax_number_s"),
    } <= got
    assert not result.passed
    sheets = {i["code"]: i["sheet"] for i in result.issues}
    assert sheets["DQ.BS_LINKED"] == "BS" and sheets["DQ.SANCTION_REF_NOT_IN_VIEW"] == "Sanction"
    assert next(i for i in result.issues if i["code"] == "DQ.SANCTION_ROWS_DIFFER")["severity"] == "WARN"


def test_references_match_on_letters_and_digits(engine):
    """The workbook writes F012/2025-26, the loan view F012-2025-26: the same reference."""
    sheets = {
        "Sanction": [{**SANCTION, "sanction_reference_number": "F012/2025-26"}],
        "BS": [{**BS, "sanction_reference_number": "f012 2025/26"}],
    }
    result = commercial(engine, sheets, refs=("F012-2025-26",))
    assert result.passed and not any(
        i["code"] in ("DQ.SANCTION_REF_NOT_IN_VIEW", "DQ.BS_LINKED") for i in result.issues
    )


def test_missing_credit_type_is_a_note(engine):
    result = commercial(engine, {"Sanction": [{**SANCTION, "credit_type_label": None}], "BS": [BS]})
    assert result.passed and ("SANCTION_REQUIRED", 2, "credit_type_label") in found(result)


def test_a_reference_missing_from_the_view_is_a_note(engine):
    result = commercial(
        engine,
        {
            "Sanction": [{**SANCTION, "sanction_reference_number": "999/2020-21"}],
            "BS": [{**BS, "sanction_reference_number": "999/2020-21"}],
        },
    )
    assert result.passed and ("SANCTION_REF_NOT_IN_VIEW", 2, "sanction_reference_number") in found(result)
