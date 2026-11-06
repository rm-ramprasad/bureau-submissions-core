"""Rule parameters: the core's typed defaults and the client's overrides in the database (app.validation.settings)."""

from datetime import date

import pytest

from app.validation import settings as S
from bureau_core.checks.identifiers import id_number_problem
from bureau_core.validation.parameters import ParameterError, builtin_parameters, render_message


def test_pack_declares_the_parameters_with_typed_defaults():
    params = builtin_parameters("CONSUMER")
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
    with pytest.raises(ParameterError, match=message):
        builtin_parameters("CONSUMER")[name].parse(raw)


def test_override_is_per_nbfc_and_default_removes_it(engine):
    S.set_parameter(engine, "nbfc_001", "CONSUMER", "min_address_length", "10", "u")
    assert S.load_parameters(engine, "nbfc_001", "CONSUMER")["min_address_length"] == 10
    assert S.load_parameters(engine, "nbfc_002", "CONSUMER")["min_address_length"] == 3
    S.set_parameter(engine, "nbfc_001", "CONSUMER", "min_address_length", "3", "u")  # the default again
    assert S.overrides(engine, "nbfc_001", "CONSUMER") == {}


def test_messages_show_the_values():
    assert (
        render_message(
            "on or after {dob_gender_required_from:%d-%m-%Y}", {"dob_gender_required_from": date(2010, 1, 2)}
        )
        == "on or after 02-01-2010"
    )
    assert render_message("no placeholders", {}) == "no placeholders"
    assert render_message("{unknown}", {}) == "{unknown}"


def test_id_patterns_can_be_overridden():
    assert id_number_problem("PAN", "ABCDE1234F") is not None  # 4th letter must be P or H
    assert id_number_problem("PAN", "ABCPE1234F") is None
    loose = {"id_pattern_pan": r"^[A-Z]{5}[0-9]{4}[A-Z]$"}
    assert id_number_problem("PAN", "ABCDE1234F", loose) is None
    assert "does not match the pattern" in id_number_problem("PAN", "1234", loose)
    assert id_number_problem("AADHAAR", "123456789012", loose) is not None  # Aadhaar checksum is not a parameter


# ---------------------------------------------------------------- effect on a real validation


def test_unknown_parameters_and_clearing(engine, clock):
    with pytest.raises(ParameterError, match="Unknown parameter"):
        S.set_parameter(engine, "nbfc_001", "CONSUMER", "no_such", "1", "u")
    assert S.set_parameter(engine, "nbfc_001", "CONSUMER", "min_address_length", "7", "u", clock=clock) == 7
    assert S.set_parameter(engine, "nbfc_001", "CONSUMER", "min_address_length", "8", "u2", clock=clock) == 8
    assert S.overrides(engine, "nbfc_001", "CONSUMER")["min_address_length"]["updated_by"] == "u2"
    assert S.clear_parameter(engine, "nbfc_001", "CONSUMER", "min_address_length")
    assert not S.clear_parameter(engine, "nbfc_001", "CONSUMER", "min_address_length")


def test_policies_and_catalogue(engine):
    from bureau_core.validation.rules import RuleDefinitionError, builtin_rules

    first = builtin_rules("CONSUMER")[0]
    S.set_policy(engine, "nbfc_001", first.rule_id, outcome="WARN")
    S.set_policy(engine, "nbfc_001", first.rule_id, is_enabled=False)  # updates the same override
    entry = next(e for e in S.catalogue(engine, "nbfc_001", "CONSUMER") if e.default.rule_id == first.rule_id)
    assert entry.changed and entry.effective.enabled is False and entry.policy["outcome"] is None
    assert set(S.policies(engine, "nbfc_001")) == {first.rule_id}
    assert S.clear_policy(engine, "nbfc_001", first.rule_id) and not S.clear_policy(engine, "nbfc_001", first.rule_id)
    assert not any(e.changed for e in S.catalogue(engine, "nbfc_001", "CONSUMER"))

    from dataclasses import replace

    custom = (
        replace(first, rule_id="CUSTOM.NBFC_001.MINE", source="CUSTOM", sql="SELECT 1 AS row_id", generated=False)
        if hasattr(first, "generated")
        else replace(first, rule_id="CUSTOM.NBFC_001.MINE", source="CUSTOM")
    )
    with pytest.raises(RuleDefinitionError, match="must not start with"):
        S.save_custom_rule(engine, "nbfc_001", "CONSUMER", replace(first, sql="SELECT 1"))
    with pytest.raises(RuleDefinitionError, match="are SQL rules"):
        S.save_custom_rule(engine, "nbfc_001", "CONSUMER", replace(custom, sql=None))
