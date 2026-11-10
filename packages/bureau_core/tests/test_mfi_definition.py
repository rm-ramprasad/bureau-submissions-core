"""MFI step 2 (ADR 0003): the canonical model and the MFI type files agree with the spec,
docs/specs/UCRF_MFI_v3.3.1.md (read here, so a drift between the spec and the files fails a test)."""

import json
import re
from pathlib import Path

import pytest

from bureau_core.canonical import canonical_model
from bureau_core.types import TypeDefinitionError, check_files

ROOT = Path(__file__).resolve().parents[3]
SPEC = (ROOT / "docs" / "specs" / "UCRF_MFI_v3.3.1.md").read_text(encoding="utf-8")
MFI = Path(__file__).resolve().parents[1] / "src" / "bureau_core" / "types" / "mfi"


@pytest.fixture(scope="module")
def mfi():
    name, translations, groups, required = check_files(MFI)
    assert name == "MFI"
    return translations, groups, required


def _codes(section: str) -> set[str]:
    """The codes of one enumeration in section 8 (tables or 'CODE Meaning · CODE Meaning' lines)."""
    body = SPEC.split(f"### {section}", 1)[1].split("\n### ", 1)[0]
    body = re.sub(r"\(No [A-Z][0-9]{2}\.\)", "", body)  # "(No F09.)": the spec's note that a code does not exist
    return set(re.findall(r"(?<![A-Z0-9])([A-Z][0-9]{2})(?![0-9])", body))


@pytest.mark.parametrize(
    ("section", "code_list"),
    [
        ("8.1 Repayment Frequency", "payment_frequency"),
        ("8.2 Loan Type", "loan_type"),
        ("8.4 Write-off Reason", "write_off_reason"),
        ("8.6 Telephone Type Indicator", "phone_type"),
        ("8.7 Relationship", "relation"),
        ("8.9 Linked to Govt Program", "govt_program"),
        ("8.10 Marital Status", "marital_status"),
        ("8.11 Educational Qualification", "education"),
        ("8.12 Occupation", "occupation"),
        ("8.13 Religion", "religion"),
        ("8.14 Caste", "social_category"),
        ("8.15 Member's Role", "member_role"),
        ("8.16 Member's Status", "member_status"),
        ("8.17 Loan Category", "loan_category"),
        ("8.19 Type of Insurance", "insurance_type"),
    ],
)
def test_every_spec_code_has_exactly_one_canonical_value(mfi, section, code_list):
    translations, _, _ = mfi
    codes = [t.output_code for t in translations[code_list].values()]
    assert set(codes) == _codes(section), code_list
    assert len(codes) == len(set(codes)), f"{code_list}: two canonical values share a code"


def test_account_status_s06_to_s18_are_translations_and_s04_s05_s07_derived(mfi):
    translations, _, _ = mfi
    statuses = {t.output_code for t in translations["credit_facility_status"].values()}
    spec = {f"S{n:02d}" for n in range(4, 19)}
    assert statuses | {"S04", "S05", "S07"} == spec and not statuses & {"S04", "S05", "S07"}


def test_gender_and_yes_no(mfi):
    translations, _, _ = mfi
    assert {v: t.output_code for v, t in translations["gender"].items()} == {
        "FEMALE": "F",
        "MALE": "M",
        "TRANSGENDER": "T",
    }
    assert {v: t.output_code for v, t in translations["yes_no"].items()} == {"YES": "Y", "NO": "N"}


def test_state_codes_follow_appendix_c_and_its_pin_ranges(mfi):
    """Owner, 2026-11-10: Appendix C. Each MFI state code's PIN range in the spec equals the canonical model's."""
    translations, _, _ = mfi
    ranges = json.loads(SPEC.split("```json", 1)[1].split("```", 1)[0])["pin_prefix_ranges"]
    model = canonical_model().code_lists["state_code"]
    by_value = {c.code: c for c in model.codes}
    states = translations["state_code"]
    assert {t.output_code for t in states.values()} == set(ranges)
    for value, t in states.items():
        c = by_value[value]
        assert [c.pin_min, c.pin_max] == ranges[t.output_code], f"{value} -> {t.output_code}"
    assert (states["CHANDIGARH"].output_code, states["CHHATTISGARH"].output_code, states["HIMACHAL_PRADESH"].output_code,
            states["HARYANA"].output_code, states["JAMMU_KASHMIR"].output_code, states["JHARKHAND"].output_code) == (
        "CH", "CG", "HP", "HR", "JK", "JH")  # fmt: skip


def test_file_routing_groups_cover_every_category_but_others(mfi):
    _, groups, _ = mfi
    shg, jlg = set(groups["shg_loan_categories"]), set(groups["jlg_loan_categories"])
    assert not shg & jlg
    assert shg | jlg | {"OTHERS"} == set(canonical_model().code_lists["loan_category"].values(active_only=False))


def test_required_columns_exist(mfi):
    _, _, required = mfi
    assert required["account"][:2] == ("account_key", "account_number") and "loan_category" in required["account"]


def test_mfi_entities_in_the_model():
    m = canonical_model()
    assert {c.name for c in m.entities["party_relative"].columns} >= {"party_key", "relative_role", "relation"}
    assert {c.name for c in m.entities["borrower_group"].columns} >= {
        "group_key",
        "group_account_number",
        "govt_program",
    }
    party = {c.name for c in m.entities["party"].columns}
    account = {c.name for c in m.entities["account"].columns}
    assert {"centre_id", "group_key", "family_income", "member_status"} <= party
    assert {"loan_category", "loan_cycle", "loan_type", "write_off_date", "unique_reference"} <= account


def test_a_bad_translation_is_refused(tmp_path):
    (tmp_path / "codes.yaml").write_text('submission_type: MFI\ntranslations:\n  gender:\n    ROBOT: "R"\n')
    (tmp_path / "requirements.yaml").write_text("submission_type: MFI\n")
    with pytest.raises(TypeDefinitionError, match=r"gender\.ROBOT is not a canonical value"):
        check_files(tmp_path)
