"""End to end on real databases (marked `db`; CI job "Database"): an uploaded workbook and the client's LMS rows go
through rarfincare's real source views (canonical/migrations) into a canonical snapshot. Edge cases the views handle:
labels -> canonical values, the 1-digit status padded (5 -> 05), an impossible date -> NULL, the newest upload's
demographics, the as-of date filter, and one row per identifier / phone / e-mail present. Both extract strategies
must give the same rows."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import Engine, select, text
from src_helpers import workbook

from canonical.db import make_engine
from canonical.enums import ExtractStrategy
from canonical.schema import ENTITY_TABLES, ref_code_translation
from canonical.snapshot import SnapshotRepository
from canonical.testing import TENANT
from canonical.testing_db import lms_column, prepare_mssql, prepare_postgres
from sources.excel import load_workbook
from sources.extract import run_extract
from sources.views import check_views, compile_views

pytestmark = pytest.mark.db

AS_OF = date(2026, 10, 31)
_batches = count(1)


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


def borrower(account: str, **values: str) -> dict[str, str]:
    row = {
        "curr_new_account_no": account,
        "consumer_name": "ASHA KUMAR",
        "date_of_birth": "15081985",
        "gender_label": "Female",
        "ownership_indicator_label": "Individual",
        "income_tax_id_number": "ABCPK1234F",
        "telephone_no_mobile": "9876543210",
        "email_id_1": "asha@example.com",
        "address_line_1": "12 MAIN ROAD CHENNAI",
        "state_code_1_label": "Tamil Nadu",
        "pin_code_1": "600001",
        "address_category_1_label": "Residence Address",
        "account_type": "02",
    }
    return row | values


def add_lms_rows(engine: Engine, *rows: dict) -> None:
    for row in rows:
        cols = ", ".join(lms_column(engine, c) for c in row)
        names = ", ".join(f":p{i}" for i in range(len(row)))
        with engine.begin() as conn:
            conn.execute(
                text(f"INSERT INTO wip.VwConsumerSubmissionAccountSegment ({cols}) VALUES ({names})"),
                {f"p{i}": v for i, v in enumerate(row.values())},
            )


def upload(engine: Engine, *rows: dict, at: datetime) -> int:
    result = load_workbook(
        engine,
        "CONSUMER",
        workbook("CONSUMER", {"Consumer": list(rows)}),
        tenant_id=TENANT.tenant_id,
        file_name="consumer.xlsx",
        loaded_by="ci",
        clock=lambda: at,
    )
    assert result.status == "LOADED", result.issues
    return result.load_id


def extract(engine: Engine, load_id: int, strategy: ExtractStrategy) -> int:
    return run_extract(
        tenant=replace(TENANT, extract_strategy=strategy),
        compiled=compile_views(engine, "CONSUMER"),
        submission_type="CONSUMER",
        as_of_date=AS_OF,
        app_engine=engine,
        source_engine=engine,
        snapshots=SnapshotRepository(engine),
        owner="ci",
        batch_id=f"db-{next(_batches)}",
        load_id=load_id,
    ).snapshot_id


def rows(engine: Engine, entity: str, snapshot_id: int, *columns: str) -> list[tuple]:
    table = ENTITY_TABLES[entity]
    query = select(*(table.c[c] for c in columns)).where(table.c.snapshot_id == snapshot_id).order_by(*columns)
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(query)]


def canonical_code(engine: Engine, code_list: str, output_code: str) -> str:
    t = ref_code_translation.c
    query = select(t.code).where(
        t.submission_type == "CONSUMER", t.code_list == code_list, t.output_code == output_code
    )
    with engine.connect() as conn:
        return conn.execute(query.order_by(t.code)).scalars().first() or output_code


def test_an_upload_and_the_lms_rows_become_a_canonical_snapshot(db: Engine) -> None:
    run = next(_batches)
    a1, a2, a3 = (f"E2E{run}-{n}" for n in (1, 2, 3))  # unique per run: the database is shared by the db tests
    lms = {
        "TL_DateDisbursed": "15012020",
        "TL_SanctionedAmt": Decimal("500000.00"),
        "TL_CurrentBalance": Decimal("123456.78"),
        "TL_NoofDaysPastDue": 0,
    }
    add_lms_rows(
        db,
        {"AccountNumber": a1, "TransactionDate": AS_OF, "TL_CreditFacilityStatus": 5, "TL_DateClosed": "31022025"}
        | lms,
        {"AccountNumber": a2, "TransactionDate": AS_OF} | lms,
        {"AccountNumber": a1, "TransactionDate": date(2026, 9, 30)} | lms,  # another date: not in this snapshot
        {"AccountNumber": a3, "TransactionDate": AS_OF} | lms,  # never uploaded: ignored
    )
    first = upload(
        db, borrower(a1), borrower(a2, consumer_name="RAVI S", gender_label="male "), at=datetime(2026, 11, 1)
    )
    upload(db, borrower(a1, consumer_name="ASHA K KUMAR"), at=datetime(2026, 11, 2))  # newer demographics for a1

    counted, errors, warnings = check_views(db, "CONSUMER", AS_OF, first)
    assert errors == [] and counted["account"] == 2 and counted["party"] == 2, (errors, warnings)

    snap = extract(db, first, ExtractStrategy.INSERT_SELECT)
    assert rows(db, "account", snap, "account_key") == [(a1,), (a2,)]
    (_, status, closed, opened, balance), _ = rows(
        db, "account", snap, "account_key", "credit_facility_status", "date_closed", "date_opened", "current_balance"
    )
    assert status == canonical_code(db, "credit_facility_status", "05")  # 5 -> '05' -> its canonical value
    assert closed is None and opened == date(2020, 1, 15)  # 31.02.2025 is not a date
    assert balance == Decimal("123456.78")
    assert rows(db, "party", snap, "party_key", "full_name", "gender", "birth_date") == [
        (f"{a1}-2", "ASHA K KUMAR", "FEMALE", date(1985, 8, 15)),  # the newest upload's name for a1
        (f"{a2}-3", "RAVI S", "MALE", date(1985, 8, 15)),  # label matched ignoring case and spaces
    ]
    assert rows(db, "account_holder", snap, "account_key", "ownership_indicator") == [
        (a1, "INDIVIDUAL"),
        (a2, "INDIVIDUAL"),
    ]
    assert rows(db, "party_address", snap, "party_key", "state_code", "address_category", "priority") == [
        (f"{a1}-2", "TAMIL_NADU", "RESIDENCE", 1),
        (f"{a2}-3", "TAMIL_NADU", "RESIDENCE", 1),
    ]
    assert len(rows(db, "party_identifier", snap, "party_key", "id_type")) == 2
    assert rows(db, "party_phone", snap, "party_key", "phone_type") == [(f"{a1}-2", "MOBILE"), (f"{a2}-3", "MOBILE")]
    assert len(rows(db, "party_email", snap, "party_key")) == 2

    streamed = extract(db, first, ExtractStrategy.STREAM)
    for entity in ("account", "party", "account_holder", "party_address", "party_identifier", "party_phone"):
        columns = [c.name for c in ENTITY_TABLES[entity].columns if c.name not in ("row_id", "snapshot_id")]
        assert rows(db, entity, streamed, *columns) == rows(db, entity, snap, *columns), entity


# ---------------------------------------------------------------- ADR 0002 PR 4: snapshot from L2a + L2b
MASTER_DAY = date(2026, 12, 31)


def _master_data(engine: Engine) -> None:
    """Demographics through apply_upload; positions as an LMS upload (vw_pos_upload), on a date no other test uses."""
    from sqlalchemy import insert

    from canonical.master.apply import AccountIn, PersonIn, SanctionIn, apply_upload
    from canonical.position import stg_lms_position
    from canonical.staging.tables import input_load

    with engine.begin() as conn:
        load = conn.execute(
            insert(input_load).values(
                tenant_id=TENANT.tenant_id,
                submission_type="LMS",
                file_name="lms.xlsx",
                sha256="0" * 64,
                status="LOADED",
                loaded_by="test",
                loaded_at=datetime(2027, 1, 1),
            )
        ).inserted_primary_key[0]
        rows = [("MST-C1", "31122026", "1000.50"), ("MST-C1", "15112026", "1100.00"), ("MST-C1", "20112026", "1090.00"),
                ("MST-K1", "31122026", "5000.00")]  # fmt: skip
        conn.execute(
            insert(stg_lms_position),
            [dict(load_id=load, source_row=i + 2, account_number=a, position_date=d, current_balance=b, days_past_due="0")
             for i, (a, d, b) in enumerate(rows)],
        )  # fmt: skip
    sanctions = [
        SanctionIn("MST-C1", PersonIn("INDIVIDUAL", {"full_name": "Asha Kumar", "birth_date": date(1985, 8, 15),
                                                     "ownership_indicator": "INDIVIDUAL"},
                                      identifiers=[{"id_type": "PAN", "id_number": "ABCPK1234F"}],
                                      phones=[{"phone_number": "9876543210", "phone_type": "MOBILE", "priority": 1}])),
        SanctionIn("MST/SR-K", PersonIn("ENTITY", {"full_name": "Acme Traders"}),
                   relationships=[{"related_key": "MR1", "relationship_type": "PROMOTER_DIRECTOR", "din": "01234567",
                                   "related_type": "INDIAN_INDIVIDUAL", "full_name": "Ravi Director",
                                   "address_line_1": "1 Road", "state_code": "TAMIL_NADU", "pin_code": "600001",
                                   "telephone": "2222", "telephone_area_code": "044"}],
                   guarantors=[{"related_key": "MR1", "full_name": "Ravi Director", "din": "01234567",
                                "mobile": "067-42303999,43137080"}],
                   securities=[{"security_value": Decimal("700.00")}],
                   cheques=[{"amount": Decimal("10.00"), "dishonour_date": date(2026, 12, 1)}]),
    ]  # fmt: skip
    accounts = [AccountIn("MST-C1", "MST-C1", {"account_type": "PERSONAL_LOAN", "suit_filed_status": "NO_SUIT_FILED"}),
                AccountIn("MST-K1", "MST/SR-K", {"account_type": "TERM_LOAN"})]  # fmt: skip
    apply_upload(engine, TENANT.tenant_id, load, sanctions, accounts)


@pytest.mark.parametrize("strategy", [ExtractStrategy.INSERT_SELECT, ExtractStrategy.STREAM])
def test_master_extract_runs_on_real_databases(db: Engine, strategy: ExtractStrategy) -> None:
    from canonical.master.tables import dm_sanction
    from sources.master import compile_master

    with db.connect() as conn:
        found = select(dm_sanction.c.sanction_id).where(dm_sanction.c.sanction_reference_number == "MST-C1")
        if conn.execute(found).first() is None:
            conn.close()
            _master_data(db)
    tenant = replace(TENANT, extract_strategy=strategy)
    got = {}
    for submission_type in ("CONSUMER", "COMMERCIAL"):
        result = run_extract(
            tenant=tenant, compiled=compile_master(db, submission_type, history=True), submission_type=submission_type,
            as_of_date=MASTER_DAY, app_engine=db, source_engine=db, snapshots=SnapshotRepository(db), owner="test",
            batch_id=f"master-{next(_batches)}",
        )  # fmt: skip
        got[submission_type] = result
    c, k = got["CONSUMER"].row_counts, got["COMMERCIAL"].row_counts
    assert (c["account"], c["party"], c["account_holder"], c["party_identifier"], c["party_phone"]) == (1, 1, 1, 1, 1)
    assert c["account_history"] == 1  # November: the latest of the two earlier positions
    assert (k["account"], k["party"], k["relationship"], k["guarantor"], k["security"], k["cheque_dishonour"]) == (
        1, 1, 1, 1, 1, 1
    )  # fmt: skip
    t = ENTITY_TABLES
    with db.connect() as conn:
        sid_c, sid_k = got["CONSUMER"].snapshot_id, got["COMMERCIAL"].snapshot_id
        acc = conn.execute(select(t["account"]).where(t["account"].c.snapshot_id == sid_c)).one()
        hist = conn.execute(select(t["account_history"]).where(t["account_history"].c.snapshot_id == sid_c)).one()
        rel = conn.execute(select(t["relationship"]).where(t["relationship"].c.snapshot_id == sid_k)).one()
    assert (acc.account_key, acc.current_balance, acc.suit_filed_status) == (
        "MST-C1",
        Decimal("1000.50"),
        "NO_SUIT_FILED",
    )
    assert (hist.history_month, hist.current_balance) == (date(2026, 11, 20), Decimal("1090.00"))
    assert (rel.party_key, rel.related_key, rel.din, rel.state_code, rel.telephone, rel.telephone_area_code) == (
        "MST/SR-K", "MR1", "01234567", "TAMIL_NADU", "2222", "044"
    )  # fmt: skip


# ---------------------------------------------------------------- ADR 0002 PR 6: parity through rarfincare's real views
PARITY_DAY = date(2027, 1, 31)  # a date no other test uses: the master reports every sanction with a position on it


def test_parity_through_the_real_views_is_identical(db: Engine) -> None:
    from sources.master_parity import compare

    run = next(_batches)
    a1, a2 = (f"PAR{run}-{n}" for n in (1, 2))
    lms = {"TL_DateDisbursed": "15012020", "TL_SanctionedAmt": Decimal("500000.00"), "TL_NoofDaysPastDue": 3,
           "TL_CurrentBalance": Decimal("1000.50"), "TL_CreditFacilityStatus": 5, "AssetClassification": "STD"}  # fmt: skip
    add_lms_rows(db, {"AccountNumber": a1, "TransactionDate": PARITY_DAY} | lms,
                 {"AccountNumber": a2, "TransactionDate": PARITY_DAY} | lms)  # fmt: skip
    first = upload(db, borrower(a1), borrower(a2, consumer_name="RAVI S", telephone_no_office="0442222333",
                                              email_id_2="ravi2@example.com"), at=datetime(2027, 2, 1))  # fmt: skip
    report = compare(db, db, TENANT.tenant_id, "CONSUMER", PARITY_DAY, first)
    assert report["same"], report
    assert report["entities"]["account"]["views"] == 2 and report["entities"]["party_phone"]["views"] == 3
    again = compare(db, db, TENANT.tenant_id, "CONSUMER", PARITY_DAY, first)  # applying the same upload again
    assert again["same"] and "0 updated" in again["summary"][0]
