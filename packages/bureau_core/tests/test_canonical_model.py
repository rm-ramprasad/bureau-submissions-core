import pytest
import yaml

from bureau_core.canonical import MODEL_PATH, ModelDefinitionError, canonical_model, parse_model
from bureau_core.types import type_definition

EXPECTED_ENTITIES = {
    "party",
    "party_identifier",
    "party_phone",
    "party_email",
    "party_address",
    "account",
    "account_holder",
    "account_history",
    "extension_attribute",
    "relationship",
    "guarantor",
    "security",
    "cheque_dishonour",
    "party_relative",
    "borrower_group",
}  # 1.1: commercial; 1.2: MFI


# ---------------------------------------------------------------- definition
def test_packaged_model_loads():
    model = canonical_model()
    assert model.version == "1.2"  # 1.2: MFI (ADR 0003)
    assert set(model.entities) == EXPECTED_ENTITIES
    assert model.entities["account_holder"].column("ownership_indicator").code_list == "ownership_indicator"


def _file_codes(tdef, list_name: str) -> set[str]:
    return {t.output_code for t in tdef.translations[list_name].values()}


def test_code_lists_match_spec_appendices():
    """Canonical lists are the union of every type's catalogues; each type's file codes match its own spec."""
    consumer = type_definition("CONSUMER")
    assert len(_file_codes(consumer, "account_type")) == 61  # 00-21, 23-47, 50-59, 61, 69-71
    assert _file_codes(consumer, "id_type") == {f"{n:02d}" for n in range(1, 11)}
    assert len(_file_codes(consumer, "credit_facility_status")) == 19
    assert consumer.groups["card_account_type"] == (
        "CREDIT_CARD",
        "FLEET_CARD",
        "SECURED_CREDIT_CARD",
        "CORPORATE_CREDIT_CARD",
    )
    future = {v for v, t in consumer.translations["account_type"].items() if not t.active}  # "future use" in the spec
    assert future == {"TELCO_WIRELESS", "TELCO_BROADBAND", "TELCO_LANDLINE"}
    assert "26" not in _file_codes(consumer, "state_code")

    commercial = type_definition("COMMERCIAL")  # Commercial UCRF 3.10, section 8
    assert len(_file_codes(commercial, "account_type")) == 66  # 8.8 credit types
    assert _file_codes(commercial, "credit_facility_status") == {
        f"{n:02d}" for n in range(3, 25)
    }  # 8.10 (01/02: dates)
    assert _file_codes(commercial, "state_code") == {f"{n:02d}" for n in range(1, 38) if n != 9} | {"77", "99"}
    assert len(_file_codes(commercial, "country")) == 195 and "079" in _file_codes(commercial, "country")
    assert commercial.translations["currency"]["INR"].output_code == "INR"
    assert commercial.groups["revolving_credit_type"] == (
        "CASH_CREDIT",
        "OVERDRAFT",
        "CORPORATE_CREDIT_CARD",
        "TEMPORARY_OVERDRAFT",
    )


def test_every_code_column_references_a_code_list():
    model = canonical_model()
    for entity in model.entities.values():
        for col in entity.columns:
            assert (col.type == "code") == (col.code_list is not None), f"{entity.name}.{col.name}"


def _raw():
    return yaml.safe_load(MODEL_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda m: m.pop("canonical_version"), "canonical_version"),
        (lambda m: m["entities"]["party"]["columns"]["gender"].update(code_list="sex"), "unknown code_list"),
        (lambda m: m["entities"]["party"]["columns"]["full_name"].update(type="text"), "type must be"),
        (lambda m: m["entities"]["party"]["columns"]["full_name"].pop("length"), "need a length"),
        (lambda m: m["entities"]["party"]["columns"].update(row_id={"type": "integer"}), "reserved"),
        (lambda m: m["entities"]["party"]["columns"]["full_name"].update(colour="red"), "unknown keys"),
        (lambda m: m["entities"]["party"].update(indexes=[["nope"]]), "unknown columns"),
        (lambda m: m["entities"]["party"]["columns"]["full_name"].update(description="a; b"), "semicolon"),
        (lambda m: m["code_lists"]["gender"]["codes"].update({4: "Other"}), "quoted"),
        (lambda m: m["code_lists"]["gender"]["codes"].update({"X" * 41: "Too long"}), "longer than 40"),
        (lambda m: m["entities"]["party_email"].update(table="c_party"), "duplicate table"),
    ],
)
def test_invalid_definitions_rejected(mutate, message):
    raw = _raw()
    mutate(raw)
    with pytest.raises(ModelDefinitionError, match=message):
        parse_model(raw)


# ---------------------------------------------------------------- tables
