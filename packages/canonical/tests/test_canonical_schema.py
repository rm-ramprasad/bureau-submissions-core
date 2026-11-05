from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, inspect, select

from bureau_core.canonical import canonical_model
from canonical.schema import (
    ENTITY_TABLES,
    RECORD_TYPES,
    extract_snapshot,
    record_from_row,
    ref_code,
    ref_code_rows,
    ref_state_pin_rows,
)

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
}  # 1.1: commercial


def test_entity_tables_have_row_id_snapshot_and_indexes():
    for name, table in ENTITY_TABLES.items():
        cols = table.c.keys()
        assert cols[:2] == ["row_id", "snapshot_id"], name
        assert all(c.nullable for c in table.c if c.name not in ("row_id", "snapshot_id")), name
        assert all(next(iter(ix.columns)).name == "snapshot_id" for ix in table.indexes), name


def test_reference_rows():
    rows = ref_code_rows()
    assert len(rows) == sum(len(cl.codes) for cl in canonical_model().code_lists.values())
    pins = {r["state_code"]: (r["pin_prefix_min"], r["pin_prefix_max"]) for r in ref_state_pin_rows()}
    assert pins["MAHARASHTRA"] == (40, 44) and pins["APO"] == (90, 99) and "FOREIGN" not in pins
    assert len(pins) == 37  # consumer states 01-25, 27-37 and APO 99 (foreign 77 has no range)


def test_tables_create_and_round_trip(engine):
    assert set(ENTITY_TABLES[n].name for n in EXPECTED_ENTITIES) <= set(
        inspect(engine).get_table_names(schema="automn")
    )
    now = datetime(2026, 10, 21)
    with engine.begin() as conn:
        assert conn.execute(select(func.count()).select_from(ref_code)).scalar() == len(ref_code_rows())
        snap = conn.execute(
            extract_snapshot.insert().values(
                tenant_id="nbfc_001",
                submission_type="CONSUMER",
                as_of_date=date(2026, 9, 30),
                canonical_version="1.0",
                status="BUILDING",
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        conn.execute(
            ENTITY_TABLES["account"]
            .insert()
            .values(
                snapshot_id=snap,
                account_key="L1",
                account_number="B101PL1",
                account_type="05",
                date_opened=date(2024, 1, 1),
                high_credit_amount=Decimal("300000.00"),
                current_balance=Decimal("-2450.50"),
                days_past_due=0,
            )
        )
        conn.execute(ENTITY_TABLES["account"].insert().values(snapshot_id=snap, account_type="HLX"))  # unmapped kept
        row = conn.execute(select(ENTITY_TABLES["account"]).where(ENTITY_TABLES["account"].c.account_key == "L1")).one()
    rec = record_from_row("account", row)
    assert type(rec) is RECORD_TYPES["account"] and rec.__class__.__name__ == "AccountRecord"
    assert (rec.snapshot_id, rec.current_balance, rec.emi_amount) == (snap, Decimal("-2450.50"), None)


def test_deleting_snapshot_cascades(engine):
    now = datetime(2026, 10, 21)
    with engine.begin() as conn:
        snap = conn.execute(
            extract_snapshot.insert().values(
                tenant_id="nbfc_001",
                submission_type="CONSUMER",
                as_of_date=date(2026, 9, 30),
                canonical_version="1.0",
                status="BUILDING",
                created_at=now,
                updated_at=now,
            )
        ).inserted_primary_key[0]
        conn.execute(ENTITY_TABLES["party"].insert(), [dict(snapshot_id=snap, party_key=f"C{i}") for i in range(3)])
        conn.execute(delete(extract_snapshot).where(extract_snapshot.c.snapshot_id == snap))
        assert conn.execute(select(func.count()).select_from(ENTITY_TABLES["party"])).scalar() == 0


def test_snapshot_unique_per_batch_tenant_type(engine):
    from sqlalchemy.exc import IntegrityError

    now = datetime(2026, 10, 21)
    values = dict(
        tenant_id="nbfc_001",
        submission_type="CONSUMER",
        as_of_date=date(2026, 9, 30),
        batch_id="b1",
        canonical_version="1.0",
        status="BUILDING",
        created_at=now,
        updated_at=now,
    )
    with engine.begin() as conn:
        conn.execute(extract_snapshot.insert().values(**values))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(extract_snapshot.insert().values(**values))
