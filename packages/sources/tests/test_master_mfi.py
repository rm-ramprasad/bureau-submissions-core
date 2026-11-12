"""MFI and the customer tables (owner 2026-11-11): the sanction-keyed customer tables are for consumer and commercial;
MFI runs from its source views until it is brought onto them (relatives, groups, members: later)."""

import pytest

from sources.master import SELECTION, MasterError, compile_master


def test_mfi_is_not_on_the_customer_tables_yet(engine) -> None:
    assert "MFI" not in SELECTION
    with pytest.raises(MasterError, match="No selection defined for MFI"):
        compile_master(engine, "MFI")


@pytest.mark.parametrize("kind", ["CONSUMER", "COMMERCIAL"])
def test_consumer_and_commercial_compile(engine, kind) -> None:
    compiled = compile_master(engine, kind)
    assert {"party", "account", "account_holder"} <= set(compiled.sql) and compiled.version.startswith("master-")
    assert ("relationship" in compiled.sql) == (kind == "COMMERCIAL")
