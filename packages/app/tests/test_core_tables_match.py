"""Drift guard: the core validates in its own private copy of the tables the rule SQL uses (bureau_core.validation.
tables). Every column there must exist in the app database's table of the same name, with a compatible type, so a
rule's SQL means the same on both."""

import pytest

import app.adjustments.tables  # registers the app tables (with the two below)
import app.validation.tables  # noqa: F401
import canonical.staging.tables  # noqa: F401
from bureau_core.validation import tables as core
from canonical.db import metadata as app_metadata


@pytest.mark.parametrize("name", sorted(t.name for t in core.metadata.sorted_tables))
def test_core_table_columns_exist_in_the_app_table(name: str) -> None:
    app_table = app_metadata.tables.get(f"automn.{name}")
    assert app_table is not None, f"automn.{name} is in the core but not in the app metadata"
    for col in core.metadata.tables[f"automn.{name}"].columns:
        assert col.name in app_table.c, f"{name}.{col.name} missing in the app table"
        assert type(col.type)._type_affinity is type(app_table.c[col.name].type)._type_affinity, f"{name}.{col.name}"
        if hasattr(col.type, "length"):
            assert col.type.length == app_table.c[col.name].type.length, f"{name}.{col.name} length"
