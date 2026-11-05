"""The generic TUDF engine and layout checks, on small hand-made layouts (no database)."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace as Row

import pytest
import yaml
from bc_helpers import consumer_layout

from bureau_core.data import Drops, SnapshotData
from bureau_core.formats.tudf.engine import Renderer
from bureau_core.formats.tudf.layout import LayoutError, parse_layout
from bureau_core.formats.tudf.parser import parse_records

BASE = """
layout: TEST
encoding: { record_end: "ES02**", trailer: "TRLR" }
header:
  length: 12
  fields:
    - { name: tag, source: bureau.header_segment_tag, width: 4 }
    - { name: date, source: run.as_of_date, format: ddmmyyyy, width: 8 }
segments:
  - tag: PN
    seq: "N01"
    from: party
    fields:
      - { tag: "01", source: full_name, format: text }
      - { tag: "02", source: nickname, default: "NONE" }
  - tag: PT
    seq: "T{n:02d}"
    from: rows
    rows: party_phone
    by: party_key
    order_by: [priority, "-row_id"]
    max: 2
    fields:
      - { tag: "01", source: number, check: none, format: raw, required: true }
  - tag: TL
    seq: "T001"
    from: account
    fields:
      - { tag: "01", source: dpd, cap: 900, format: int }
      - { tag: "02", source: limit, format: amount, when: { account.kind: ["card"] } }
      - { tag: "03", source: paid, format: amount, only_positive: true }
      - tag: "04"
        alternatives:
          - { source: dpd, check: not_null, format: int3 }
          - { source: grade, check: not_null, format: lookup, args: { map: { "1": STD } } }
