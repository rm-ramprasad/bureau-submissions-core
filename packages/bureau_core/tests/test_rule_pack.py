"""Rule packs and their parameters (bureau_core.validation)."""

from collections import Counter
from datetime import date

import pytest

from bureau_core.checks.identifiers import id_number_problem
from bureau_core.validation import parameters as P
from bureau_core.validation.python_rules import PYTHON_RULES
from bureau_core.validation.rules import RuleDefinitionError, builtin_pack, builtin_rules


def test_pack_declares_the_parameters_with_typed_defaults():
    params = P.builtin_parameters("CONSUMER")
    assert params["dob_gender_required_from"].default == date(2005, 5, 1)
    assert params["phone_or_id_required_from"].default == date(2007, 6, 1)
    assert params["min_address_length"].default == 3
    assert params["id_pattern_pan"].default.startswith("^[A-Z]{3}")
    assert set(params["state_pin_required_from"].used_by) == {"UCRF.PA.STATE_REQUIRED", "UCRF.PA.PIN_REQUIRED"}


@pytest.mark.parametrize(
    "name, raw, message",
    [
        ("dob_gender_required_from", "01-05-2005", "YYYY-MM-DD"),
        ("min_address_length", "two", "whole number"),
        ("min_address_length", "0", "between 1 and 40"),
        ("id_pattern_pan", "^[A-Z", "not a valid regular expression"),
        ("id_pattern_pan", "", "enter a value"),
    ],
)
def test_invalid_values_are_rejected(name, raw, message):
    with pytest.raises(P.ParameterError, match=message):
        P.builtin_parameters("CONSUMER")[name].parse(raw)


def test_messages_show_the_values():
    assert (
        P.render_message(
            "on or after {dob_gender_required_from:%d-%m-%Y}", {"dob_gender_required_from": date(2010, 1, 2)}
        )
        == "on or after 02-01-2010"
    )
    assert P.render_message("no placeholders", {}) == "no placeholders"
    assert P.render_message("{unknown}", {}) == "{unknown}"


def test_id_patterns_can_be_overridden():
    assert id_number_problem("PAN", "ABCDE1234F") is not None  # 4th letter must be P or H
    assert id_number_problem("PAN", "ABCPE1234F") is None
    loose = {"id_pattern_pan": r"^[A-Z]{5}[0-9]{4}[A-Z]$"}
    assert id_number_problem("PAN", "ABCDE1234F", loose) is None
    assert "does not match the pattern" in id_number_problem("PAN", "1234", loose)
    assert id_number_problem("AADHAAR", "123456789012", loose) is not None  # Aadhaar checksum is not a parameter


def test_pack_loads_with_generated_rules():
    rules = builtin_rules("CONSUMER")
    kinds = Counter(r.kind for r in rules)
    assert kinds["python"] == 10 and kinds["cascade"] == 3 and kinds["sql"] > 80
    ids = [r.rule_id for r in rules]
    assert len(ids) == len(set(ids)) and all(i.startswith("UCRF.") for i in ids)
    assert "UCRF.REQUIRED.account.account_number" in ids and "UCRF.ENUM.party_address.state_code" in ids
    assert all(":snapshot_id" in r.sql for r in rules if r.sql)


def test_no_builtin_pack_for_other_types():
    with pytest.raises(RuleDefinitionError):
        builtin_rules("NESL")


def test_pack_loads_with_its_own_prefix_and_cascade():
    pack = builtin_pack("COMMERCIAL")
    assert pack.prefix == "CUCRF." and pack.cascade.name == "facility_borrower_minimums"
    assert all(r.rule_id.startswith("CUCRF.") for r in pack.rules)
    assert all(r.python in PYTHON_RULES for r in pack.rules if r.python)
    ids = {r.rule_id for r in pack.rules}
    assert {
        "CUCRF.REQUIRED.party.legal_constitution",
        "CUCRF.ENUM.account.account_type",
        "CUCRF.REC.NO_RELATIONSHIP",
    } <= ids
    assert not ids & {r.rule_id for r in builtin_pack("CONSUMER").rules}
