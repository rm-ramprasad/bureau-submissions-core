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


def test_the_finding_index_gives_the_same_reasons_in_the_same_order() -> None:
    """The indexed lookup (1 lakh records) matches scanning every finding with `applies`, order included."""
    from types import SimpleNamespace

    from hypothesis import given, settings
    from hypothesis import strategies as st

    from bureau_core.validation.cascades import FindingIndex, applies

    keys = st.one_of(st.none(), st.sampled_from(["A1", "A2", "A3"]))
    parties = st.one_of(st.none(), st.sampled_from(["P1", "P2"]))
    finding = st.builds(
        SimpleNamespace, rule_id=st.sampled_from(["R1", "R2", "R3", "R4"]), account_key=keys, party_key=parties
    )

    @settings(max_examples=300, deadline=None)
    @given(st.lists(finding, max_size=12))
    def same(findings):
        index = FindingIndex(findings)
        for a in ("A1", "A2", "A3"):
            for p in ("P1", "P2"):
                assert index.reasons(a, p) == [f.rule_id for f in findings if applies(f, a, p)]

    same()
