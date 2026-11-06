"""Exclusions and overrides through rarfincare's real source views on SQL Server and Postgres (marked `db`; CI job
"Database"): an upload and LMS rows, a request with one excluded loan and one override, then the snapshot."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from itertools import count
from pathlib import Path

import pytest
from sqlalchemy import Engine, select, text

from app.adjustments import AdjustmentRepository, AdjustmentScope, applied_summary
from app.adjustments.extract import SqlAdjustments
from app.enums import AdjustmentKind, ExtractStrategy, OverrideField, RunMode
from app.requests import RequestRepository
from canonical.db import make_engine
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository
from canonical.testing import TENANT
from canonical.testing_db import lms_column, prepare_mssql, prepare_postgres
from sources.excel import load_workbook
from sources.extract import run_extract
from sources.views import compile_views

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sources" / "tests"))
from src_helpers import workbook

pytestmark = pytest.mark.db
AS_OF = date(2026, 9, 29)
_run = count(1)


@pytest.fixture(scope="module", params=["mssql", "postgres"])
def db(request) -> Iterator[Engine]:
    url = os.environ.get({"mssql": "BSC_MSSQL_URL", "postgres": "BSC_POSTGRES_URL"}[request.param])
    if not url:
        pytest.skip("database URL not set")
    engine = make_engine(url)
    (prepare_mssql if request.param == "mssql" else prepare_postgres)(engine)
    yield engine
    engine.dispose()


def test_an_exclusion_and_an_override_reach_the_snapshot(db: Engine) -> None:
    run = next(_run)
    a1, a2 = f"ADJ{run}-1", f"ADJ{run}-2"
    with db.begin() as conn:
        for acc in (a1, a2):
            conn.execute(
                text(
                    f"INSERT INTO wip.VwConsumerSubmissionAccountSegment ({lms_column(db, 'AccountNumber')}, "
                    f"{lms_column(db, 'TransactionDate')}, {lms_column(db, 'TL_NoofDaysPastDue')}) VALUES (:a, :d, 45)"
                ),
                {"a": acc, "d": AS_OF},
            )
    rows = [
        {"curr_new_account_no": acc, "consumer_name": "ASHA KUMAR", "gender_label": "Female",
         "ownership_indicator_label": "Individual", "account_type": "02"}
        for acc in (a1, a2)
    ]  # fmt: skip
    load = load_workbook(
        db, "CONSUMER", workbook("CONSUMER", {"Consumer": rows}), tenant_id=TENANT.tenant_id,
        file_name="c.xlsx", loaded_by="ci", clock=lambda: datetime(2026, 11, 1),
    )  # fmt: skip
    assert load.status == "LOADED", load.issues

    requests = RequestRepository(db)
    batch = requests.create_batch(TENANT.tenant_id, AS_OF, RunMode.VALIDATE, ["CONSUMER"], draft=True)
    scope = AdjustmentScope.of(requests.list_batch(batch)[0])
    adjustments = AdjustmentRepository(db)
    adjustments.add_exclusions(scope, AdjustmentKind.EXCLUDE_ACCOUNT, [a2], "ci", "ci")
    adjustments.add_override(scope, a1, OverrideField.DAYS_PAST_DUE, Decimal(0), "ci", "ci")

    sid = run_extract(
        tenant=replace(TENANT, extract_strategy=ExtractStrategy.INSERT_SELECT),
        compiled=compile_views(db, "CONSUMER"),
        submission_type="CONSUMER",
        as_of_date=AS_OF,
        app_engine=db,
        source_engine=db,
        snapshots=SnapshotRepository(db),
        owner="ci",
        batch_id=batch,
        load_id=load.load_id,
        adjustments=SqlAdjustments(db),
    ).snapshot_id
    t = ENTITY_TABLES["account"]
    with db.connect() as conn:
        got = conn.execute(select(t.c.account_key, t.c.days_past_due).where(t.c.snapshot_id == sid)).all()
    assert [tuple(r) for r in got] == [(a1, 0)]  # a2 excluded; a1's 45 days overridden to 0
    summary = applied_summary(db, sid)
    assert (summary.excluded_accounts, summary.overridden_accounts, summary.not_found) == (1, 1, 0)
