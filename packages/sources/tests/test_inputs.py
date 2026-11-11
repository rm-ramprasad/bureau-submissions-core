"""Input workbooks -> staging tables (sources.excel, canonical.staging)."""

import io
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import func, select
from src_helpers import workbook

from canonical.staging.spec import SpecError, column_name, load_spec, normalise_header, parse
from canonical.staging.tables import STAGING_TABLES, input_issue, input_load
from sources.excel import cell_text, load_workbook, purge_loads

TEMPLATES = Path(os.environ.get("BSC_WORKBOOK_DIR", "/nonexistent"))  # local input templates (never committed)
NOW = datetime(2026, 10, 28, 9, 0)


# ---------------------------------------------------------------- values and headers
@pytest.mark.parametrize(
    "value, text",
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("\xa0", None),
        (" \xa0 ", None),
        ("  ABC  ", "ABC"),
        ("A\xa0B", "A B"),
        (600054, "600054"),
        (600054.0, "600054"),
        (15.5, "15.5"),
        (0, "0"),
        (True, "1"),
        (datetime(2026, 9, 30), "2026-09-30"),
        (datetime(2026, 9, 30, 14, 5, 0), "2026-09-30 14:05:00"),
        (date(2026, 9, 30), "2026-09-30"),
        ("079", "079"),
        ("30062026", "30062026"),
    ],
)
def test_cell_text(value, text):
    assert cell_text(value) == text


def test_headers_match_ignoring_case_spacing_and_quotes():
    assert normalise_header("Borrower‟s  Name") == normalise_header("borrower's name")
    assert normalise_header("Overdue Bucket 01\n( 1 – 30 days)") == "overdue bucket 01 ( 1 – 30 days)"
    assert normalise_header(" Telephone No.Other ") == "telephone no.other"
    assert column_name("Overdue Bucket 01\n( 1 – 30 days)") == "overdue_bucket_01_1_30_days"
    assert column_name("Service Tax #") == "service_tax_no" and column_name("1st") == "c_1st"


@pytest.mark.parametrize(
    "data, error",
    [
        ({"workbook": "x"}, "non-empty 'sheets'"),
        ({"workbook": "x", "sheets": [{"sheet": "A", "table": "stg_a", "key": "k"}]}, "exactly sheet, table, key"),
        ({"workbook": "x", "sheets": [{"sheet": "A", "table": "a", "key": "k", "columns": {"k": "K"}}]}, "stg_"),
        (
            {"workbook": "x", "sheets": [{"sheet": "A", "table": "stg_a", "key": "k", "columns": {"K!": "K"}}]},
            "invalid",
        ),
        (
            {"workbook": "x", "sheets": [{"sheet": "A", "table": "stg_a", "key": "k", "columns": {"load_id": "K"}}]},
            "invalid",
        ),
        (
            {
                "workbook": "x",
                "sheets": [{"sheet": "A", "table": "stg_a", "key": "k", "columns": {"a": "K", "b": " k"}}],
            },
            "distinct",
        ),
        ({"workbook": "x", "sheets": [{"sheet": "A", "table": "stg_a", "key": "z", "columns": {"a": "A"}}]}, "key"),
        (
            {
                "workbook": "x",
                "sheets": [
                    {"sheet": "A", "table": "stg_a", "key": "a", "columns": {"a": "A"}},
                    {"sheet": "A", "table": "stg_b", "key": "a", "columns": {"a": "A"}},
                ],
            },
            "twice",
        ),
    ],
)
def test_layout_is_checked(data, error):
    with pytest.raises(SpecError, match=error):
        parse(data, "X")


def test_packaged_layouts():
    consumer, commercial = load_spec("CONSUMER"), load_spec("COMMERCIAL")
    assert [s.sheet for s in consumer.sheets] == ["Consumer"] and len(consumer.sheets[0].columns) == 55
    assert {"gender_label", "state_code_1_label", "account_type", "account_type_label"} <= set(
        consumer.sheets[0].columns
    )
    assert [s.sheet for s in commercial.sheets] == ["BS", "AS", "RS", "GS", "SS", "Sanction", "NeSL_Security", "CD"]
    assert all(s.key == "sanction_reference_number" for s in commercial.sheets)  # every sheet links by reference
    sanction = next(s for s in commercial.sheets if s.sheet == "Sanction")
    assert {"sanction_reference_number", "loan_account_no", "credit_type_label"} <= set(sanction.columns)
    assert set(STAGING_TABLES) == {"CONSUMER", "COMMERCIAL", "MFI"}
    mfi = load_spec("MFI")  # provisional (ADR 0003 step 6): Loans by account, Groups by group
    assert [(s.sheet, s.key) for s in mfi.sheets] == [("Loans", "account_number"), ("Groups", "group_id")]


# ---------------------------------------------------------------- loading
def load(engine, submission_type, data, **kw):
    return load_workbook(
        engine,
        submission_type,
        data,
        tenant_id="nbfc_001",
        file_name="in.xlsx",
        loaded_by="asha.k",
        batch_id="b-1",
        clock=lambda: NOW,
        **kw,
    )


