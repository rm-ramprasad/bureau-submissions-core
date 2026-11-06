"""Golden regression through the app: a validated snapshot (a golden batch with the result the reference app recorded)
-> a GENERATE request -> the worker -> the files in the file store, compared byte for byte with the golden bureau
files (the consumer TUDF converted from the bureau Excel, the commercial pipe file), for every bureau. Also the other
outputs: the bureau Excel and the review Excel exist, the request records the counts."""

from datetime import date, datetime
from pathlib import Path

import pytest
from app_helpers import GOLDEN, golden, load_snapshot

from app.bureaus import BureauProfile, BureauRepository
from app.enums import BureauCode, ReportingCycle, RunMode, SubmissionType
from app.enums import RequestStatus as S
from app.pipeline import Services
from app.ports.files import LocalFileStore
from app.validation.run import store_result
from app.worker.runner import Worker
from canonical.snapshot import SnapshotRepository
from canonical.tenants import TenantRepository

GOLDEN_FILES = GOLDEN.parent
CREATED = datetime(2026, 10, 5, 10, 30)  # the golden commercial files were made on 05-10-2026 (HD field 4)
PLACEHOLDERS = {  # the reference app's placeholder profiles for nbfc_001, as in bureau_core's golden test
    BureauCode.EXPERIAN: ("Experian", "EXP", "CONS"),
    BureauCode.CIBIL: ("TransUnion CIBIL", "CIB", "TUDF"),
    BureauCode.CRIF_HIGHMARK: ("CRIF High Mark", "CRF", "CONS"),
    BureauCode.EQUIFAX: ("Equifax", "EQF", "CONS"),
}


@pytest.fixture
def worker(engine, tmp_path, monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return CREATED

    monkeypatch.setattr("app.pipeline.outputs.datetime", FixedClock)
    bureaus = BureauRepository(engine)
    for code, (name, prefix, tag) in PLACEHOLDERS.items():
        bureaus.upsert(
            BureauProfile("nbfc_001", code, SubmissionType.CONSUMER, name, f"{prefix}0000001", "NBFC 001", tag,
                          "{member_id}.txt")
        )  # fmt: skip
        bureaus.upsert(
            BureauProfile("nbfc_001", code, SubmissionType.COMMERCIAL, f"{code.value} commercial", "CM0010001", None,
                          "", "Commercial-ME-{as_of:%d%m%Y}-{member_id}-{bureau}.txt")  # one name per bureau
        )  # fmt: skip
    services = Services(engine, engine, SnapshotRepository(engine), LocalFileStore(tmp_path / "files"))
    from app.requests import RequestRepository

    return Worker(services, RequestRepository(engine), TenantRepository(engine), bureaus, "test-worker")


def validated_snapshot(engine, name):
    """The golden batch (account history included: it is in the files) with the result the reference recorded."""
    batch, result = golden(name)
    sid = load_snapshot(engine, batch, batch_id=f"golden-{name}")
    with engine.begin() as conn:
        store_result(conn, sid, batch.submission_type, result)
    SnapshotRepository(engine).mark_validated(sid)
    return sid, batch.submission_type


@pytest.mark.parametrize(
    ("name", "golden_file"),
    [
        ("consumer_nbfc_001", "consumer_nbfc_001_{bureau}_via_excel.txt"),
        ("commercial_nbfc_001", "commercial_nbfc_001_{bureau}.txt"),
    ],
)
def test_the_worker_writes_the_golden_files(engine, worker, name, golden_file):
    sid, submission_type = validated_snapshot(engine, name)
    batch = worker.requests.create_batch(
        "nbfc_001", date(2026, 9, 30), RunMode.GENERATE, [submission_type], list(BureauCode),
        reporting_cycle=ReportingCycle.MONTH_END, snapshot_id=sid,
    )  # fmt: skip
    assert worker.run_once() == len(BureauCode)
    for row in worker.requests.list_batch(batch):
        assert row.status is S.SUCCEEDED, row.error_message
        expected = (GOLDEN_FILES / golden_file.format(bureau=row.bureau_code.value.lower())).read_bytes()
        assert Path(row.output_file_path).read_bytes() == expected, f"{row.bureau_code}: differs from the golden file"
        assert Path(row.review_file_path).is_file() and row.accepted_records > 0
        if submission_type == "CONSUMER":  # three outputs: bureau Excel, TUDF, review Excel
            assert Path(row.excel_file_path).is_file()
        assert row.total_records == row.accepted_records + row.rejected_records


def test_a_validate_request_runs_through_the_views(engine, worker):
    """No source views in this database: the row fails with a clear message (the views path is tested in sources and
    in the Database job)."""
    batch = worker.requests.create_batch("nbfc_001", date(2026, 9, 30), RunMode.VALIDATE, ["CONSUMER"])
    worker.run_once()
    (row,) = worker.requests.list_batch(batch)
    assert row.status is S.FAILED and "Snapshot build failed" in row.error_message
    assert "vw_consumer_account" in row.error_message


def test_generate_refuses_a_snapshot_that_is_not_validated(engine, worker):
    batch_data, _ = golden("commercial_nbfc_001")
    sid = load_snapshot(engine, batch_data, batch_id="not-validated")
    batch = worker.requests.create_batch(
        "nbfc_001", date(2026, 9, 30), RunMode.GENERATE, ["COMMERCIAL"], [BureauCode.CIBIL], snapshot_id=sid
    )
    worker.run_once()
    (row,) = worker.requests.list_batch(batch)
    assert row.status is S.FAILED and "not VALIDATED" in row.error_message
