"""The LMS's CR section (account figures) per as-of date, from the client's stored procedures (owner 2026-11-16).

Until 2026-11-16 the source views read the client's LMS views (rarfincare: wip.VwConsumerSubmissionAccountSegment,
wip.VwCommercialSubmissionCR). Now the LMS hands its figures out through stored procedures:

    dbo.usp_GetConsumerSubmissionCR   @TransactionDate, @PreviousTransactionDate, @CloseDate
    dbo.usp_GetCommercialSubmissionCR @TransactionDate, @PreviousTransactionDate, @CloseDate

A view cannot call a procedure, so fetch_cr runs it for a request's date and saves the rows in automn.lms_cr_consumer /
automn.lms_cr_commercial (migration 024): the same column names as the old views (plus SelectionReason), so the
source views (consumer.sql, commercial.sql, position.sql) and the input checks only change their FROM. One set per
type and date: a new fetch replaces it (the LMS's latest figures); every fetch is logged in automn.lms_cr_fetch.

Dates: @TransactionDate is the request's as-of date; @PreviousTransactionDate and @CloseDate are both the previous
reporting date (previous_cycle_date): 9th -> last day of the previous month, 16th -> 9th, 23rd -> 16th, last day of
the month -> last day of the previous month (owner 2026-11-16).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Engine,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Unicode,
    delete,
    insert,
    text,
)
from sqlalchemy.types import TypeEngine

from canonical.db import metadata


def V(n: int) -> TypeEngine[Any]:
    return String(n)


def DEC(p: int, s: int) -> TypeEngine[Any]:
    return Numeric(p, s)


D: TypeEngine[Any] = Date()
INT: TypeEngine[Any] = Integer()
NV: TypeEngine[Any] = Unicode(4000)

# The columns the source views read, by their names and types in the LMS: the procedures' result sets as rarfincare's
# database describes them (owner 2026-11-17; the same as the old views, plus SelectionReason). Types kept exactly, so
# a value reaches the views as the old views gave it. Other columns a procedure returns are not kept.
CONSUMER_COLUMNS: dict[str, TypeEngine[Any]] = {
    "AccountNumber": V(20),
    "TransactionDate": D,
    "AssetClassification": V(2),
    "TL_CreditFacilityStatus": INT,
    "TL_DateDisbursed": NV,
    "TL_DateofLastPayment": NV,
    "TL_DateClosed": NV,
    "TL_DateReported": NV,
    "TL_NoofDaysPastDue": INT,
    "TL_RepaymentTenure": INT,
    "TL_RateofInterest": DEC(18, 2),
    "TL_SanctionedAmt": INT,
    "TL_CurrentBalance": DEC(19, 0),
    "TL_AmtOverdue": DEC(18, 0),
    "TL_CreditLimit": INT,
    "TL_CashLimit": INT,
    "TL_EMIAmount": DEC(18, 0),
    "TL_WrittenoffAmountTotal": INT,
    "TL_WrittenOffPrincipalAmount": INT,
    "TL_SettlementAmt": INT,
    "TL_ActualPaymentAmt": DEC(18, 2),
    "SelectionReason": V(100),  # 29 in the LMS today: room to grow
}

COMMERCIAL_COLUMNS: dict[str, TypeEngine[Any]] = {
    "AccountNumber": V(20),
    "SanctionRefNo": V(255),
    "transactiondate": D,
    "AcClosedDate": D,
    "LastPaymentDate": D,
    "IsAccountClosed": V(3),
    "RBIODDays": INT,
    "Tenure": INT,
    "OTHER_BK": INT,
    "Major reasons for Restructuring": INT,
    "Asset based Security coverage": INT,
    "Guarantee Coverage": INT,
    "Sanction Date": NV,
    "Account Status Date": NV,
    "Asset Classification Date": INT,
    "Date Classified as Wilful Default": INT,
    "Loan Renewal Date": INT,
    "Maturity Date": NV,
    "UFCE Date": INT,
    "Account Status": V(2),
    "Asset Classification": V(4),
    "Bank Remark Code": INT,
    "Currency Code": V(3),
    "Dispute ID No.": INT,
    "Previous Account Number": V(20),
    **{
        c: INT
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
    "SelectionReason": V(100),  # 29 in the LMS today: room to grow
}


def _column(name: str, type_: TypeEngine[Any]) -> Column[Any]:
    # Names without spaces are left unquoted, as the generated views write them (Postgres folds them to lower case);
    # names with spaces are always quoted.
    return Column(name, type_, quote=None if " " in name else False)


@dataclass(frozen=True)
class CrSource:
    submission_type: str
    procedure: str
    table: Table
    date_column: str
    columns: dict[str, TypeEngine[Any]]


lms_cr_consumer = Table(
    "lms_cr_consumer",
    metadata,
    *(_column(c, t) for c, t in CONSUMER_COLUMNS.items()),
    Index("ix_lms_cr_consumer_date", "TransactionDate", "AccountNumber"),
)

lms_cr_commercial = Table(
    "lms_cr_commercial",
    metadata,
    *(_column(c, t) for c, t in COMMERCIAL_COLUMNS.items()),
    Index("ix_lms_cr_commercial_date", "transactiondate", "AccountNumber"),
)

_PK = BigInteger().with_variant(Integer(), "sqlite")

lms_cr_fetch = Table(  # one row per run of a procedure
    "lms_cr_fetch",
    metadata,
    Column("fetch_id", _PK, primary_key=True, autoincrement=True),
    Column("submission_type", String(20), nullable=False),
    Column("transaction_date", Date, nullable=False),
    Column("previous_date", Date, nullable=False),
    Column("row_count", Integer),
    Column("outcome", String(20), nullable=False),  # SAVED / FAILED
    Column("message", Unicode(1000)),
    Column("fetched_by", Unicode(200)),
    Column("fetched_at", DateTime, nullable=False),
)

SOURCES: dict[str, CrSource] = {
    "CONSUMER": CrSource(
        "CONSUMER", "dbo.usp_GetConsumerSubmissionCR", lms_cr_consumer, "TransactionDate", CONSUMER_COLUMNS
    ),
    "COMMERCIAL": CrSource(
        "COMMERCIAL", "dbo.usp_GetCommercialSubmissionCR", lms_cr_commercial, "transactiondate", COMMERCIAL_COLUMNS
    ),
}


# The old LMS views' names: the SQLite unit tests keep their stand-in tables under them (schema wip).
STAND_INS = {"CONSUMER": "VwConsumerSubmissionAccountSegment", "COMMERCIAL": "VwCommercialSubmissionCR"}


class CrFetchError(Exception):
    """The LMS procedure could not be run, or its result is not usable: the message is for the screen."""


def _month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def previous_cycle_date(as_of: date) -> date:
    """The previous reporting date for an as-of date (owner 2026-11-16): the 9th and the month-end go back to the last
    day of the previous month, the 16th to the 9th, the 23rd to the 16th. Other dates are not reporting dates."""
    if as_of.day == 16:
        return as_of.replace(day=9)
    if as_of.day == 23:
        return as_of.replace(day=16)
    if as_of.day == 9 or as_of == _month_end(as_of):
        return as_of.replace(day=1) - timedelta(days=1)
    raise CrFetchError(
        f"{as_of:%d-%m-%Y} is not a reporting date: the LMS figures can be fetched for the 9th, 16th, 23rd or the "
        "last day of a month"
    )


def _call_sql(engine: Engine, procedure: str) -> str:
    if engine.dialect.name != "mssql":
        raise ValueError(f"LMS procedures run on SQL Server only, not {engine.dialect.name}")
    # NOCOUNT: a procedure that fills temp tables first would otherwise hand back row counts before its rows
    return f"SET NOCOUNT ON; EXEC {procedure} @TransactionDate = :t, @PreviousTransactionDate = :p, @CloseDate = :c"


def run_procedure(engine: Engine, source: CrSource, as_of: date, previous: date) -> list[dict[str, Any]]:
    """The procedure's rows, as {column name: value}."""
    if engine.dialect.name == "sqlite":  # unit tests: the stand-in table in schema wip holds the rows
        stand_in = STAND_INS[source.submission_type]
        sql = f"SELECT * FROM wip.{stand_in} WHERE {source.date_column} = :t"  # noqa: S608  (fixed names)
        params: dict[str, Any] = {"t": as_of}
    else:
        sql, params = _call_sql(engine, source.procedure), {"t": as_of, "p": previous, "c": previous}
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


