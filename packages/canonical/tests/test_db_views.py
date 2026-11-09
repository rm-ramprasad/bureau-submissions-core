"""The migrations and source views on real databases (CI job "Database: views and migrations"; marked `db`).

- SQL Server (stands in for Azure SQL): the baseline as the administrator runs it, then 002-006 through the runner,
  then the T-SQL views. Every view must compile and run, and every column the app tables define must exist in the
  migrated schema with a compatible type (the migrations and the SQLAlchemy tables must not drift apart).
- Postgres: the app tables from the SQLAlchemy metadata, then the generated Postgres views.

The client's LMS views (schema `wip`, e.g. wip.VwConsumerSubmissionAccountSegment on rarfincare) are stand-in tables
with the columns the views read (canonical.testing_db).

    BSC_MSSQL_URL=mssql+pyodbc://sa:...@localhost:1433/bsc?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
    BSC_POSTGRES_URL=postgresql+psycopg://postgres:...@localhost:5432/bsc
    uv run pytest -m db
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text

import canonical.master.tables
import canonical.position
import canonical.staging.loads
import canonical.staging.tables  # noqa: F401  (registers all app tables)
from canonical.db import SCHEMA, make_engine, metadata
from canonical.migrate import AZURE_MIGRATIONS, apply_migrations, apply_views, pending_migrations
from canonical.testing_db import prepare_mssql, prepare_postgres, schema_drift

pytestmark = pytest.mark.db

TABLES_NOT_IN_MIGRATIONS: set[str] = set()  # app tables a migration has not created yet


def _engine(var: str) -> Engine:
    url = os.environ.get(var)
    if not url:
        pytest.skip(f"{var} not set")
    return make_engine(url)


def _views(engine: Engine) -> list[str]:
    return sorted(v for v in inspect(engine).get_view_names(schema=SCHEMA) if v.startswith("vw_"))


@pytest.fixture(scope="module")
def mssql_migrated() -> Iterator[Engine]:
    engine = _engine("BSC_MSSQL_URL")
    prepare_mssql(engine)
    yield engine
    engine.dispose()


def test_mssql_migrations_all_applied(mssql_migrated: Engine) -> None:
    assert pending_migrations(mssql_migrated) == []
    with mssql_migrated.connect() as conn:
        versions = conn.execute(text(f"SELECT version FROM {SCHEMA}.schema_migration ORDER BY version")).scalars()
        assert list(versions) == [p.stem for p in sorted(AZURE_MIGRATIONS.glob("*.sql"))]
    assert apply_migrations(mssql_migrated) == []


def test_mssql_views_run_and_can_be_applied_again(mssql_migrated: Engine) -> None:
    views = _views(mssql_migrated)
    assert {"vw_consumer_account", "vw_consumer_party", "vw_commercial_account", "vw_ref_label"} <= set(views)
    with mssql_migrated.connect() as conn:
        for v in views:
            conn.execute(text(f"SELECT TOP 1 * FROM {SCHEMA}.{v}")).all()
    apply_views(mssql_migrated)  # CREATE OR ALTER: safe to run again


def test_mssql_labels_sql_runs_to_the_end(mssql_migrated: Engine) -> None:
    """labels.sql is hundreds of UPDATEs in one batch: every one must apply, the last ones included."""
    with mssql_migrated.connect() as conn:
        missing = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {SCHEMA}.ref_code_translation WHERE label IS NULL AND submission_type = 'CONSUMER' AND code_list IN "
                "('gender', 'ownership_indicator')"
            )
        ).scalar_one()
    assert missing == 0


def test_mssql_schema_matches_the_app_tables(mssql_migrated: Engine) -> None:
    problems = schema_drift(mssql_migrated, metadata.sorted_tables, TABLES_NOT_IN_MIGRATIONS)
    assert not problems, "\n".join(problems)


@pytest.fixture(scope="module")
def postgres() -> Iterator[Engine]:
    engine = _engine("BSC_POSTGRES_URL")
    prepare_postgres(engine)
    yield engine
    engine.dispose()


def test_postgres_views_run(postgres: Engine) -> None:
    views = _views(postgres)
    assert {"vw_consumer_account", "vw_consumer_party", "vw_commercial_account", "vw_ref_label"} <= set(views)
    with postgres.connect() as conn:
        for v in views:
            conn.execute(text(f"SELECT * FROM {SCHEMA}.{v} LIMIT 1")).all()
    apply_views(postgres)  # CREATE OR REPLACE: safe to run again


def test_mssql_row_counts_still_work_after_the_views(mssql_migrated: Engine) -> None:
    """run_batch turns NOCOUNT off again: the app relies on UPDATE row counts on reused connections."""
    apply_views(mssql_migrated)
    with mssql_migrated.begin() as conn:
        changed = conn.execute(text(f"UPDATE {SCHEMA}.tenant SET display_name = display_name WHERE 1 = 0")).rowcount
    assert changed == 0


def test_mssql_master_apply_is_stable(mssql_migrated: Engine) -> None:
    """Applying the same upload twice on SQL Server logs no change (types read back compare equal)."""
    from datetime import date
    from decimal import Decimal

    from canonical.master.apply import PartyIn, SanctionIn, SanctionPartyIn, SecurityIn, apply_upload

    party = PartyIn(
        "DBT1",
        "INDIVIDUAL",
        {"full_name": "Test Ünicode", "birth_date": date(1990, 1, 2), "income": Decimal("50000.5")},
        phones=[{"phone_number": "9000000001"}],
    )
    loan = SanctionIn(
        "DBT-L1",
        values={"rate_of_interest": Decimal("12.25"), "repayment_tenure": 36},
        parties=[SanctionPartyIn("DBT1", "BORROWER", {"control_percentage": Decimal("100")})],
        securities=[SecurityIn("s-b"), SecurityIn("S-A")],
    )
    first = apply_upload(mssql_migrated, "dbtest", 1, [party], [loan])
    again = apply_upload(mssql_migrated, "dbtest", 2, [party], [loan])
    assert (first.parties_added, first.sanctions_added) == (1, 1)
    assert (again.parties_unchanged, again.sanctions_unchanged, again.changes) == (1, 1, 0)


def _position_data(engine: Engine) -> None:
    """One consumer and one commercial LMS row, and two uploads of the same account and date (the newer wins)."""
    from datetime import datetime

    from sqlalchemy import insert

    from canonical.position import stg_lms_position
    from canonical.staging.tables import input_load
    from canonical.testing_db import lms_column as q

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM wip.VwConsumerSubmissionAccountSegment"))
        conn.execute(text("DELETE FROM wip.VwCommercialSubmissionCR"))
        conn.execute(
            text(
                "INSERT INTO wip.VwConsumerSubmissionAccountSegment (AccountNumber, TransactionDate, "
                "TL_CurrentBalance, TL_AmtOverdue, TL_NoofDaysPastDue, TL_CreditFacilityStatus, TL_DateClosed) "
                "VALUES ('POS-C1', '2026-10-31', 1000.50, 20.00, 15, NULL, '31-10-2026')"
            )
        )
        conn.execute(
            text(
                f"INSERT INTO wip.VwCommercialSubmissionCR (AccountNumber, transactiondate, {q(engine, 'Current Balance')}, "
                f"RBIODDays, IsAccountClosed, AcClosedDate, {q(engine, 'Overdue Bucket 01 ( 1 – 30 days)')}, "
                f"{q(engine, 'Account Status')}) "
                "VALUES ('POS-K1', '2026-10-31', 5000.00, 0, 'Yes', '2026-10-30', 7.00, '01')"
            )
        )
        loads = []
        for i in range(2):
            loads.append(
                conn.execute(
                    insert(input_load).values(
                        tenant_id="nbfc_001",
                        submission_type="LMS",
                        file_name=f"lms{i}.xlsx",
                        sha256="0" * 64,
                        status="LOADED",
                        loaded_by="test",
                        loaded_at=datetime(2026, 11, 1, i),
                    )
                ).inserted_primary_key[0]
            )
        for load, balance in zip(loads, ("100", "1,234.50"), strict=True):
            conn.execute(
                insert(stg_lms_position).values(
                    load_id=load,
                    source_row=2,
                    account_number="POS-U1",
                    position_date="31-10-2026",
                    current_balance=balance,
                    days_past_due="3",
                )
            )


@pytest.mark.parametrize("db", ["mssql_migrated", "postgres"])
def test_account_position_view_keeps_the_contract(db: str, request: pytest.FixtureRequest) -> None:
    from datetime import date
    from decimal import Decimal

    from canonical.position import check_view, read_positions

    engine: Engine = request.getfixturevalue(db)
    _position_data(engine)
    assert check_view(engine) == []
    rows = {r["account_number"]: r for r in read_positions(engine, date(2026, 10, 31))}
    assert sorted(rows) == ["POS-C1", "POS-K1", "POS-U1"]
    c, k, u = rows["POS-C1"], rows["POS-K1"], rows["POS-U1"]
    assert (c["current_balance"], c["days_past_due"], c["date_closed"], c["source"]) == (
        Decimal("1000.50"),
        15,
        date(2026, 10, 31),
        "VIEW",
    )
    assert (k["current_balance"], k["overdue_bucket_1"], k["date_closed"], k["credit_facility_status"]) == (
        Decimal("5000.00"),
        Decimal("7.00"),
        date(2026, 10, 30),
        None,
    )
    assert (u["current_balance"], u["days_past_due"], u["source"], u["sanction_number"]) == (
        Decimal("1234.50"),
        3,
        "UPLOAD",
        "",
    )
    assert u["source_load_id"] == max(r["source_load_id"] or 0 for r in rows.values())
    assert [r["account_number"] for r in read_positions(engine, date(2026, 10, 31), ["POS-U1"])] == ["POS-U1"]
    assert read_positions(engine, date(2026, 9, 30)) == []
