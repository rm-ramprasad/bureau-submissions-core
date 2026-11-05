"""Test support shared by the packages that build on canonical (sources, app): an in-memory SQLite database standing
in for Azure SQL, with the app schema, the client row, the reference data and the source views the input checks read.

Used from a package's conftest.py:  from canonical.testing import *  (fixtures: engine, clock)
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import Engine, event, func, text, update
from sqlalchemy.pool import StaticPool

import canonical.staging.tables  # noqa: F401  (registers input_load, input_issue, stg_*)
from canonical.db import make_engine, metadata
from canonical.reference import seed as seed_reference
from canonical.schema import ref_code_translation
from canonical.tenants import Tenant, TenantRepository

TENANT = Tenant("nbfc_001", "Demo NBFC One", "nbfc_001")  # the installation's client in tests

__all__ = ["TENANT", "FakeClock", "add_view_accounts", "clock", "engine", "seed_labels", "sqlite_engine"]


class FakeClock:
    def __init__(self, start: datetime = datetime(2026, 10, 21, 9, 0, 0)) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int = 1) -> None:
        self.now += timedelta(seconds=seconds)


def sqlite_engine() -> Engine:
    """In-memory SQLite with the automn schema and T-SQL shims (make_engine), plus the client's source views
    (on Azure: views in schema wip) as tables that tests fill."""
    eng = make_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng, "connect")
    def _setup(dbapi_conn, _):
        dbapi_conn.execute("ATTACH DATABASE ':memory:' AS wip")
        dbapi_conn.execute(
            "CREATE TABLE wip.VwConsumerSubmissionAccountSegment (AccountNumber TEXT, TransactionDate DATE)"
        )
        dbapi_conn.execute(
            "CREATE TABLE wip.VwCommercialSubmissionCR (AccountNumber TEXT, SanctionRefNo TEXT, transactiondate DATE)"
        )

    return eng


def add_view_accounts(engine: Engine, as_of: date, *accounts: str) -> None:
    """Accounts the (test) consumer source view has for a date."""
    with engine.begin() as conn:
        for a in accounts:
            conn.execute(
                text("INSERT INTO wip.VwConsumerSubmissionAccountSegment VALUES (:a, :d)"), {"a": a, "d": as_of}
            )


def seed_labels(engine: Engine) -> None:
    """Workbook labels in ref_code_translation, as the NBFC fills them: the canonical value in words."""
    t = ref_code_translation
    with engine.begin() as conn:
        conn.execute(update(t).values(label=func.replace(t.c.code, "_", " ")))


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = sqlite_engine()
    metadata.create_all(eng)
    TenantRepository(eng, clock=lambda: datetime(2026, 10, 1)).upsert(TENANT)
    seed_reference(eng)  # as on deploy: canonical codes + each type's translations
    seed_labels(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
