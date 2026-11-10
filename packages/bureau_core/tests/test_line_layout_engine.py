"""The delimited engine's line layouts (ADR 0003 step 3): fixed-width header / trailer, segments joined on one line
per record, several files per request selected by a field, identical lines once, and the source paths address.,
relative., group., file.key. A small synthetic layout shaped like MFI; the MFI layout itself is step 4.
Consumer and commercial layouts are untouched (their golden tests)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace as Row

import pytest
import yaml

from bureau_core.bureau import BureauProfile
from bureau_core.data import Drops, SnapshotData
from bureau_core.formats.delimited.engine import render_files
from bureau_core.formats.delimited.formatters import VALUE_FORMATTERS
from bureau_core.formats.delimited.layout import LayoutError, parse_line_layout

LAYOUT = """
layout: TEST-LINES
encoding: {delimiter: "|", line_end: "\\n", trailing_delimiter: false, charset: ascii, strip_chars: ""}
header:
  tag: HDR
  fields: 4
  fixed: true
  length: 24
  field_list:
    - {source: bureau.member_id, width: 10, check: none}
    - {source: run.as_of_date, format: ddmmyyyy, width: 8}
    - {source: file.key, width: 3}
trailer:
  tag: TRL
  fields: 3
  fixed: true
  length: 13
  field_list:
    - {const: "3.3", width: 3}
    - {source: count.ACTCRD, format: int, width: 7, align: right, pad: "0"}
segments:
  grp:
    tag: GRPCRD
    from: group
    fields: 3
    field_list:
      - group_key
      - full_name
  cns:
    tag: CNSCRD
    from: party
    fields: 7
    field_list:
      - party_key
      - {source: full_name, format: word_part, args: {widths: [10, 5, 5], part: 1}}
      - {source: full_name, format: word_part, args: {widths: [10, 5, 5], part: 2}}
      - {source: full_name, format: word_part, args: {widths: [10, 5, 5], part: 3}}
      - {source: relative.NOMINEE.1.full_name}
      - {source: address.PERMANENT.pin_code}
  act:
    tag: ACTCRD
    from: account
    fields: 4
    field_list:
      - account_number
      - {source: current_balance, format: signed_amount_truncated, check: not_null}
      - {source: days_past_due, format: int3, cap: 999, check: not_null}
files:
  - key: SHG
    select: {account.loan_category: [SHG_GROUP, SHG_INDIVIDUAL]}
    record_line: [grp, cns, act]
    columns: 14
  - key: JLG
    select: {account.loan_category: [JLG_INDIVIDUAL, INDIVIDUAL]}
    record_line: [cns, act]
    columns: 11
uses:
  - {rows: party_address, by: party_key}
  - {rows: party_relative, by: party_key}
  - {rows: borrower_group, by: group_key}
