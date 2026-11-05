"""Golden regression: captured canonical batches (synthetic mock data, tests/golden/batches/) render, with no database,
byte for byte the files the reference app produced: the direct consumer TUDF, the live consumer path (bureau Excel ->
converter -> TUDF) and the commercial pipe file, for every bureau.

An intended output change: run with REGENERATE_GOLDEN=1, review the diff, and state it in the pull request.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bureau_core.bureau import BureauProfile
from bureau_core.canonical import canonical_model
from bureau_core.contract import CanonicalBatch, ValidationResult, check_batch, json_schema, snapshot_data
from bureau_core.enums import BureauCode, ReportingCycle, SubmissionType
from bureau_core.formats.delimited import engine as delimited
from bureau_core.formats.delimited.layout import load_layout as load_delimited_layout
from bureau_core.formats.excel import excel_layout
from bureau_core.formats.excel.engine import render_workbook
from bureau_core.formats.tudf import engine as tudf
from bureau_core.formats.tudf.layout import load_layout
from bureau_core.tudf import convert
from bureau_core.types import type_definition

GOLDEN = Path(__file__).parent / "golden"
REGENERATE = os.environ.get("REGENERATE_GOLDEN") == "1"
AS_OF = date(2026, 9, 30)
CREATED = date(2026, 10, 5)  # commercial: file creation date (clamped to >= as-of)

# The reference app's seeded placeholder profiles for tenant nbfc_001 (submissions/bureaus/seed.py; test values)
_PLACEHOLDERS = {
    BureauCode.EXPERIAN: ("Experian", "EXP", "CONS"),
    BureauCode.CIBIL: ("TransUnion CIBIL", "CIB", "TUDF"),
    BureauCode.CRIF_HIGHMARK: ("CRIF High Mark", "CRF", "CONS"),
    BureauCode.EQUIFAX: ("Equifax", "EQF", "CONS"),
}


def consumer_profile(code: BureauCode) -> BureauProfile:
    name, prefix, tag = _PLACEHOLDERS[code]
    return BureauProfile(
        "nbfc_001", code, SubmissionType.CONSUMER, name, f"{prefix}0000001", "NBFC 001", tag, "{member_id}.txt"
    )


def commercial_profile(code: BureauCode) -> BureauProfile:
    return BureauProfile(
        "nbfc_001",
        code,
        SubmissionType.COMMERCIAL,
        f"{code.value} commercial",
        "CM0010001",
        None,
        "",
        "Commercial-ME-{as_of:%d%m%Y}-{member_id}.txt",
    )


def check(name: str, content: bytes) -> None:
    path = GOLDEN / name
    if REGENERATE:
        path.write_bytes(content)
    assert path.exists(), f"missing golden file {name}: run with REGENERATE_GOLDEN=1"
    assert content == path.read_bytes(), f"{name} differs from the golden file"


def load(name: str) -> tuple[CanonicalBatch, ValidationResult]:
    batch_text = (GOLDEN / "batches" / f"{name}.batch.json").read_text("utf-8")
    result_text = (GOLDEN / "batches" / f"{name}.result.json").read_text("utf-8")
    batch, result = CanonicalBatch.from_json(batch_text), ValidationResult.from_json(result_text)
    assert batch.to_json() == batch_text and result.to_json() == result_text  # lossless round trip
    return batch, result


def _children(layout) -> list[tuple[str, str]]:
    return [(s.rows, s.by) for s in layout.segments if s.rows]


@pytest.mark.parametrize("code", list(BureauCode))
def test_consumer_direct_tudf(code: BureauCode) -> None:
    batch, result = load("consumer_nbfc_001")
    layout = load_layout(type_definition(SubmissionType.CONSUMER).path("layout.yaml"))
    got, _ = tudf.render_file(
        snapshot_data(batch, result, _children(layout)),
        AS_OF,
        consumer_profile(code),
        ReportingCycle.MONTH_END,
        layout,
        result.translations,
    )
    check(f"nbfc_001_{code.value.lower()}.txt", got)


@pytest.mark.parametrize("code", list(BureauCode))
def test_consumer_via_bureau_excel(code: BureauCode) -> None:
    batch, result = load("consumer_nbfc_001")
    profile = consumer_profile(code)
    xl = excel_layout("CONSUMER")
    excel = render_workbook(
        snapshot_data(batch, result, list(xl.child_entities.items())), result.translations, profile, xl
    )
    converted = convert(
        excel.content,
        submission_type="CONSUMER",
        bureau=str(profile.bureau_code),
        member_id=profile.member_id,
        member_short_name=profile.member_short_name,
        header_tag=profile.header_segment_tag,
        as_of_date=AS_OF,
        cycle="ME",
    ).content
    check(f"consumer_nbfc_001_{code.value.lower()}_via_excel.txt", converted)


@pytest.mark.parametrize("code", list(BureauCode))
def test_commercial_delimited(code: BureauCode) -> None:
    batch, result = load("commercial_nbfc_001")
    layout = load_delimited_layout(type_definition(SubmissionType.COMMERCIAL).path("layout.yaml"))
    got, _ = delimited.render_file(
        snapshot_data(batch, result, _children(layout)),
        AS_OF,
        commercial_profile(code),
        ReportingCycle.MONTH_END,
        layout,
        result.translations,
        max(CREATED, AS_OF),
    )
    check(f"commercial_nbfc_001_{code.value.lower()}.txt", got)


# ---------------------------------------------------------------- the contract itself
def _batch(**account: object) -> CanonicalBatch:
    return CanonicalBatch(
        "CONSUMER", AS_OF, canonical_model().version, {"account": [{"row_id": 1, "account_key": "A1", **account}]}
    )


def test_contract_reports_each_kind_of_problem() -> None:
    problems = check_batch(
        _batch(account_type="Personal Loan", date_opened="31-12-2025", current_balance=1.5, nonsense="x")
    )
    assert {(p.column, p.kind) for p in problems} == {
        ("account_type", "CODE"),
        ("date_opened", "TYPE"),
        ("current_balance", "TYPE"),
        ("nonsense", "COLUMN"),
    }
    assert check_batch(_batch()) == []
    wrong = CanonicalBatch("CONSUMER", AS_OF, "0.0", {"x": [{"row_id": 1}]})
    assert {p.kind for p in check_batch(wrong)} == {"VERSION", "ENTITY"}


def test_contract_is_deterministic_and_lossless() -> None:
    text = _batch(current_balance="100.10", date_opened="2025-12-31").to_json()
    again = CanonicalBatch.from_json(text)
    assert again.to_json() == text
    assert again.entities["account"][0]["current_balance"] == Decimal("100.10")  # exact, not a float


def test_contract_rejects_other_formats() -> None:
    with pytest.raises(ValueError):
        CanonicalBatch.from_json('{"format": "something-else"}')
    with pytest.raises(ValueError):
        ValidationResult.from_json(_batch().to_json())


def test_json_schema_covers_the_model() -> None:
    entities = json_schema()["properties"]["entities"]["properties"]
    assert set(entities) == set(canonical_model().entities)
    assert entities["account"]["items"]["properties"]["account_type"]["anyOf"][0]["enum"]


@pytest.mark.parametrize("name", ["consumer_nbfc_001", "commercial_nbfc_001"])
def test_mock_captures_meet_the_contract(name: str) -> None:
    """The mock sources break validation rules on purpose (code / length problems); nothing structural."""
    batch, _ = load(name)
    assert [p for p in check_batch(batch) if p.kind not in ("CODE", "LENGTH")] == []


# ---------------------------------------------------------------- validation in the core
@pytest.mark.parametrize("name", ["consumer_nbfc_001", "commercial_nbfc_001"])
def test_core_validation_reproduces_the_captured_result(name: str) -> None:
    """The core's validation of a captured batch gives the result the reference app recorded, compared like for like:
    - finding values from decimal columns with their scale (6401.00, as Azure SQL writes them; the capture came from a
      SQLite test database, which writes 6401);
    - the consumer capture's account history was added after it was validated (TH coverage for the files), so it is
      left out of the batch validated here."""
    from bureau_core.validation.rules import builtin_rules
    from bureau_core.validation.validate import finding_value, validate, value_scale

    batch, expected = load(name)
    scales = {r.rule_id: value_scale(r) for r in builtin_rules(batch.submission_type)}
    for f in expected.findings:
        f["value"] = finding_value(scales.get(f["rule_id"]), f["value"])
    entities = {k: v for k, v in batch.entities.items() if k != "account_history"}
    as_validated = CanonicalBatch(
        batch.submission_type,
        batch.as_of_date,
        batch.canonical_version,
        entities,
        source=batch.source,
        exceptions=batch.exceptions,
    )
    assert validate(as_validated).to_json() == expected.to_json()