def _match(rows: list[dict[str, Any]], source: CrSource, *, strict: bool = True) -> list[dict[str, Any]]:
    """The rows in the table's columns, matched by name ignoring case. A missing column (other than SelectionReason)
    stops the fetch: the views would read nothing for it (`strict`; the SQLite stand-ins have only a few columns)."""
    if not rows:
        return []
    names = {k.lower(): k for k in rows[0]}
    missing = [c for c in source.columns if c.lower() not in names and c != "SelectionReason"]
    if missing and strict:
        raise CrFetchError(
            f"{source.procedure} did not return the column(s) {', '.join(missing)}: the bureau file needs them "
            "(ask the LMS team, or tell us the new names)"
        )
    picked = {c: names.get(c.lower()) for c in source.columns}
    return [{c: (r[k] if k is not None else None) for c, k in picked.items()} for r in rows]


def _as_date(value: Any) -> date | None:
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class FetchResult:
    submission_type: str
    transaction_date: date
    previous_date: date
    rows: int


def fetch_cr(
    source_engine: Engine,
    app_engine: Engine,
    submission_type: str,
    as_of: date,
    *,
    by: str = "",
    clock: Any = datetime.now,
) -> FetchResult | None:
    """Run the type's LMS procedure for `as_of` on `source_engine` and save its rows there, beside the source views
    (replacing the date's earlier set); the attempt is logged in `app_engine`. On Azure both are the same database.
    None for a type without a procedure (MFI, NeSL). Raises CrFetchError with a message for the screen."""
    source = SOURCES.get(str(submission_type).upper())
    if source is None:
        return None
    previous = previous_cycle_date(as_of)

    def log(outcome: str, count: int | None, message: str | None) -> None:
        with app_engine.begin() as conn:
            conn.execute(
                insert(lms_cr_fetch).values(
                    submission_type=source.submission_type,
                    transaction_date=as_of,
                    previous_date=previous,
                    row_count=count,
                    outcome=outcome,
                    message=(message or "")[:1000] or None,
                    fetched_by=by[:200] or None,
                    fetched_at=clock(),
                )
            )

    try:
        strict = source_engine.dialect.name != "sqlite"
        rows = _match(run_procedure(source_engine, source, as_of, previous), source, strict=strict)
    except CrFetchError as exc:
        log("FAILED", None, str(exc))
        raise
    except Exception as exc:  # the database's own error, in its words
        message = f"{source.procedure} failed for {as_of:%d-%m-%Y}: {exc}"
        log("FAILED", None, message)
        raise CrFetchError(message) from exc
    date_col = source.table.c[source.date_column]
    for r in rows:  # the date as a date (a driver may hand a datetime, SQLite a string)
        r[source.date_column] = _as_date(r[source.date_column])
    dates = {as_of, *(r[source.date_column] for r in rows if r[source.date_column] is not None)}
    with source_engine.begin() as conn:  # where the source views read them
        conn.execute(delete(source.table).where(date_col.in_(sorted(dates))))
        if rows:
            conn.execute(insert(source.table), rows)
    log("SAVED", len(rows), None)
    return FetchResult(source.submission_type, as_of, previous, len(rows))


