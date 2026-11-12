"""L2a customer tables, sanction model (migration 019; the SQL Server drift check is in test_db_views.py)."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from canonical.db import make_engine, metadata
from canonical.master.tables import (
    MASTER_TABLES,
    dm_sanction,
    dm_sanction_account,
    dm_sanction_phone,
)
from canonical.migrate import AZURE_MIGRATIONS

NOW = datetime(2026, 11, 11)
LIKE_SNAPSHOT = {"dm_sanction_guarantor", "dm_sanction_relationship", "dm_sanction_security", "dm_sanction_cheque"}
LOAD = dict(first_load_id=1, last_load_id=1, updated_at=NOW)


@pytest.fixture
def eng(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    metadata.create_all(engine, tables=list(MASTER_TABLES))
    return engine


def test_migration_019_creates_every_master_table() -> None:
    sql = (AZURE_MIGRATIONS / "019_sanction_master.sql").read_text() + (
        AZURE_MIGRATIONS / "010_demographics_master.sql"
    ).read_text()  # dm_change_log is from 010
    for table in MASTER_TABLES:
        assert f"CREATE TABLE automn.{table.name} (" in sql
        for col in table.columns:
            assert f"\t{col.name} " in sql, f"{table.name}.{col.name}"


def test_master_columns_are_as_wide_as_the_snapshot_columns_they_feed() -> None:
    """A value today's snapshot accepts must fit the customer tables too."""
    from canonical.schema import ENTITY_TABLES

    snapshot: dict[str, int] = {}
    for table in ENTITY_TABLES.values():
        for col in table.columns:
            if getattr(col.type, "length", None):
                snapshot[col.name] = max(snapshot.get(col.name, 0), col.type.length)
    snapshot["sanction_reference_number"] = snapshot["party_key"]
    narrow = [
        f"{t.name}.{c.name}: {c.type.length} < {snapshot[c.name]}"
        for t in MASTER_TABLES
        if t.name not in LIKE_SNAPSHOT
        for c in t.columns
        if getattr(c.type, "length", None) and c.name in snapshot and c.type.length < snapshot[c.name]
    ]
    assert not narrow, narrow


def test_sanction_account_round_trip(eng) -> None:
    with eng.begin() as conn:
        sid = conn.execute(
            dm_sanction.insert().values(tenant_id="t", sanction_reference_number="SR/1", party_kind="ENTITY", **LOAD)
        ).inserted_primary_key[0]
        conn.execute(dm_sanction_account.insert().values(tenant_id="t", account_number="A1", sanction_id=sid,
                                                          sanctioned_amount=Decimal("1000.50"), **LOAD))  # fmt: skip
        conn.execute(dm_sanction_phone.insert().values(sanction_id=sid, holder_seq=0, seq=1, phone_number="9999999999",
                                                        load_id=1))  # fmt: skip
        a = conn.execute(select(dm_sanction_account)).one()
        s = conn.execute(select(dm_sanction)).one()
    assert (a.is_active, a.sanctioned_amount, a.sanction_id) == (True, Decimal("1000.50"), s.sanction_id)
    assert s.is_active and s.full_name is None
    assert "dm_change_log" in inspect(eng).get_table_names(schema="automn")


def test_sanction_reference_and_account_are_unique_per_tenant(eng) -> None:
    row = dict(tenant_id="t", sanction_reference_number="SR/1", party_kind="ENTITY", **LOAD)
    with eng.begin() as conn:
        conn.execute(dm_sanction.insert().values(**row))
        conn.execute(dm_sanction.insert().values(**{**row, "tenant_id": "u"}))
    with pytest.raises(IntegrityError), eng.begin() as conn:
        conn.execute(dm_sanction.insert().values(**row))
