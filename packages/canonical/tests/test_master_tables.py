"""L2a demographics master tables (migration 010; the SQL Server drift check is in test_db_views.py)."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from canonical.db import make_engine, metadata
from canonical.master.tables import (
    MASTER_TABLES,
    dm_party,
    dm_party_phone,
    dm_sanction,
    dm_sanction_party,
)
from canonical.migrate import AZURE_MIGRATIONS

NOW = datetime(2026, 11, 8)


@pytest.fixture
def eng(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    metadata.create_all(engine, tables=list(MASTER_TABLES))
    return engine


def test_migration_creates_every_master_table() -> None:
    sql = "".join(
        (AZURE_MIGRATIONS / f).read_text()
        for f in (
            "010_demographics_master.sql",
            "013_master_fields.sql",
            "014_master_lengths.sql",
            "015_sanction_payment_fallback.sql",
        )
    )
    for table in MASTER_TABLES:
        assert f"CREATE TABLE automn.{table.name} (" in sql
        for col in table.columns:
            assert f"\t{col.name} " in sql, f"{table.name}.{col.name}"


def test_master_columns_are_as_wide_as_the_snapshot_columns_they_feed() -> None:
    """A value today's snapshot accepts must fit the master too (rarfincare: two phone numbers in one field)."""
    from canonical.schema import ENTITY_TABLES

    snapshot: dict[str, int] = {}
    for table in ENTITY_TABLES.values():
        for col in table.columns:
            if getattr(col.type, "length", None):
                snapshot[col.name] = max(snapshot.get(col.name, 0), col.type.length)
    # the guarantor / relationship shapes' flattened fields land in these master columns
    snapshot["phone_number"] = max(snapshot["phone_number"], snapshot["mobile"], snapshot["telephone"], snapshot["fax"])
    snapshot["area_code"] = max(snapshot["telephone_area_code"], snapshot["fax_area_code"])
    snapshot["id_number"] = max(
        snapshot["id_number"], *(snapshot[c] for c in ("pan", "din", "cin", "company_registration"))
    )
    snapshot["customer_id"] = max(snapshot["party_key"], snapshot["related_key"])
    narrow = [
        f"{t.name}.{c.name}: {c.type.length} < {snapshot[c.name]}"
        for t in MASTER_TABLES
        for c in t.columns
        if getattr(c.type, "length", None) and c.name in snapshot and c.type.length < snapshot[c.name]
    ]
    assert not narrow, narrow


def test_party_sanction_role_round_trip(eng) -> None:
    load = dict(first_load_id=1, last_load_id=1, updated_at=NOW)
    with eng.begin() as conn:
        pid = conn.execute(
            dm_party.insert().values(tenant_id="t", customer_id="C1", party_kind="INDIVIDUAL", **load)
        ).inserted_primary_key[0]
        sid = conn.execute(
            dm_sanction.insert().values(
                tenant_id="t", account_number="A1", sanctioned_amount=Decimal("1000.50"), **load
            )
        ).inserted_primary_key[0]
        conn.execute(dm_sanction_party.insert().values(sanction_id=sid, party_id=pid, role="BORROWER", load_id=1))
        conn.execute(dm_party_phone.insert().values(party_id=pid, seq=1, phone_number="9999999999", load_id=1))
        s = conn.execute(select(dm_sanction)).one()
        p = conn.execute(select(dm_party)).one()
    assert (s.sanction_number, s.is_active, s.sanctioned_amount) == ("", True, Decimal("1000.50"))
    assert p.is_active and p.birth_date is None
    assert "dm_change_log" in inspect(eng).get_table_names(schema="automn")


def test_customer_id_is_unique_per_tenant(eng) -> None:
    row = dict(tenant_id="t", customer_id="C1", party_kind="ENTITY", first_load_id=1, last_load_id=1, updated_at=NOW)
    with eng.begin() as conn:
        conn.execute(dm_party.insert().values(**row))
        conn.execute(dm_party.insert().values(**{**row, "tenant_id": "u"}))
    with pytest.raises(IntegrityError), eng.begin() as conn:
        conn.execute(dm_party.insert().values(**row))
