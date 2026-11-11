"""MFI step 6 (ADR 0003, provisional workbook) end to end on real databases (marked `db`; CI job "Database"): the sample
workbook goes through the real MFI source views (canonical/migrations/<dialect>/views/mfi.sql) into a canonical
snapshot. MFI codes become canonical values, a member with two loans is one party, relatives and groups come through,
and the upload's as-of date is the accounts' date. Both extract strategies give the same rows."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, select

from canonical.db import make_engine
from canonical.enums import ExtractStrategy
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository
from canonical.staging.checks import run_checks
from canonical.testing import TENANT
from canonical.testing_db import prepare_mssql, prepare_postgres
from sources.excel import load_workbook
from sources.extract import run_extract
from sources.samples import mfi_sample, write_workbook
from sources.views import check_views, compile_views

pytestmark = pytest.mark.db

AS_OF = date(2026, 10, 31)


@pytest.fixture(scope="module", params=["mssql", "postgres"])
def db(request) -> Iterator[Engine]:
    var = {"mssql": "BSC_MSSQL_URL", "postgres": "BSC_POSTGRES_URL"}[request.param]
    url = os.environ.get(var)
    if not url:
        pytest.skip(f"{var} not set")
    engine = make_engine(url)
    (prepare_mssql if request.param == "mssql" else prepare_postgres)(engine)
    yield engine
    engine.dispose()


def rows(engine: Engine, entity: str, snapshot_id: int, *columns: str) -> list[tuple]:
    table = ENTITY_TABLES[entity]
    query = select(*(table.c[c] for c in columns)).where(table.c.snapshot_id == snapshot_id).order_by(*columns)
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(query)]


def extract(engine: Engine, load_id: int, strategy: ExtractStrategy, batch: str) -> int:
    return run_extract(
        tenant=replace(TENANT, extract_strategy=strategy),
        compiled=compile_views(engine, "MFI"),
        submission_type="MFI",
        as_of_date=AS_OF,
        app_engine=engine,
        source_engine=engine,
        snapshots=SnapshotRepository(engine),
        owner="ci",
        batch_id=batch,
        load_id=load_id,
    ).snapshot_id


def test_the_sample_workbook_becomes_an_mfi_snapshot(db: Engine) -> None:
    sample = mfi_sample(AS_OF, groups=4)
    loans, groups = sample["Loans"], sample["Groups"]
    result = load_workbook(db, "MFI", write_workbook("MFI", sample), tenant_id=TENANT.tenant_id,
                           file_name="mfi.xlsx", loaded_by="ci", clock=lambda: datetime(2026, 11, 2),
                           as_of_date=AS_OF)  # fmt: skip
    assert result.status == "LOADED", result.errors
    checks = run_checks(db, result.load_id, "MFI", AS_OF, record=False)
    assert checks.passed, [(i["code"], i["source_row"]) for i in checks.issues]

    counted, errors, _ = check_views(db, "MFI", AS_OF, result.load_id)
    members = {r["member_id"] for r in loans}
    assert errors == [] and counted["account"] == len(loans) and counted["party"] == len(members), (counted, errors)

    snap = extract(db, result.load_id, ExtractStrategy.INSERT_SELECT, "mfi-db-1")
    assert len(rows(db, "account", snap, "account_key")) == len(loans)
    assert len(rows(db, "party", snap, "party_key")) == len(members)  # two loans, one member
    assert len(rows(db, "account_holder", snap, "account_key")) == len(loans)
    assert {g for (g,) in rows(db, "borrower_group", snap, "group_key")} == {g["group_id"] for g in groups}

    first = loans[0]
    (acc,) = [r for r in rows(db, "account", snap, "account_key", "loan_category", "payment_frequency", "loan_type",
                                  "high_credit_amount", "date_opened", "days_past_due", "group_key")
              if r[0] == first["account_number"]]  # fmt: skip
    assert acc[1:] == ("SHG_INDIVIDUAL", "MONTHLY", "TERM_LOAN", Decimal(first["disbursed_amount"]),
                       first["date_disbursed"], 0, first["group_id"])  # fmt: skip
    written_off = next(r for r in loans if r.get("account_status") == "S06")
    assert (written_off["account_number"], "WRITTEN_OFF", "FIRST_PAYMENT_DEFAULT") in rows(
        db, "account", snap, "account_key", "credit_facility_status", "write_off_reason"
    )

    (member,) = [r for r in rows(db, "party", snap, "party_key", "full_name", "gender", "marital_status", "religion",
                                 "member_role", "birth_date") if r[0] == first["member_id"]]  # fmt: skip
    assert member[1:] == (first["member_name"], "FEMALE", "MARRIED", "HINDU", "LEADER", first["date_of_birth"])
    relatives = [r[1:] for r in rows(db, "party_relative", snap, "party_key", "relative_role", "priority", "relation")
                 if r[0] == first["member_id"]]  # fmt: skip
    assert relatives == [("KEY_PERSON", 1, "HUSBAND"), ("NOMINEE", 1, "DAUGHTER"), ("RELATIVE", 1, "FATHER")]
    addresses = [r[1:] for r in rows(db, "party_address", snap, "party_key", "address_category", "state_code")
                 if r[0] == first["member_id"]]  # fmt: skip
    assert [c for c, _ in addresses] == ["PERMANENT", "RESIDENCE"] and {s for _, s in addresses} <= {
        "TAMIL_NADU", "KARNATAKA"}  # fmt: skip
    ids = {t for k, t in rows(db, "party_identifier", snap, "party_key", "id_type") if k == first["member_id"]}
    assert {"VOTER_ID", "AADHAAR"} <= ids

    streamed = extract(db, result.load_id, ExtractStrategy.STREAM, "mfi-db-2")
    for entity in ("account", "party", "account_holder", "party_relative", "party_address", "borrower_group"):
        columns = [c.name for c in ENTITY_TABLES[entity].columns if c.name not in ("row_id", "snapshot_id")]
        assert rows(db, entity, streamed, *columns) == rows(db, entity, snap, *columns), entity
