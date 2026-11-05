"""Real-database test support (SQL Server, Postgres) for the `db` tests: schema, migrations, reference data, views,
and stand-ins for the client's LMS views in schema `wip` (on rarfincare: views over its loan system). The source
views join them, so a database test needs them: tables with the columns the views read and plausible types."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import Engine, Table, inspect

import canonical.staging.tables  # noqa: F401  (registers all app tables)
from canonical.db import SCHEMA, metadata
from canonical.migrate import AZURE_MIGRATIONS, apply_migrations, apply_views, run_batch
from canonical.reference import seed as seed_reference
from canonical.tenants import TenantRepository
from canonical.testing import TENANT

D, N, INT, T, S = "DATE", "DECIMAL(20, 2)", "INT", "VARCHAR(40)", "VARCHAR(100)"

CONSUMER = {
    "AccountNumber": S,
    "TransactionDate": D,
    "AssetClassification": S,
    "TL_CreditFacilityStatus": INT,
    "TL_DateDisbursed": T,
    "TL_DateofLastPayment": T,
    "TL_DateClosed": T,
    "TL_DateReported": T,
    "TL_NoofDaysPastDue": INT,
    "TL_RepaymentTenure": INT,
    "TL_RateofInterest": N,
    **{
        c: N
        for c in (
            "TL_SanctionedAmt",
            "TL_CurrentBalance",
            "TL_AmtOverdue",
            "TL_CreditLimit",
            "TL_CashLimit",
            "TL_EMIAmount",
            "TL_WrittenoffAmountTotal",
            "TL_WrittenOffPrincipalAmount",
            "TL_SettlementAmt",
            "TL_ActualPaymentAmt",
        )
    },
}

COMMERCIAL = {
    "AccountNumber": S,
    "SanctionRefNo": S,
    "transactiondate": D,
    "AcClosedDate": D,
    "LastPaymentDate": D,
    "IsAccountClosed": S,
    "RBIODDays": INT,
    "Tenure": INT,
    "OTHER_BK": S,
    "Major reasons for Restructuring": INT,
    "Asset based Security coverage": INT,
    "Guarantee Coverage": INT,
    **{
        c: T
        for c in (
            "Sanction Date",
            "Account Status Date",
            "Asset Classification Date",
            "Date Classified as Wilful Default",
            "Loan Renewal Date",
            "Maturity Date",
            "UFCE Date",
        )
    },
    **{
        c: S
        for c in (
            "Account Status",
            "Asset Classification",
            "Bank Remark Code",
            "Currency Code",
            "Dispute ID No.",
            "Previous Account Number",
        )
    },
    **{
        c: N
        for c in (
            "Amount Overdue",
            "Amount of Contracts Classified as NPA",
            "Current Balance",
            "Drawing Power",
            "High Credit",
            "Installment Amount",
            "Last Repaid Amount",
            "Notional Amount of Outstanding Restructured Contracts",
            "Overdue Bucket 01 ( 1 – 30 days)",
            "Overdue Bucket 02 ( 31 – 60 days)",
            "Overdue Bucket 03 ( 61 – 90 days)",
            "Overdue Bucket 04 (91 – 180 days)",
            "Overdue Bucket 05 (Above 180 days)",
            "Sanctioned Amount",
            "Settled Amount",
            "UFCE Amount",
            "Written Off Amount",
        )
    },
}

STUBS = {"VwConsumerSubmissionAccountSegment": CONSUMER, "VwCommercialSubmissionCR": COMMERCIAL}


def _name(engine: Engine, name: str) -> str:
    """Postgres folds unquoted names to lower case, as the generated views write them; names with spaces are quoted."""
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
            cols = ", ".join(f"{_name(engine, c)} {t}" for c, t in columns.items())
            conn.exec_driver_sql(f"CREATE TABLE wip.{table} ({cols})")


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
    apply_views(engine)


def prepare_postgres(engine: Engine) -> None:
    """Postgres: the app tables from the SQLAlchemy metadata, the reference data, the stand-ins, the views."""
    _schemas(engine)
    metadata.create_all(engine)
    _seed(engine)
    create_lms_stubs(engine)
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
