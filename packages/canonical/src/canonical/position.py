"""L2b account position (ADR 0002): the loan system's figures per account and as-of date, read through ONE view,
automn.vw_pos_account_position (migrations/azure/views/position.sql), whatever the client's source:

  A. the client's LMS views (rarfincare): history by date in their database, no data load;
  B. LMS Excel uploads into automn.stg_lms_position (migration 012): the newest LOADED upload per account, sanction
     and date wins.

POSITION_COLUMNS is the contract: the columns (and their kind) every client's view returns. The extract (PR 4) reads
positions for one date with read_positions; check_view reports a view that does not keep the contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from sqlalchemy import BigInteger, Column, Engine, ForeignKey, Index, Integer, Table, Unicode, inspect, text

from canonical.db import SCHEMA, metadata

VIEW = "vw_pos_account_position"
KEY, AMOUNT, NUMBER, CODE, DATE, ID = "key", "amount", "number", "code", "date", "id"

POSITION_COLUMNS: dict[str, str] = {
    "account_number": KEY,
    "sanction_number": KEY,  # '' when the account has one sanction
    "as_of_date": DATE,
    "current_balance": AMOUNT,
    "amount_overdue": AMOUNT,
    "days_past_due": NUMBER,
    "asset_classification": CODE,
    "asset_classification_date": DATE,
    "credit_facility_status": CODE,
    "status_date": DATE,
    "date_last_payment": DATE,
    "actual_payment_amount": AMOUNT,
    "date_closed": DATE,
    "date_reported": DATE,
    "high_credit_amount": AMOUNT,
    "credit_limit": AMOUNT,
    "cash_limit": AMOUNT,
    "drawing_power": AMOUNT,
    **{f"overdue_bucket_{i}": AMOUNT for i in range(1, 6)},
    "written_off_total": AMOUNT,
    "written_off_principal": AMOUNT,
    "settlement_amount": AMOUNT,
    "npa_contracts_amount": AMOUNT,
    "wilful_default_date": DATE,
    "ufce_amount": AMOUNT,
    "ufce_date": DATE,
    "source": CODE,  # VIEW (an LMS view) / UPLOAD (an LMS Excel upload)
    "source_load_id": ID,  # the upload (automn.input_load), UPLOAD only
    "lms_type": CODE,  # the LMS view's format (CONSUMER / COMMERCIAL); '' = an upload, for every format
}

# The upload columns: the contract's figures as text, as typed in the workbook (the view parses them).
_NOT_UPLOADED = ("account_number", "sanction_number", "as_of_date", "source", "source_load_id", "lms_type")
UPLOAD_COLUMNS = (
    "account_number",
    "sanction_number",
    "position_date",
    *(c for c in POSITION_COLUMNS if c not in _NOT_UPLOADED),
)

_PK = BigInteger().with_variant(Integer(), "sqlite")

stg_lms_position = Table(
    "stg_lms_position",
    metadata,
    Column("load_id", _PK, ForeignKey(f"{SCHEMA}.input_load.load_id"), primary_key=True, autoincrement=False),
    Column("source_row", Integer, primary_key=True, autoincrement=False),
    *(Column(c, Unicode(100)) for c in UPLOAD_COLUMNS),
    Index("ix_stg_lms_position_key", "account_number", "position_date", "load_id"),
)


def check_view(engine: Engine, view: str = VIEW) -> list[str]:
    """Differences between a position view's columns and the contract (names and order). Empty when it keeps it."""
    got = [c["name"].lower() for c in inspect(engine).get_columns(view, schema=SCHEMA)]
    want = list(POSITION_COLUMNS)
    problems = [f"{view}: missing column {c}" for c in want if c not in got]
    problems += [f"{view}: column {c} is not in the contract" for c in got if c not in want]
    if not problems and got != want:
        problems.append(f"{view}: columns are in another order than the contract")
    return problems


def read_positions(
    engine: Engine, as_of_date: date, account_numbers: Iterable[str] | None = None
) -> list[dict[str, Any]]:
    """The positions for one as-of date (optionally only these accounts), as dicts in the contract's columns."""
    cols = ", ".join(POSITION_COLUMNS)
    sql = f"SELECT {cols} FROM {SCHEMA}.{VIEW} WHERE as_of_date = :d"  # noqa: S608  (fixed names, values bound)
    params: dict[str, Any] = {"d": as_of_date}
    out: list[dict[str, Any]] = []
    with engine.connect() as conn:
        if account_numbers is None:
            return [
                dict(r._mapping) for r in conn.execute(text(sql + " ORDER BY account_number, sanction_number"), params)
            ]
        accounts = sorted(set(account_numbers))
        for i in range(0, len(accounts), 500):  # SQL Server: about 2100 parameters per statement
            part = accounts[i : i + 500]
            names = {f"a{j}": a for j, a in enumerate(part)}
            q = f"{sql} AND account_number IN ({', '.join(':' + n for n in names)}) ORDER BY account_number, sanction_number"
            out += [dict(r._mapping) for r in conn.execute(text(q), {**params, **names})]
    return out
