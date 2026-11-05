"""Standalone Excel -> TUDF converter (submissions/tudf): a filled-in Data Submission Form in, the TUDF file out."""

from datetime import date

import pytest
from bc_forms import bureau_form
from bc_helpers import consumer_layout as tudf_layout
from bc_helpers import other_workbook, parse_records

from bureau_core.tudf import ConversionError, convert
from bureau_core.tudf.__main__ import main

ROW = {
    "consumer_name": "ASHA K",
    "date_of_birth": "23031981",
    "gender": "1",
    "income_tax_id_number": "BIVPA9062Q",
    "telephone_no_mobile": "9884444855",
    "email_id_1": "asha@example.com",
    "address_line_1": "12 MAIN ROAD CHENNAI",
    "state_code_1": "33",
    "pin_code_1": "600005",
    "address_category_1": "01",
    "residence_code_1": "01",
    "current_new_member_code": "CIB0000001",
    "current_new_member_short_name": "RARFINCARE",
    "curr_new_account_no": "000110710000006",
    "account_type": "05",
    "ownership_indicator": "1",
    "date_opened_disbursed": "30032022",
    "date_reported": "30092026",
    "high_credit_sanctioned_amt": "900000",
    "current_balance": "472731",
    "amt_overdue": "11705",
    "no_of_days_past_due": "11",
    "rate_of_interest": "15.5",
    "repaymenttenure": "84",
    "emi_amount": "17500",
    "payment_frequency": "03",
    "ckyc": "30062832419227",
}


def form(*rows):
    return bureau_form(rows)


def records(result):
    enc = tudf_layout().encoding
    text = result.content.decode(enc.charset)
    assert text.endswith(enc.trailer)
    return parse_records(text[tudf_layout().header_length : len(text) - len(enc.trailer)])


def segment(record, tag):
    return [s for s in record if s.tag == tag]


def test_a_form_row_becomes_a_tudf_record():
    result = convert(form(ROW), bureau="CIBIL")
    assert result.rows == 1 and result.stats.written == 1 and result.warnings == []
    assert result.member_id == "CIB0000001" and result.as_of_date == date(2026, 9, 30)
    text = result.content.decode("ascii")
    assert text.startswith("TUDF12CIB0000001") and "RARFINCARE" in text[:60]
    (rec,) = records(result)
    pn, tl = segment(rec, "PN")[0], segment(rec, "TL")[0]
    assert pn.fields["01"] == "ASHA K" and pn.fields["07"] == "23031981" and pn.fields["08"] == "1"
    ids = {s.fields["01"]: s.fields["02"] for s in segment(rec, "ID")}
    assert ids == {"01": "BIVPA9062Q", "09": "30062832419227"}  # PAN, CKYC
    assert segment(rec, "PT")[0].fields["01"] == "9884444855"
    assert segment(rec, "EC")[0].fields["01"] == "asha@example.com"
    assert tl.fields["01"] == "CIB0000001" and tl.fields["03"] == "000110710000006" and tl.fields["04"] == "05"
    assert tl.fields["13"] == "472731" and tl.fields["12"] == "900000"


def test_member_and_bureau_settings():
    other = convert(form(ROW), bureau="EXPERIAN", member_id="EXP0000009", member_short_name="RAR")
    assert other.content.decode("ascii").startswith("CONS12EXP0000009")  # bureau's header tag, member given
    with pytest.raises(ConversionError, match="member ID"):
        convert(form({**ROW, "current_new_member_code": None}))
    with pytest.raises(ConversionError, match="not one value"):
        convert(form(ROW, {**ROW, "curr_new_account_no": "2", "current_new_member_code": "CIB0000002"}))


def test_co_borrowers_share_the_account():
    co = {**ROW, "consumer_name": "RAVI M", "ownership_indicator": "2", "income_tax_id_number": "CFRPR1754Q"}
    result = convert(form(ROW, co))
    recs = records(result)
    assert len(recs) == 2 and {segment(r, "PN")[0].fields["01"] for r in recs} == {"ASHA K", "RAVI M"}
    assert {segment(r, "TL")[0].fields["03"] for r in recs} == {"000110710000006"}


def test_bad_values_are_left_out_and_reported():
    result = convert(
        form(
            {**ROW, "gender": "7", "date_of_birth": "2404199", "current_balance": "abc"},
            {"consumer_name": "no account"},
        )
    )
    assert result.rows == 1
    joined = "\n".join(result.warnings)
    assert "row 2: Gender '7' is not a gender code" in joined and "Date of Birth '2404199' is not a date" in joined
    assert "Current  Balance 'abc' is not a number" in joined and "row 3: no Curr/New Account No" in joined
    pn = segment(records(result)[0], "PN")[0]
    assert "07" not in pn.fields and "08" not in pn.fields  # left out of the file


def test_wrong_workbooks_are_refused():
    with pytest.raises(ConversionError, match="no sheet"):
        convert(other_workbook())
    with pytest.raises(ConversionError, match="lacks column"):
        convert(bureau_form(headers=["Consumer Name"]))
    with pytest.raises(ConversionError, match="no rows"):
        convert(form({"consumer_name": "no account"}))
    with pytest.raises(ConversionError, match=r"Not an \.xlsx"):
        convert(b"name,dob\n")


def test_as_of_date_defaults_to_the_latest_date_reported():
    result = convert(form(ROW, {**ROW, "curr_new_account_no": "2", "date_reported": "31102026"}))
    assert result.as_of_date == date(2026, 10, 31)
    assert convert(form(ROW), as_of_date=date(2026, 9, 1)).as_of_date == date(2026, 9, 1)


def test_command_line(tmp_path, capsys):
    path = tmp_path / "form.xlsx"
    path.write_bytes(form(ROW))
    out = tmp_path / "out.txt"
    assert main([str(path), "--bureau", "CIBIL", "--out", str(out)]) == 0
    assert out.read_bytes().startswith(b"TUDF12CIB0000001") and "1 form rows -> 1 records" in capsys.readouterr().out
    assert main([str(tmp_path / "missing.xlsx")]) == 1
