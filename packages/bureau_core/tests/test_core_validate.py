"""bureau_core.validation.validate: decimal formatting, value scale, contract refusal."""

from datetime import date

import pytest

from bureau_core.contract import CanonicalBatch
from bureau_core.validation.rules import builtin_rules
from bureau_core.validation.validate import BatchContractError, finding_value, validate, value_scale

AS_OF = date(2026, 9, 30)


def _rule(rule_id):
    return next(r for t in ("CONSUMER", "COMMERCIAL") for r in builtin_rules(t) if r.rule_id == rule_id)


@pytest.mark.parametrize(
    "scale, value, expected",
    [
        (2, "6401", "6401.00"),  # SQLite's text for an amount -> Azure SQL's
        (2, "0", "0.00"),
        (2, "6401.00", "6401.00"),  # already formatted: unchanged
        (2, "-12.5", "-12.50"),
        (2, "n/a", "n/a"),  # not a number
        (None, "45", "45"),  # not a decimal column (e.g. days_past_due, an integer)
        (2, None, None),
    ],
)
def test_finding_values_are_written_as_on_azure_sql(scale, value, expected):
    assert finding_value(scale, value) == expected


@pytest.mark.parametrize(
    "rule_id, scale",
    [
        ("UCRF.TL.EMI_NOT_ALLOWED", 2),  # a.emi_amount: decimal
        ("UCRF.TL.COLLATERAL_VALUE_NOT_POSITIVE", 2),  # a.collateral_value
        ("UCRF.TL.DPD_WITHOUT_OVERDUE", None),  # filed under amount_overdue, reports days_past_due (integer)
    ],
)
def test_value_scale_comes_from_the_reported_column(rule_id, scale):
    assert value_scale(_rule(rule_id)) == scale


def test_validate_refuses_a_broken_batch():
    batch = CanonicalBatch("CONSUMER", AS_OF, "0.0", {"account": [{"row_id": 1, "nonsense": "x"}]})
    with pytest.raises(BatchContractError):
        validate(batch)