def staged(engine, submission_type, sheet):
    table = STAGING_TABLES[submission_type][sheet]
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(select(table).order_by(table.c.source_row))]


def test_commercial_sheets_load_into_their_tables(engine):
    data = workbook(
        "COMMERCIAL",
        {
            "BS": [
                {
                    "sanction_reference_number": 19,
                    "flag": 1,
                    "borrower_name": " AANAND MEDICALS\xa0",
                    "pan": "BXEPS1434E",
                    "tin": "\xa0",
                    "date_of_incorporation": 1102021,
                },
                {},  # empty row: skipped
                {"sanction_reference_number": "34", "borrower_name": "THE NEW LINE"},
            ],
            "AS": [
                {"sanction_reference_number": 19, "pin_code": 600054.0, "country_label": "India"},
                {"sanction_reference_number": 19, "pin_code": "600045"},
            ],
            "Sanction": [
                {
                    "sanction_reference_number": "24/2023-24",
                    "loan_account_no": "000110410000080",
                    "date_of_last_acknowledgement": datetime(2023, 3, 30),
                }
            ],
        },
    )
    result = load(engine, "COMMERCIAL", data)
    assert result.status == "LOADED" and result.errors == []
    assert result.row_counts == {
        "BS": 2,
        "AS": 2,
        "RS": 0,
        "GS": 0,
        "SS": 0,
        "Sanction": 1,
        "NeSL_Security": 0,
        "CD": 0,
    }
    bs = staged(engine, "COMMERCIAL", "BS")
    assert [(r["load_id"], r["source_row"], r["sanction_reference_number"]) for r in bs] == [
        (result.load_id, 2, "19"),
        (result.load_id, 4, "34"),
    ]
    assert bs[0]["borrower_name"] == "AANAND MEDICALS" and bs[0]["tin"] is None and bs[0]["flag"] == "1"
    assert bs[0]["date_of_incorporation"] == "1102021"  # as received: leading zero lost in Excel
    assert [r["pin_code"] for r in staged(engine, "COMMERCIAL", "AS")] == ["600054", "600045"]
    assert staged(engine, "COMMERCIAL", "AS")[0]["country_label"] == "India"
    assert staged(engine, "COMMERCIAL", "Sanction")[0]["date_of_last_acknowledgement"] == "2023-03-30"
    with engine.connect() as conn:
        row = conn.execute(select(input_load)).one()
    assert (row.status, row.tenant_id, row.submission_type, row.batch_id, row.loaded_by) == (
        "LOADED",
        "nbfc_001",
        "COMMERCIAL",
        "b-1",
        "asha.k",
    )
    assert json.loads(row.row_counts)["BS"] == 2 and len(row.sha256) == 64


def test_rows_and_values_are_reported_not_fatal(engine):
    data = workbook(
        "COMMERCIAL",
        {
            "BS": [
                {"borrower_name": "NO KEY"},
                {"sanction_reference_number": 1, "pan": "#N/A"},
                {"sanction_reference_number": 2, "cin": "X" * 1200},
                {"sanction_reference_number": "9" * 150},
            ]
        },
    )
    result = load(engine, "COMMERCIAL", data)
    assert result.status == "LOADED"
    assert [(i.severity, i.code, i.source_row, i.column_name) for i in result.issues] == [
        ("WARN", "KEY_MISSING", 2, "sanction_reference_number"),
        ("WARN", "EXCEL_ERROR", 3, "pan"),
        ("WARN", "VALUE_TOO_LONG", 4, "cin"),
        ("WARN", "VALUE_TOO_LONG", 5, "sanction_reference_number"),
    ]
    bs = staged(engine, "COMMERCIAL", "BS")
    assert (
        [r["sanction_reference_number"] for r in bs] == ["1", "2", "9" * 100]
        and bs[0]["pan"] is None
        and len(bs[1]["cin"]) == 1000
    )
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(input_issue)).scalar() == 4


def test_unlisted_columns_are_reported_and_ignored(engine):
    spec = next(s for s in load_spec("COMMERCIAL").sheets if s.sheet == "SS")
    headers = {"SS": [*spec.columns.values(), "New Column", "Formula", None]}
    result = load(engine, "COMMERCIAL", workbook("COMMERCIAL", headers=headers))
    assert result.status == "LOADED"
    assert [(i.code, i.sheet) for i in result.issues] == [("COLUMN_UNMAPPED", "SS")]


def test_headers_may_differ_in_case_spacing_and_quotes(engine):
    spec = next(s for s in load_spec("COMMERCIAL").sheets if s.sheet == "BS")
    headers = {"BS": [h.upper().replace("‟", "'") + "  " for h in spec.columns.values()]}
    assert load(engine, "COMMERCIAL", workbook("COMMERCIAL", headers=headers)).status == "LOADED"