def _same(a: Any, b: Any) -> bool:
    """Equal as reported: numbers by value (1234.5 = 1234.50), dates whatever the driver's type, text trimmed."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (date, datetime)) or isinstance(b, (date, datetime)):
        try:
            return _as_date(a) == _as_date(b)
        except ValueError:
            return False
    try:
        from decimal import Decimal, InvalidOperation

        return Decimal(str(a)) == Decimal(str(b))
    except InvalidOperation, ValueError:
        return str(a).strip() == str(b).strip()


def compare_with_view(engine: Engine, submission_type: str, as_of: date, *, examples: int = 20) -> dict[str, Any]:
    """Before switching over (owner 2026-11-16): the procedure's rows for a date vs the old LMS view's (wip.*), column
    by column, by account (commercial: account + sanction reference). Saves nothing."""
    source = SOURCES[str(submission_type).upper()]
    previous = previous_cycle_date(as_of)
    proc = _match(run_procedure(engine, source, as_of, previous), source)
    view_sql = f"SELECT * FROM wip.{STAND_INS[source.submission_type]} WHERE {source.date_column} = :t"  # noqa: S608
    with engine.connect() as conn:
        view = _match([dict(r._mapping) for r in conn.execute(text(view_sql), {"t": as_of})], source)
    key_cols = ["AccountNumber"] + (["SanctionRefNo"] if source.submission_type == "COMMERCIAL" else [])

    def keyed(rows: list[dict[str, Any]]) -> dict[tuple[str, ...], dict[str, Any]]:
        return {tuple(str(r[c] or "").strip() for c in key_cols): r for r in rows}

    p, v = keyed(proc), keyed(view)
    compared = [c for c in source.columns if c != "SelectionReason"]
    differing: list[dict[str, Any]] = []
    columns: dict[str, int] = {}
    for key in sorted(p.keys() & v.keys()):
        diffs = {c: (v[key][c], p[key][c]) for c in compared if not _same(v[key][c], p[key][c])}
        if diffs:
            for c in diffs:
                columns[c] = columns.get(c, 0) + 1
            differing.append(
                {"key": " / ".join(key), "view_vs_procedure": {c: [str(x), str(y)] for c, (x, y) in diffs.items()}}
            )
    only_view, only_proc = sorted(v.keys() - p.keys()), sorted(p.keys() - v.keys())
    identical = not (differing or only_view or only_proc) and len(proc) == len(p) and len(view) == len(v)
    return {
        "verdict": "IDENTICAL" if identical else "DIFFERENT",
        "submission_type": source.submission_type,
        "transaction_date": as_of.isoformat(),
        "previous_date": previous.isoformat(),
        "view_rows": len(view),
        "procedure_rows": len(proc),
        "repeated_keys": {"view": len(view) - len(v), "procedure": len(proc) - len(p)},
        "only_in_view": [" / ".join(k) for k in only_view[:examples]],
        "only_in_view_count": len(only_view),
        "only_in_procedure": [" / ".join(k) for k in only_proc[:examples]],
        "only_in_procedure_count": len(only_proc),
        "differing_count": len(differing),
        "differing_columns": dict(sorted(columns.items(), key=lambda kv: -kv[1])),
        "differing_examples": differing[:examples],
    }
