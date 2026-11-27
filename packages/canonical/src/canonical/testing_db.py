"""Real-database test support (SQL Server) for the `db` tests: schema, migrations, reference data, views,
and stand-ins for the client's LMS views in schema `wip` (on rarfincare: views over its loan system). The source
views join them, so a database test needs them: tables with the columns the views read and plausible types."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Engine, Table, inspect

import canonical.position
import canonical.staging.tables  # noqa: F401  (registers all app tables)
from canonical.db import SCHEMA
from canonical.lms_cr import SOURCES, STAND_INS
from canonical.migrate import AZURE_MIGRATIONS, apply_migrations, apply_views, run_batch
from canonical.reference import seed as seed_reference
from canonical.tenants import TenantRepository
from canonical.testing import TENANT

# The stand-ins have the procedures' columns and types (canonical.lms_cr), without SelectionReason (the stand-in
# procedures add it).
STUBS = {
    STAND_INS[kind]: {c: t for c, t in source.columns.items() if c != "SelectionReason"}
    for kind, source in SOURCES.items()
}


def _name(engine: Engine, name: str) -> str:
    """Names with spaces or dots are quoted; others are left as the views write them."""
    if " " not in name and "." not in name:
        return name
    return f"[{name}]" if engine.dialect.name == "mssql" else f'"{name}"'


def create_lms_stubs(engine: Engine) -> None:
    """The stand-in LMS tables in schema wip (created once; safe to call again)."""
    existing = {t.lower() for t in inspect(engine).get_table_names(schema="wip")}
    with engine.begin() as conn:
        for table, columns in STUBS.items():
            if table.lower() in existing:
                continue
            cols = ", ".join(f"{_name(engine, c)} {t.compile(dialect=engine.dialect)}" for c, t in columns.items())
            conn.exec_driver_sql(f"CREATE TABLE wip.{table} ({cols})")


def create_lms_procedures(engine: Engine) -> None:
    """Stand-ins for the client's LMS procedures (rarfincare: dbo.usp_Get*SubmissionCR, owner 2026-11-16): they return
    the stand-in table's rows for @TransactionDate, as canonical.lms_cr.fetch_cr calls them (SQL Server)."""
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.exec_driver_sql("IF SCHEMA_ID('dbo') IS NULL EXEC('CREATE SCHEMA dbo')")
        for kind, source in SOURCES.items():
            stand_in, date_col = STAND_INS[kind], _name(engine, source.date_column)
            conn.exec_driver_sql(
                f"CREATE OR ALTER PROCEDURE {source.procedure} @TransactionDate DATE, "  # noqa: S608  (fixed names)
                "@PreviousTransactionDate DATE, @CloseDate DATE AS "
                f"SELECT *, CAST('stand-in' AS NVARCHAR(400)) AS SelectionReason FROM wip.{stand_in} "
                f"WHERE {date_col} = @TransactionDate"
            )


def mirror_lms(engine: Engine) -> None:
    """Copy every stand-in LMS row into the tables the source views read (automn.lms_cr_*), as fetches for all their
    dates would: for tests that seed the stand-ins and then read the views directly, on any date."""
    with engine.begin() as conn:
        for kind, source in SOURCES.items():
            cols = ", ".join(_name(engine, c) for c in STUBS[STAND_INS[kind]])
            conn.exec_driver_sql(f"DELETE FROM {SCHEMA}.{source.table.name}")  # noqa: S608  (fixed names)
            conn.exec_driver_sql(
                f"INSERT INTO {SCHEMA}.{source.table.name} ({cols}) SELECT {cols} FROM wip.{STAND_INS[kind]}"  # noqa: S608
            )


def lms_column(engine: Engine, name: str) -> str:
    """A stand-in LMS column as SQL on this database (quoted when it has spaces)."""
    return _name(engine, name)


def _schemas(engine: Engine) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for schema in (SCHEMA, "wip"):
            if engine.dialect.name == "mssql":
                conn.exec_driver_sql(f"IF SCHEMA_ID('{schema}') IS NULL EXEC('CREATE SCHEMA {schema}')")
            else:
                conn.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {schema}")


def _seed(engine: Engine) -> None:
    TenantRepository(engine, clock=lambda: datetime(2026, 10, 1)).upsert(TENANT)
    seed_reference(engine)


def prepare_mssql(engine: Engine) -> None:
    """SQL Server as on rarfincare: the baseline (as the administrator runs it), migrations 002-006, the reference
    data, the stand-in LMS tables, then the views (labels.sql fills the labels, so it runs after the seed)."""
    _schemas(engine)
    if "schema_migration" not in inspect(engine).get_table_names(schema=SCHEMA):
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            run_batch(conn, (AZURE_MIGRATIONS / "001_baseline.sql").read_text("utf-8"))
    apply_migrations(engine)
    _seed(engine)
    create_lms_stubs(engine)
    create_lms_procedures(engine)
    apply_views(engine)


def _family(type_: object) -> str:
    name = type(type_).__name__.upper()
    for family, words in {
        "text": ("CHAR", "TEXT", "STRING", "CLOB", "UNICODE"),
        "datetime": ("DATETIME", "TIMESTAMP"),
        "date": ("DATE",),
        "bool": ("BOOL", "BIT"),
        "number": ("INT", "NUMERIC", "DECIMAL", "FLOAT", "REAL", "DOUBLE"),
    }.items():
        if any(w in name for w in words):
            return family
    return name


def schema_drift(
    engine: Engine, tables: Iterable[Table], not_yet_migrated: frozenset[str] | set[str] = frozenset()
) -> list[str]:
    """Differences between SQLAlchemy tables and the migrated database: a missing table or column, another type
    family, another length. Empty when they agree."""
    live = inspect(engine)
    names = set(live.get_table_names(schema=SCHEMA))
    problems = []
    for table in tables:
        if table.name not in names:
            if table.name not in not_yet_migrated:
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
                problems.append(
                    f"{table.name}.{col.name}: length {length} in the app, {getattr(got['type'], 'length', None)} in the db"
                )
    return problems
