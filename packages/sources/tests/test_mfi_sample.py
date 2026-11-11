"""MFI step 6 (ADR 0003, provisional workbook): the sample workbook (sources.samples) matches the layout
(canonical/types/mfi/inputs.yaml), loads, and passes the upload checks (input_checks.yaml); the checks catch the
workbook's own mistakes."""

from datetime import date, datetime

import openpyxl
import pytest

from canonical.staging.checks import run_checks
from canonical.staging.spec import load_spec
from sources.excel import load_workbook
from sources.samples import main, mfi_sample, write_workbook

AS_OF = date(2026, 10, 31)


def load(engine, rows) -> int:
    result = load_workbook(engine, "MFI", write_workbook("MFI", rows), tenant_id="nbfc_001", file_name="mfi.xlsx",
                           loaded_by="t", clock=lambda: datetime(2026, 11, 2), as_of_date=AS_OF)  # fmt: skip
    assert result.status == "LOADED", result.errors
    return result.load_id


def test_the_sample_has_the_cases_the_files_need() -> None:
    rows = mfi_sample(AS_OF)
    loans, groups = rows["Loans"], rows["Groups"]
    categories = {r["loan_category"] for r in loans}
    assert {"T05", "T03"} <= categories and categories & {"T01", "T02"}  # SHG file and JLG + Individual file
    assert any(r.get("date_closed") for r in loans) and any(r.get("account_status") == "S06" for r in loans)
    assert any(r["days_past_due"] > 0 and not r.get("account_status") for r in loans)  # delinquent
    members = [r["member_id"] for r in loans]
    assert len(members) > len(set(members))  # a member with two loans
    assert {r["group_id"] for r in loans if r["group_id"]} == {g["group_id"] for g in groups}


def test_the_sample_loads_and_passes_every_check(engine) -> None:
    load_id = load(engine, mfi_sample(AS_OF))
    checks = run_checks(engine, load_id, "MFI", AS_OF, record=False)
    assert checks.passed and checks.blocking == 0, [(i["code"], i["message"], i["source_row"]) for i in checks.issues]
    assert checks.warnings == 0, [(i["code"], i["column_name"], i["source_row"]) for i in checks.issues]


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"gender": "X"}, "DQ.GENDER_CODE"),
        ({"loan_category": "T99"}, "DQ.LOAN_CATEGORY_CODE"),
        ({"permanent_state": "Tamil Nadu"}, "DQ.STATE_CODE"),
        ({"current_balance": "1,200.50"}, "DQ.AMOUNT_FORMAT"),
        ({"member_id": None}, "DQ.REQUIRED"),
        ({"date_disbursed": "31022026"}, "DQ.DATE_FORMAT"),
        ({"date_closed": date(2020, 1, 1)}, "DQ.CLOSED_BEFORE_OPENED"),
        ({"relative_1_relation": "HUSBAND"}, "DQ.RELATION_CODE"),
    ],
)
def test_the_checks_catch_a_bad_value(engine, change, code) -> None:
    rows = mfi_sample(AS_OF)
    rows["Loans"][0] = rows["Loans"][0] | change
    checks = run_checks(engine, load(engine, rows), "MFI", AS_OF, record=False)
    assert code in {i["code"] for i in checks.issues}
    assert not checks.passed


def test_member_details_must_agree_between_their_loans(engine) -> None:
    rows = mfi_sample(AS_OF)
    rows["Loans"][-1] = rows["Loans"][-1] | {"member_name": "Someone Else"}  # the member's second loan
    checks = run_checks(engine, load(engine, rows), "MFI", AS_OF, record=False)
    assert {i["code"] for i in checks.issues} == {"DQ.MEMBER_FIELDS_DIFFER"} and checks.passed  # a warning


def test_the_cli_writes_a_sample_and_a_blank_template(tmp_path, capsys) -> None:
    assert main(["mfi", str(tmp_path / "s.xlsx"), "--as-of", "2026-10-31"]) == 0
    assert "as of 2026-10-31" in capsys.readouterr().out
    book = openpyxl.load_workbook(tmp_path / "s.xlsx")
    spec = load_spec("MFI")
    assert book.sheetnames == [s.sheet for s in spec.sheets]
    assert [c.value for c in book["Loans"][1]] == list(spec.sheets[0].columns.values())
    assert book["Loans"].max_row > 20
    assert main(["MFI", str(tmp_path / "t.xlsx"), "--blank"]) == 0
    assert openpyxl.load_workbook(tmp_path / "t.xlsx")["Groups"].max_row == 1
    assert main(["CONSUMER", str(tmp_path / "c.xlsx")]) == 2  # no sample data: only --blank
