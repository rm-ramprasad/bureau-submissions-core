"""MFI step 4 (ADR 0003): types/mfi/layout.yaml rendered by the generic engine on sample members and accounts, with
the MFI translations from types/mfi/codes.yaml. Checks the spec's shapes (1.2, 3, 7A) and the derived values."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as Row

import pytest

from bureau_core.bureau import BureauProfile
from bureau_core.data import Drops, SnapshotData
from bureau_core.formats.delimited.engine import render_files
from bureau_core.formats.delimited.layout import load_line_layout
from bureau_core.types import check_files

MFI = Path(__file__).resolve().parents[1] / "src" / "bureau_core" / "types" / "mfi"
AS_OF, CREATED = date(2027, 7, 31), date(2027, 8, 2)
BUREAU = BureauProfile("t", "CIBIL", "MFI", "CIBIL", "NB12345678", "OPEN TERRACE MFI", "", "x.txt")


def translations() -> dict[str, dict[str, str]]:
    _, tr, _, _ = check_files(MFI)
    return {name: {value: t.output_code for value, t in values.items()} for name, values in tr.items()}


def party(key, name, **kw):
    base = dict(party_key=key, full_name=name, branch_id="BR1", centre_id="KEN-7", group_key="G1", old_party_key=None,
                birth_date=date(1990, 1, 2), age=None, age_as_of=None, gender="FEMALE", marital_status="MARRIED",
                education="PASSED_10TH", occupation="HOME_MAKER", family_income=Decimal("12000.75"),
                family_expenses=None, religion="HINDU", social_category="GENERAL", member_role="MEMBER",
                member_status="ACTIVE", bank_name=None, bank_branch=None, bank_account_number=None, row_id=1)  # fmt: skip
    return Row(**(base | kw))


def account(key, category, **kw):
    base = dict(account_key=key, account_number=key, unique_reference=None, branch_code="BR1", centre_id="KEN-7",
                loan_officer="E77", loan_category=category, group_key="G1", loan_cycle=2, loan_purpose="Dairy",
                credit_facility_status=None, sanctioned_date=None, date_opened=date(2026, 1, 15), date_closed=None,
                date_last_payment=date(2027, 7, 20), applied_amount=None, sanctioned_amount=Decimal("50000"),
                high_credit_amount=Decimal("50000"), repayment_tenure=52, payment_frequency="WEEKLY",
                emi_amount=Decimal("1100.90"), current_balance=Decimal("20500.60"), amount_overdue=Decimal("0"),
                days_past_due=0, written_off_total=None, write_off_date=None, write_off_reason=None,
                loan_type="TERM_LOAN", old_unique_reference=None, insurance_indicator="YES", insurance_type="CREDIT",
                sum_assured=Decimal("50000"), old_account_number=None, settlement_amount=None, row_id=1)  # fmt: skip
    return Row(**(base | kw))


def children():
    return {
        "borrower_group": {
            "G1": [
                Row(
                    group_key="G1",
                    full_name="Jyothi SHG",
                    group_account_number="GL-1",
                    disbursed_amount=Decimal("500000"),
                    current_balance=Decimal("-12.5"),
                    date_opened=date(2026, 1, 1),
                    date_closed=None,
                    date_last_payment=None,
                    repayment_tenure=24,
                    payment_frequency="MONTHLY",
                    emi_amount=Decimal("22000"),
                    amount_overdue=None,
                    days_past_due=1500,
                    loan_cycle=1,
                    loan_type="TERM_LOAN",
                    written_off_total=None,
                    write_off_date=None,
                    write_off_reason=None,
                    address_line_1="Ward 4, Hosur Road",
                    state_code="KARNATAKA",
                    pin_code="560-068",
                    phone_type=None,
                    phone_number="9876500001",
                    bank_name=None,
                    bank_branch=None,
                    bank_account_number=None,
                    bank_balance=None,
                    formed_date=date(2020, 5, 1),
                    first_linkage_date=None,
                    govt_group_id="NRLM-KA-77",
                    govt_program=None,
                    row_id=1,
                )
            ]
        },
        "party_identifier": {
            "M1": [
                Row(id_type="AADHAAR", id_number="2345 6789 0123", row_id=1),
                Row(id_type="PAN", id_number="ABCPD1234F", row_id=2),
            ]
        },
        "party_phone": {
            "M1": [
                Row(phone_number="9876543210", phone_type=None, priority=1, row_id=1),
                Row(phone_number="0802222333", phone_type="HOME", priority=2, row_id=2),
            ]
        },
        "party_email": {"M1": [Row(email="Lakshmi.D@Example.com", priority=1, row_id=1)]},
        "party_address": {
            "M1": [
                Row(
                    address_category="PERMANENT",
                    address_line_1="12, 2nd Cross",
                    address_line_2="Jayanagar",
                    address_line_3=None,
                    address_line_4=None,
                    address_line_5=None,
                    state_code="KARNATAKA",
                    pin_code="560011",
                    priority=1,
                    row_id=1,
                )
            ]
        },
        "party_relative": {
            "M1": [
                Row(relative_role="NOMINEE", priority=1, full_name="Anu", relation="DAUGHTER", row_id=1),
                Row(relative_role="RELATIVE", priority=1, full_name="Ravi", relation="HUSBAND", row_id=2),
                Row(relative_role="KEY_PERSON", priority=1, full_name="Ravi", relation="HUSBAND", row_id=3),
            ]
        },
    }


@pytest.fixture(scope="module")
def files():
    accounts = {
        "S1": account("S1", "SHG_INDIVIDUAL"),
        "J1": account("J1", "JLG_INDIVIDUAL", days_past_due=45, amount_overdue=Decimal("2200")),
        "J2": account("J2", "INDIVIDUAL", group_key=None, date_closed=date(2027, 7, 1), current_balance=Decimal("0")),
        "J3": account("J3", "OTHERS", credit_facility_status="WRITTEN_OFF", written_off_total=Decimal("9000.99")),
    }
    data = SnapshotData(
        parties={"M1": party("M1", "Lakshmi Devi"), "M2": party("M2", "Sita")},
        accounts=accounts, holders={}, children=children(),
        accepted=[("S1", "M1"), ("J1", "M1"), ("J2", "M2"), ("J3", "M2")], rejected=0, drops=Drops(),
    )  # fmt: skip
    layout = load_line_layout(MFI / "layout.yaml")
    return {k: c.decode("ascii").split("\r\n") for k, (c, _s) in
            render_files(data, AS_OF, BUREAU, "ME", layout, translations(), CREATED).items()}  # fmt: skip


def cols(line: str) -> list[str]:
    return line.split("|")


def test_two_files_with_the_spec_shapes(files):
    assert set(files) == {"SHG", "JLG"}
    for key, columns in (("SHG", 136), ("JLG", 100)):
        lines = files[key]
        assert lines[-1] == ""  # the file ends with a line end
        header, body, trailer = lines[0], lines[1:-2], lines[-2]
        assert len(header) == 190 and header.startswith("HDR     3.3NB12345678OPEN TERRACE MFI")
        assert header[81:89] == "31072027" and header[89:97] == "02082027" and header[97:100] == key
        assert header[160:170].rstrip() == "ME"
        assert len(trailer) == 36 and trailer == "TRL3.3NB12345678" + " " * 20
        assert all(len(cols(line)) == columns for line in body)


def test_loan_categories_go_to_their_file(files):
    shg = [cols(x) for x in files["SHG"][1:-2]]
    jlg = [cols(x) for x in files["JLG"][1:-2]]
    assert [c[36 + 64] for c in shg] == ["S1"]  # ACTCRD #65 in the SHG line (GRPCRD 36 columns first)
    assert sorted(c[64] for c in jlg) == ["J1", "J2", "J3"]  # T02, T03 and T08 (others: ADR 0003 #4)


def test_shg_line_group_member_address_account(files):
    c = cols(files["SHG"][1])
    g, m = c[:36], c[36:]
    assert g[:7] == ["GRPCRD", "G1", "Jyothi SHG", "GL-1", "500000", "-12", "01012026"]
    assert (g[13], g[20], g[21], g[22], g[23], g[31]) == ("999", "KA", "560068", "P08", "9876500001", "U99")
    # member: names packed, relatives by role, IDs cleaned, phone type defaulted, codes translated, amounts cut
    assert m[:9] == ["CNSCRD", "M1", "BR1", "KEN-7", "G1", "Lakshmi Devi", "", "", ""]
    assert (m[9], m[12], m[13]) == ("02011990", "F", "M01")
    assert (m[14], m[15], m[16], m[17], m[24], m[25]) == ("Ravi", "K02", "Ravi", "K02", "Anu", "K05")
    assert (m[28], m[29]) == ("234567890123", "ABCPD1234F")
    assert (m[37], m[38], m[39], m[40]) == ("P08", "9876543210", "P01", "0802222333")
    assert (m[41], m[47], m[48], m[50], m[51], m[52], m[54]) == ("Y04", "Z01", "12000", "R01", "V05", "L03", "W01")
    # address: permanent; current falls back to it; e-mail lowercased
    assert m[55:63] == ["ADRCRD", "12, 2nd Cross, Jayanagar", "KA", "560011", "12, 2nd Cross, Jayanagar", "KA",
                        "560011", "lakshmi.d@example.com"]  # fmt: skip
    a = m[63:]
    assert a[:10] == ["ACTCRD", "S1", "S1", "BR1", "KEN-7", "E77", "31072027", "T05", "G1", "2"]
    assert (a[11], a[19], a[20], a[22], a[23], a[24], a[25]) == ("S04", "50000", "52", "1100", "20500", "0", "000")
    assert (a[29], a[31], a[32], a[33]) == ("C01", "Y", "L02", "50000")


@pytest.mark.parametrize(("account_key", "status"), [("J1", "S05"), ("J2", "S07"), ("J3", "S06")])
def test_account_status_derived_or_translated(files, account_key, status):
    line = next(cols(x) for x in files["JLG"][1:-2] if cols(x)[64] == account_key)
    assert line[63 + 11] == status


def test_written_off_amount_is_cut_and_dpd_padded(files):
    j1 = next(cols(x) for x in files["JLG"][1:-2] if cols(x)[64] == "J1")
    j3 = next(cols(x) for x in files["JLG"][1:-2] if cols(x)[64] == "J3")
    assert (j1[63 + 24], j1[63 + 25]) == ("2200", "045")
    assert j3[63 + 26] == "9000"
