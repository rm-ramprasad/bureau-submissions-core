"""Reconciliation of a TUDF with the Excel form it was made from (bureau_core/tudf/reconcile.py)."""

import re

from bc_forms import bureau_form
from test_tudf_converter import ROW

from bureau_core.formats.excel import excel_layout
from bureau_core.tudf import convert
from bureau_core.tudf.reconcile import column_map, compare, reconcile


def form(*rows):
    return bureau_form(rows)


def tl_swap(tudf: str, a: str, b: str) -> str:
    """Swap the values of TL fields a and b in the first TL segment."""
    start = tudf.index("TLT001") + 6
    fields, i = {}, start
    while not tudf.startswith(("ES02", "PNN"), i):
        tag, n = tudf[i : i + 2], int(tudf[i + 2 : i + 4])
        fields[tag] = (tudf[i + 4 : i + 4 + n], i, i + 4 + n)
        i += 4 + n
    (va, sa, ea), (vb, sb, eb) = fields[a], fields[b]
    assert ea == sb  # adjacent fields
    return tudf[:sa] + f"{a}{len(vb):02d}{vb}" + f"{b}{len(va):02d}{va}" + tudf[eb:]


def test_every_form_column_has_exactly_one_tudf_place():
    places = column_map("CONSUMER")
    assert [p.header for p in places] == [c.header for c in excel_layout("CONSUMER").columns]
    keys = [(p.segment, p.tags, p.select_tag, p.select_value, p.rank) for p in places]
    assert len(keys) == len(set(keys)) == 72
    by_header = {p.header: p.label for p in places}
    assert by_header["Current  Balance"] == "TL 13" and by_header["Amt Overdue"] == "TL 14"
    assert by_header["Income Tax ID Number"] == "ID 02 (the ID whose ID01 = 01)"
    assert by_header["Telephone No.Mobile"] == "PT 01 (the PT whose PT03 = 01)"
    assert by_header["Address Line 2"] == "PA 01+02+03+04+05 (filled slot 2)"


def test_a_converted_form_reconciles():
    co = {
        **ROW,
        "consumer_name": "RAVI M",
        "ownership_indicator": "2",
        "income_tax_id_number": "CFRPR1754Q",
        "email_id_1": None,
        "email_id_2": "ravi@example.com",
        "address_line_2": "7 SECOND STREET",
        "state_code_2": "33",
        "pin_code_2": "600017",
    }
    excel = form(ROW, co)
    r = reconcile(excel, convert(excel).content)
    assert r.matched and r.form_rows == r.tudf_records == 2
    assert sum(c.mismatched for c in r.columns) == 0 and all(c[3] for c in r.checks)
    assert r.segment_counts["PA"] == 3 and r.segment_counts["EC"] == 2  # e-mail 2 is the only one


def test_a_value_in_the_wrong_place_is_caught():
    excel = form(ROW)
    tudf = convert(excel).content.decode("ascii")
    r = reconcile(excel, tl_swap(tudf, "13", "14").encode("ascii"))
    assert not r.matched
    assert {(m[1], m[3]) for m in r.mismatches} == {("Current  Balance", "TL 13"), ("Amt Overdue", "TL 14")}


def test_a_changed_value_and_missing_records_are_caught():
    excel = form(ROW, {**ROW, "curr_new_account_no": "2", "consumer_name": "RAVI M"})
    tudf = convert(excel).content.decode("ascii")
    changed = re.sub(r"BIVPA9062Q", "ZZZZZ9999Z", tudf, count=1)
    r = reconcile(excel, changed.encode("ascii"))
    assert not r.matched and ("Income Tax ID Number", "different number") in {(m[1], m[5]) for m in r.mismatches}
    one_record = tudf[: tudf.index("ES02**") + 6] + "TRLR"  # second record dropped
    r = reconcile(excel, one_record.encode("ascii"))
    assert not r.matched and any("no TUDF record" in m[5] for m in r.mismatches)


def test_spec_rules_are_not_mismatches():
    places = {p.header: p for p in column_map("CONSUMER")}
    assert compare(places["Date Reported"], None, "30092026", "30092026") == (
        "spec",
        "empty: the as-of date is written",
    )
    assert compare(places["Telephone No.Mobile"], "12345", None, "")[0] == "spec"
    assert compare(places["Current  Balance"], "-2451", "2451-", "") == ("match", None)
    assert compare(places["Rate of Interest"], "15.5", "15.500", "") == ("match", None)
    assert compare(places["Consumer Name"], "ASHA  K", "ASHA K", "") == ("match", None)
    assert compare(places["Curr/New Account No"], "A" * 30, "A" * 25, "") == ("spec", "cut to 25 characters")
    assert compare(places["Gender"], "1", "2", "")[0] == "mismatch"
    assert compare(places["Gender"], "1", None, "")[0] == "mismatch"
    assert compare(places["Gender"], None, "1", "")[0] == "mismatch"


def test_reconciliation_sheets():
    excel = form(ROW)
    sheets = reconcile(excel, convert(excel).content).sheets()
    assert list(sheets) == ["Reconciliation", "Column map", "Sample", "Mismatches"]
    assert sheets["Reconciliation"][0] == ["Result", "MATCHED"]
    assert len(sheets["Column map"]) == 73 and sheets["Column map"][0][0] == "Form column"
    assert ["Current  Balance", "TL", "13", "", "signed_amount"] in sheets["Column map"]
    assert len(sheets["Mismatches"]) == 1  # header only


def test_reconciliation_goes_into_the_excel_without_touching_the_form():
    import io

    import openpyxl

    from bureau_core.formats.excel.engine import add_sheets

    excel = form(ROW)
    result = reconcile(excel, convert(excel).content)
    combined = add_sheets(excel, result.sheets())
    wb = openpyxl.load_workbook(io.BytesIO(combined))
    assert wb.sheetnames == ["Data Submission Form", "Reconciliation", "Column map", "Sample", "Mismatches"]
    assert wb["Reconciliation"]["B1"].value == "MATCHED"
    assert convert(combined).content == convert(excel).content  # the form sheet is unchanged


def test_landline_numbers_compare_by_digits():
    places = {p.header: p for p in column_map("CONSUMER")}
    assert compare(places["Telephone No.Office"], "0674-2734014", "0674-2734014", "") == ("match", None)
    assert compare(places["Telephone No.Office"], "0674-2734014", "0674-2734015", "")[0] == "mismatch"
