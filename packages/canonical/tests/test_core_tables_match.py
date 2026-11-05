"""Drift guard: the core validates in its own private copy of the tables the rule SQL uses (bureau_core.validation.
tables). Every column there must exist in the app database's table of the same name, with a compatible type, so a
rule's SQL means the same on both."""

import pytest

import canonical.staging.tables  # noqa: F401  (registers all app tables)
from bureau_core.validation import tables as core
from canonical.db import metadata as app_metadata

APP_ONLY = {
    "tenant",
    "extract_snapshot",
    "submission_adjustment",
    "snapshot_adjustment",
    "snapshot_adjustment_effect",
    "validation_finding",
    "validation_summary",
    "record_outcome",
}  # defined by the app in step 1.3


@pytest.mark.parametrize("name", sorted(t.name for t in core.metadata.sorted_tables))
def test_core_table_columns_exist_in_the_app_table(name: str) -> None:
    app = app_metadata.tables.get(f"automn.{name}")
    if app is None:
        assert name in APP_ONLY, f"automn.{name} is in the core but not in the app metadata"
        pytest.skip(f"automn.{name}: app definition moves in step 1.3")
    for col in core.metadata.tables[f"automn.{name}"].columns:
        assert col.name in app.c, f"{name}.{col.name} missing in the app table"
        assert type(col.type)._type_affinity is type(app.c[col.name].type)._type_affinity, f"{name}.{col.name}"
        if hasattr(col.type, "length"):
            assert col.type.length == app.c[col.name].type.length, f"{name}.{col.name} length"
