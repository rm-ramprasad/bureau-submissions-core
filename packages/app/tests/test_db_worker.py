"""One request end to end through the real pipeline on SQL Server and Postgres (marked `db`; CI job "Database"):
an upload and LMS rows -> VALIDATE through rarfincare's source views (snapshot, core validation, stored result) ->
GENERATE for one bureau (bureau Excel, TUDF, review Excel in the file store)."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from datetime import date, datetime
from itertools import count
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.bureaus import BureauRepository
from app.bureaus.seed import seed_missing
from app.enums import RequestStatus, RunMode
from app.pipeline import Services
from app.ports.files import LocalFileStore
from app.requests import RequestRepository
from app.worker.runner import Worker
from canonical.db import make_engine
from canonical.snapshot import SnapshotRepository
from canonical.staging.loads import loaded_workbook
from canonical.tenants import TenantRepository
from canonical.testing import TENANT
from canonical.testing_db import lms_column, prepare_mssql, prepare_postgres
from sources.excel import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sources" / "tests"))
from src_helpers import workbook

pytestmark = pytest.mark.db
AS_OF = date(2026, 9, 28)
_run = count(1)


@pytest.fixture(scope="module", params=["mssql", "postgres"])
def db(request) -> Iterator[Engine]:
    url = os.environ.get({"mssql": "BSC_MSSQL_URL", "postgres": "BSC_POSTGRES_URL"}[request.param])
    if not url:
        pytest.skip("database URL not set")
    engine = make_engine(url)
    (prepare_mssql if request.param == "mssql" else prepare_postgres)(engine)
    seed_missing(BureauRepository(engine), TENANT.tenant_id)
    yield engine
    engine.dispose()


def test_a_request_validates_and_generates(db: Engine, tmp_path) -> None:
    acc = f"WRK{next(_run)}-{db.dialect.name}"
    with db.begin() as conn:
        conn.execute(
            text(
                f"INSERT INTO wip.VwConsumerSubmissionAccountSegment ({lms_column(db, 'AccountNumber')}, "
                f"{lms_column(db, 'TransactionDate')}, {lms_column(db, 'TL_DateDisbursed')}, "
                f"{lms_column(db, 'TL_SanctionedAmt')}, {lms_column(db, 'TL_CurrentBalance')}, "
                f"{lms_column(db, 'TL_NoofDaysPastDue')}) VALUES (:a, :d, '15012020', 500000, 1234.50, 0)"
            ),
            {"a": acc, "d": AS_OF},
        )
    requests = RequestRepository(db)
    batch = requests.create_batch(TENANT.tenant_id, AS_OF, RunMode.VALIDATE, ["CONSUMER"], draft=True)
    row = {
        "curr_new_account_no": acc, "consumer_name": "ASHA KUMAR", "date_of_birth": "15081985",
        "gender_label": "Female", "ownership_indicator_label": "Individual", "income_tax_id_number": "ABCPK1234F",
        "telephone_no_mobile": "9876543210", "address_line_1": "12 MAIN ROAD CHENNAI",
        "state_code_1_label": "Tamil Nadu", "pin_code_1": "600001", "address_category_1_label": "Residence Address",
        "account_type": "02",
    }  # fmt: skip
    load = load_workbook(
        db, "CONSUMER", workbook("CONSUMER", {"Consumer": [row]}), tenant_id=TENANT.tenant_id,
        file_name="c.xlsx", loaded_by="ci", batch_id=batch, clock=lambda: datetime(2026, 11, 1),
    )  # fmt: skip
    assert load.status == "LOADED", load.issues
    assert loaded_workbook(db, batch, "CONSUMER") == load.load_id
    requests.submit_draft(batch)

    services = Services(db, db, SnapshotRepository(db), LocalFileStore(tmp_path))
    worker = Worker(services, requests, TenantRepository(db), BureauRepository(db), "ci-worker")
    worker.run_once()
    (validated,) = requests.list_batch(batch)
    assert validated.status is RequestStatus.SUCCEEDED, validated.error_message
    assert validated.total_records == 1

    gen = requests.create_batch(
        TENANT.tenant_id, AS_OF, RunMode.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=validated.snapshot_id
    )
    worker.run_once()
    (file_row,) = requests.list_batch(gen)
    if validated.accepted_records:
        assert file_row.status is RequestStatus.SUCCEEDED, file_row.error_message
        assert Path(file_row.output_file_path).read_bytes().startswith(b"TUDF")
        assert Path(file_row.excel_file_path).is_file() and Path(file_row.review_file_path).is_file()
    else:  # the record was rejected by a rule: no file, a clear reason
        assert file_row.status is RequestStatus.FAILED and "No accepted records" in file_row.error_message