"""


def layout(text: str = LAYOUT):
    return parse_line_layout(yaml.safe_load(text))


def _data(accounts, accepted, children=None) -> SnapshotData:
    parties = {
        "M1": Row(party_key="M1", full_name="Lakshmi Devi Ramaswamy Iyer", row_id=1),
        "M2": Row(party_key="M2", full_name="Anu", row_id=2),
    }
    return SnapshotData(
        parties=parties, accounts=accounts, holders={}, children=children or {}, accepted=accepted, rejected=0,
        drops=Drops(),
    )  # fmt: skip


def _account(key, category, balance="1000.99", dpd=5, group=None):
    return Row(account_key=key, account_number=key, loan_category=category, current_balance=Decimal(balance),
               days_past_due=dpd, group_key=group, row_id=1)  # fmt: skip


BUREAU = BureauProfile("t", "CIBIL", "MFI", "CIBIL", "NB12345678", None, "", "x.txt")


def render(data, lay=None):
    return render_files(data, date(2027, 7, 31), BUREAU, "ME", lay or layout(), {}, date(2027, 8, 2))


def test_two_files_one_line_per_record_fixed_header_and_trailer():
    accounts = {"L1": _account("L1", "SHG_INDIVIDUAL", group="G1"), "L2": _account("L2", "JLG_INDIVIDUAL", "-10.5", 1200),
                "L3": _account("L3", "INDIVIDUAL")}  # fmt: skip
    children = {
        "borrower_group": {"G1": [Row(group_key="G1", full_name="Jyothi SHG", row_id=1)]},
        "party_relative": {"M1": [Row(relative_role="NOMINEE", priority=1, full_name="Ravi", row_id=1)]},
        "party_address": {"M1": [Row(address_category="CURRENT", pin_code="560001", priority=1, row_id=1),
                                 Row(address_category="PERMANENT", pin_code="560062", priority=2, row_id=2)]},
    }  # fmt: skip
    files = render(_data(accounts, [("L1", "M1"), ("L2", "M1"), ("L3", "M2")], children))
    assert set(files) == {"SHG", "JLG"}
    shg = files["SHG"][0].decode().split("\n")
    assert shg[0] == "HDRNB12345678" + "31072027" + "SHG" and len(shg[0]) == 24
    assert shg[1] == "GRPCRD|G1|Jyothi SHG|CNSCRD|M1|Lakshmi|Devi||Ravi|560062|ACTCRD|L1|1000|005"
    assert shg[2] == "TRL3.30000001" and len(shg[2]) == 13
    jlg = files["JLG"][0].decode().split("\n")
    assert jlg[0].endswith("JLG")
    assert jlg[1] == "CNSCRD|M1|Lakshmi|Devi||Ravi|560062|ACTCRD|L2|-10|999"  # truncated, DPD capped
    assert jlg[2] == "CNSCRD|M2|Anu|||||ACTCRD|L3|1000|005" and jlg[3] == "TRL3.30000002"
    assert all(line.count("|") == 13 for line in shg[1:2]) and all(line.count("|") == 10 for line in jlg[1:3])
    assert (files["JLG"][1].total_records, files["JLG"][1].written) == (2, 2)


def test_empty_files_are_left_out_and_identical_lines_written_once():
    accounts = {"L2": _account("L2", "JLG_INDIVIDUAL")}
    data = _data(accounts, [("L2", "M2"), ("L2", "M2")])
    files = render(data)
    assert list(files) == ["JLG"]  # no SHG record: no SHG file
    content, stats = files["JLG"]
    assert content.decode().count("CNSCRD") == 1 and (stats.written, stats.duplicates) == (1, 1)


def test_a_missing_group_gives_empty_group_fields_not_an_error():
    files = render(_data({"L1": _account("L1", "SHG_GROUP", group="NOPE")}, [("L1", "M2")]))
    assert files["SHG"][0].decode().split("\n")[1].startswith("GRPCRD|||CNSCRD|M2|")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("length: 24", "length: 25"), "declares length 25, the widths add up to 24"),
        (("columns: 11", "columns: 12"), "declares 12 columns, the segments give 11"),
        (("record_line: [cns, act]", "record_line: [cns, nope]"), "unknown segments"),
        (('- {const: "3.3", width: 3}', '- {const: "3.3"}'), "gives every field a width"),
        (("from: group", "from: nowhere"), "from must be one of"),
        (("key: JLG", "key: SHG"), "duplicate file keys"),
    ],
)
def test_layout_mistakes_are_refused_on_load(change, message):
    with pytest.raises(LayoutError, match=message):
        layout(LAYOUT.replace(*change))


@pytest.mark.parametrize(
    ("value", "part", "want"),
    [
        ("Lakshmi Devi Ramaswamy Iyer", 1, "Lakshmi"),  # 'Lakshmi Devi' is 12 > 10: Devi moves on
        ("Lakshmi Devi Ramaswamy Iyer", 2, "Devi"),
        ("Lakshmi Devi Ramaswamy Iyer", 3, None),  # Ramaswamy (9) fits no 5-wide field: packing stops there
        ("Anu", 2, None),
    ],
)
def test_word_part_never_splits_a_word(value, part, want):
    assert VALUE_FORMATTERS["word_part"](value, None, {"widths": [10, 5, 5], "part": part}) == want


def test_truncated_amounts():
    f = VALUE_FORMATTERS
    assert (
        f["amount_truncated"](Decimal("1234.99"), None, {}),
        f["signed_amount_truncated"](Decimal("-10.9"), None, {}),
    ) == ("1234", "-10")
    assert f["amount_truncated"](Decimal("-1"), None, {}) is None