"""


def layout(text: str = BASE, **changes):
    raw = yaml.safe_load(text)
    for k, v in changes.items():
        raw[k] = v
    return parse_layout(raw)


def render(lay, party=None, account=None, phones=(), drops=None, translations=None):
    party = party or Row(row_id=1, party_key="P1", full_name="Asha  Kumar", nickname=None)
    account = account or Row(
        row_id=1, account_key="A1", dpd=1200, limit=Decimal("5000"), paid=Decimal("0"), kind="loan", grade=None
    )
    data = SnapshotData(
        parties={"P1": party},
        accounts={"A1": account},
        holders={("A1", "P1"): Row(row_id=1)},
        children={"party_phone": {"P1": list(phones)}},
        accepted=[("A1", "P1")],
        rejected=0,
        drops=drops or Drops(),
    )
    r = Renderer(
        lay,
        data,
        Row(header_segment_tag="TUDF", member_id="M1"),
        Row(as_of_date=date(2026, 9, 30), cycle=None),
        translations,
    )
    return r, r.record("A1", "P1")


def test_fields_defaults_caps_conditions_and_alternatives():
    _, rec = render(layout())
    assert rec == "PNN01" + "0110Asha Kumar" + "0204NONE" + "TLT001" + "0103900" + "04041200" + "ES02**"
    card = Row(row_id=1, account_key="A1", dpd=None, limit=Decimal("5000.4"), paid=Decimal("10"), kind="card", grade=1)
    _, rec = render(layout(), account=card)
    assert "TLT001" + "02045000" + "030210" + "0403STD" in rec  # when, only_positive, lookup


def test_translate_writes_the_file_code_after_defaults():
    text = BASE.replace(
        '- { tag: "02", source: nickname, default: "NONE" }',
        '- { tag: "02", source: gender, translate: gender, default: FEMALE }',
    )
    party = Row(row_id=1, party_key="P1", full_name="Asha", nickname=None, gender="MALE")
    codes = {"gender": {"MALE": "2", "FEMALE": "1"}}
    assert render(layout(text), party=party, translations=codes)[1].startswith("PNN01" + "0104Asha" + "02012")
    party.gender = None  # default is canonical too
    assert render(layout(text), party=party, translations=codes)[1].startswith("PNN01" + "0104Asha" + "02011")
    party.gender = "TRANSGENDER"  # no active code: field left out
    assert render(layout(text), party=party, translations=codes)[1].startswith("PNN01" + "0104Asha" + "TL")


def test_repeating_segments_order_max_required_and_drops():
    phones = [
        Row(row_id=1, party_key="P1", number="111", priority=2),
        Row(row_id=2, party_key="P1", number=None, priority=1),
        Row(row_id=3, party_key="P1", number="333", priority=2),
        Row(row_id=4, party_key="P1", number="444", priority=None),
    ]
    _, rec = render(layout(), phones=phones)
    # priority 1 first (no number -> left out, numbering continues at T01), then priority 2 by row_id descending;
    # max 2 is applied before leaving out instances without a value
    assert "PTT01" + "0103333" in rec and "PTT02" not in rec
    drops = Drops()
    drops.segments[("party_phone", 3)].append(None)
    _, rec = render(layout(), phones=phones, drops=drops)  # REJECT_SEGMENT on row 3
    assert "PTT01" + "0103111" in rec


def test_field_drops_apply_to_present_check():
    drops = Drops()
    drops.fields[("party", 1, "full_name")].append("A1")
    _, rec = render(layout(), drops=drops)
    assert rec.startswith("PNN01" + "0204NONE")  # PN.01 dropped by validation


def test_header_and_tlv_style_and_parser():
    lay = layout()
    r, _rec = render(lay)
    assert r.header() == "TUDF30092026"
    tlv_style = lay.with_encoding(segment_tag_style="tlv")
    _, rec2 = render(tlv_style)
    assert rec2.startswith("PN03N01")
    (parsed,) = parse_records(rec2, tlv_style)
    assert [s.tag for s in parsed] == ["PN", "TL", "ES"] and parsed[1].fields["01"] == "900"


def test_split_words_reports_overflow():
    lay = layout(
        segments=[
            {
                "tag": "PN",
                "seq": "N01",
                "from": "party",
                "fields": [
                    {
                        "tags": ["01", "02"],
                        "source": "full_name",
                        "format": "split_words",
                        "args": {"width": 6},
                        "warn": "name of {party.party_key}",
                    }
                ],
            }
        ]
    )
    party = Row(row_id=1, party_key="P1", full_name="Abc Defgh Ijk Lmn", nickname=None)
    r, rec = render(lay, party=party)
    assert rec == "PNN01" + "0103Abc" + "0205Defgh" + "ES02**" and r.truncated == ["name of P1"]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"header": {"length": 13, "fields": [{"name": "x", "const": "A", "width": 12}]}}, "add up to 12"),
        (
            {"segments": [{"tag": "PN", "seq": "N01", "from": "party", "fields": [{"tag": 1, "source": "x"}]}]},
            "two digits",
        ),
        (
            {
                "segments": [
                    {
                        "tag": "PN",
                        "seq": "N01",
                        "from": "party",
                        "fields": [{"tag": "01", "source": "x", "format": "nope"}],
                    }
                ]
            },
            "unknown format",
        ),
        (
            {
                "segments": [
                    {"tag": "PN", "seq": "N01", "from": "party", "fields": [{"tag": "01", "source": "x", "colour": 1}]}
                ]
            },
            "unknown keys",
        ),
        ({"segments": [{"tag": "PN", "seq": "N01", "from": "nowhere", "fields": []}]}, "from must be"),
        ({"segments": [{"tag": "PT", "seq": "T{n:02d}", "from": "rows", "fields": []}]}, "rows: <entity>"),
        (
            {"segments": [{"tag": "PN", "seq": "N01", "from": "party", "fields": [{"tag": "01", "source": "loan.x"}]}]},
            "unknown source",
        ),
        (
            {
                "segments": [
                    {
                        "tag": "PN",
                        "seq": "N01",
                        "from": "party",
                        "fields": [{"tag": "01", "source": "x", "format": "lookup"}],
                    }
                ]
            },
            "args.map",
        ),
        (
            {
                "segments": [
                    {
                        "tag": "PN",
                        "seq": "N01",
                        "from": "party",
                        "fields": [{"tag": "01", "source": "x", "translate": "sex"}],
                    }
                ]
            },
            "code list",
        ),
    ],
)
def test_layout_mistakes_fail_at_load(change, message):
    with pytest.raises(LayoutError, match=message):
        layout(**change)


def test_consumer_layout_loads():
    lay = consumer_layout()
    assert lay.header_length == 146 and [s.tag for s in lay.segments] == ["PN", "ID", "PT", "EC", "PA", "TL", "TH"]
    for seg in lay.segments:  # tags ascending within a segment
        tags = [t for f in seg.fields for t in ((f.tag,) if f.tag else f.tags)]
        assert tags == sorted(tags), seg.tag
