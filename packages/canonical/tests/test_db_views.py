"""The migrations and source views on real databases (CI job "Database: views and migrations"; marked `db`).

- SQL Server (stands in for Azure SQL): the baseline as the administrator runs it, then 002-006 through the runner,
  then the T-SQL views. Every view must compile and run, and every column the app tables define must exist in the
  migrated schema with a compatible type (the migrations and the SQLAlchemy tables must not drift apart).
- Postgres: the app tables from the SQLAlchemy metadata, then the generated Postgres views.

The client's LMS views (schema `wip`, e.g. wip.VwConsumerSubmissionAccountSegment on rarfincare) are stand-in tables
with the columns the views read (lms_stub.py).

    BSC_MSSQL_URL=mssql+pyodbc://sa:...@localhost:1433/bsc?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes
    BSC_POSTGRES_URL=postgresql+psycopg://postgres:...@localhost:5432/bsc
    uv run pytest -m db
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from can_lms_stub import create_lms_stubs
from sqlalchemy import Engine, inspect, text

import canonical.staging.loads
import canonical.staging.tables  # noqa: F401  (registers all app tables)
from canonical.db import SCHEMA, make_engine, metadata
from canonical.migrate import AZURE_MIGRATIONS, apply_migrations, apply_views, pending_migrations

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
def mssql() -> Iterator[Engine]:
    engine = _engine("BSC_MSSQL_URL")
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for schema in (SCHEMA, "wip"):
            conn.exec_driver_sql(f"IF SCHEMA_ID('{schema}') IS NULL EXEC('CREATE SCHEMA {schema}')")
        # the baseline as the administrator runs it: one script, its own transaction
        conn.exec_driver_sql((AZURE_MIGRATIONS / "001_baseline.sql").read_text("utf-8"))
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def mssql_migrated(mssql: Engine) -> Engine:
    assert [p.stem for p in pending_migrations(mssql)] == [p.stem for p in sorted(AZURE_MIGRATIONS.glob("*.sql"))][1:]
    apply_migrations(mssql)
    create_lms_stubs(mssql)
    apply_views(mssql)
    return mssql


def test_mssql_migrations_all_applied(mssql_migrated: Engine) -> None:
    assert pending_migrations(mssql_migrated) == []
    assert apply_migrations(mssql_migrated) == []


def test_mssql_views_run_and_can_be_applied_again(mssql_migrated: Engine) -> None:
    views = _views(mssql_migrated)
    assert {"vw_consumer_account", "vw_consumer_party", "vw_commercial_account", "vw_ref_label"} <= set(views)
    with mssql_migrated.connect() as conn:
        for v in views:
            conn.execute(text(f"SELECT TOP 1 * FROM {SCHEMA}.{v}")).all()
    apply_views(mssql_migrated)  # CREATE OR ALTER: safe to run again


def _family(type_: object) -> str:
    name = type(type_).__name__.upper()
    for family, words in {
        "text": ("CHAR", "TEXT", "STRING", "CLOB"),
        "datetime": ("DATETIME", "TIMESTAMP"),
        "date": ("DATE",),
        "bool": ("BOOL", "BIT"),
        "number": ("INT", "NUMERIC", "DECIMAL", "FLOAT", "REAL", "DOUBLE"),
    }.items():
        if any(w in name for w in words):
            return family
    return name


def test_mssql_schema_matches_the_app_tables(mssql_migrated: Engine) -> None:
    live = inspect(mssql_migrated)
    names = set(live.get_table_names(schema=SCHEMA))
    problems = []
    for table in metadata.sorted_tables:
        if table.name not in names:
            if table.name not in TABLES_NOT_IN_MIGRATIONS:
                problems.append(f"{table.name}: not created by the migrations")
            continue
        cols = {c["name"]: c for c in live.get_columns(table.name, schema=SCHEMA)}
        for col in table.columns:
            got = cols.get(col.name)
            if got is None:
                problems.append(f"{table.name}.{col.name}: missing")
                continue
            want, have = _family(col.type), _family(got["type"])
            if want != have and {want, have} != {"bool", "number"}:
                problems.append(f"{table.name}.{col.name}: {col.type} in the app, {got['type']} in the database")
            length = getattr(col.type, "length", None)
            if length and getattr(got["type"], "length", None) not in (None, length):
                problems.append(f"{table.name}.{col.name}: length {length} in the app, {got['type'].length} in the db")
    assert not problems, "\n".join(problems)


@pytest.fixture(scope="module")
def postgres() -> Iterator[Engine]:
    engine = _engine("BSC_POSTGRES_URL")
    with engine.begin() as conn:
        for schema in (SCHEMA, "wip"):
            conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    metadata.create_all(engine)
    create_lms_stubs(engine)
    apply_views(engine)
    yield engine
    engine.dispose()


def test_postgres_views_run(postgres: Engine) -> None:
    views = _views(postgres)
    assert {"vw_consumer_account", "vw_consumer_party", "vw_commercial_account", "vw_ref_label"} <= set(views)
    with postgres.connect() as conn:
        for v in views:
            conn.execute(text(f"SELECT * FROM {SCHEMA}.{v} LIMIT 1")).all()
    apply_views(postgres)  # CREATE OR REPLACE: safe to run again