@pytest.mark.parametrize(
    "kw, code",
    [
        ({"drop": ["GS"]}, "SHEET_MISSING"),
        ({"hidden": ["Sanction"]}, "SHEET_HIDDEN"),
        ({"headers": {"SS": ["Sanction Reference Number", "Flag"]}}, "COLUMN_MISSING"),
        (
            {
                "headers": {
                    "CD": [
                        *next(s for s in load_spec("COMMERCIAL").sheets if s.sheet == "CD").columns.values(),
                        "sanction reference number",
                    ]
                }
            },
            "DUPLICATE_HEADER",
        ),
    ],
)
def test_layout_problems_stop_the_load_and_keep_nothing(engine, kw, code):
    data = workbook("COMMERCIAL", {"BS": [{"sanction_reference_number": 1}]}, **kw)
    result = load(engine, "COMMERCIAL", data)
    assert result.status == "FAILED" and result.row_counts == {} and [i.code for i in result.errors] == [code]
    assert staged(engine, "COMMERCIAL", "BS") == []  # all sheets or nothing
    with engine.connect() as conn:
        row = conn.execute(select(input_load)).one()
        assert row.status == "FAILED" and row.error_message
        assert conn.execute(select(input_issue.c.code)).scalars().all() == [code]


def test_hidden_sheets_are_never_read(engine):
    data = workbook(
        "COMMERCIAL", {"BS": [{"sanction_reference_number": 1}]}, extra_sheets=["Audit Framework", "Master Data"]
    )
    wb = openpyxl.load_workbook(io.BytesIO(data))
    for name in ("Audit Framework", "Master Data"):
        wb[name].sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    result = load(engine, "COMMERCIAL", buf.getvalue())
    assert result.status == "LOADED" and "Audit Framework" not in result.row_counts and result.issues == []


def test_not_a_workbook(engine):
    result = load(engine, "CONSUMER", b"name,dob\nA,1\n")
    assert result.status == "FAILED" and [i.code for i in result.errors] == ["NOT_A_WORKBOOK"]


def test_each_upload_is_its_own_load(engine):
    data = workbook(
        "CONSUMER",
        {
            "Consumer": [
                {"curr_new_account_no": "000110710000006", "consumer_name": "ARIVUMANI CK", "gender_label": "Male"}
            ]
        },
    )
    first, second = load(engine, "CONSUMER", data), load(engine, "CONSUMER", data)
    assert first.load_id != second.load_id
    rows = staged(engine, "CONSUMER", "Consumer")
    assert {r["load_id"] for r in rows} == {first.load_id, second.load_id}
    assert rows[0]["consumer_name"] == "ARIVUMANI CK" and rows[0]["gender_label"] == "Male"


def test_retention_purges_staged_rows_and_keeps_the_header(engine):
    data = workbook("COMMERCIAL", {"BS": [{"sanction_reference_number": 1}, {"pan": "no key"}]})
    old = load(engine, "COMMERCIAL", data)
    assert purge_loads(engine, NOW - timedelta(days=1)) == []
    assert purge_loads(engine, NOW + timedelta(days=1)) == [old.load_id]
    assert staged(engine, "COMMERCIAL", "BS") == []
    with engine.connect() as conn:
        assert conn.execute(select(input_load.c.status)).scalar() == "PURGED"
        assert conn.execute(select(func.count()).select_from(input_issue)).scalar() == 0
    assert purge_loads(engine, NOW + timedelta(days=1)) == []


# ---------------------------------------------------------------- the sample templates (input-templates/)
@pytest.mark.workbook
@pytest.mark.parametrize(
    "submission_type, file, expected",
    [
        ("CONSUMER", "Consumer_Submissions_Workbook_Staging.xlsx", {"Consumer": 417}),
        (
            "COMMERCIAL",
            "Commercial_Submissions_Workbook_Staging.xlsx",
            {"BS": 187, "AS": 240, "RS": 198, "GS": 409, "SS": 202, "Sanction": 237, "NeSL_Security": 223, "CD": 0},
        ),  # BS and RS each repeat one row exactly
    ],
)
def test_sample_templates_load(engine, submission_type, file, expected):
    result = load(engine, submission_type, (TEMPLATES / file).read_bytes())
    assert result.status == "LOADED", result.errors
    assert result.row_counts == expected
    assert all(i.severity == "WARN" for i in result.issues)


def test_values_are_trimmed_and_repeated_rows_loaded_once(engine):
    data = workbook(
        "COMMERCIAL",
        {
            "BS": [
                {"sanction_reference_number": " 16 ", "borrower_name": "AANAND MEDICALS"},
                {"sanction_reference_number": "16", "borrower_name": "  AANAND MEDICALS  "},
                {"sanction_reference_number": "17", "borrower_name": "AANAND MEDICALS"},
            ]
        },
    )
    result = load(engine, "COMMERCIAL", data)
    assert result.status == "LOADED" and result.row_counts["BS"] == 2
    assert [(i.code, i.source_row) for i in result.issues] == [("DUPLICATE_ROW", 3)]
    assert "the same as row 2" in result.issues[0].message and result.issues[0].severity == "WARN"
    assert [(r["source_row"], r["sanction_reference_number"]) for r in staged(engine, "COMMERCIAL", "BS")] == [
        (2, "16"),
        (4, "17"),
    ]
