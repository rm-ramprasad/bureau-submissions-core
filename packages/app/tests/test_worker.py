"""The worker's own rules (snapshot sharing and reuse, busy snapshots, retries, failures) and the operations jobs
(recovery, retention). Snapshots are built by a test pipeline from the golden batches; file generation is the real
one (app.pipeline.views)."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from app_helpers import golden, load_snapshot
from sqlalchemy import func, select

from app import audit
from app.bureaus import BureauRepository
from app.bureaus.seed import seed_missing
from app.enums import RequestStatus as S
from app.enums import RunMode as M
from app.enums import SnapshotStatus
from app.ops.recovery import RecoverySettings, recover
from app.ops.retention import purge
from app.pipeline import Services, enabled_types
from app.pipeline.views import ViewsPipeline
from app.ports.files import LocalFileStore
from app.requests import RequestRepository
from app.validation.run import validate_snapshot
from app.validation.settings import load_rule_set
from app.validation.tables import record_outcome, validation_finding, validation_summary
from app.worker.runner import Worker
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository
from canonical.tenants import Tenant, TenantRepository

AS_OF = date(2026, 9, 30)  # the golden batches' date


class GoldenPipeline:
    """Builds the request's snapshot from a golden batch (instead of the views); generates files as the app does."""

    def __init__(self) -> None:
        self.fail_build = False
        self.builds = 0
        self.files = ViewsPipeline(write_file=None)

    def build_snapshot(self, services, tenant, request, owner, **_):
        self.builds += 1
        batch, _ = golden(f"{str(request.submission_type).lower()}_nbfc_001")
        sid = load_snapshot(
            services.app_engine, batch, skip=("account_history",), batch_id=request.batch_id, keep_row_ids=False
        )
        if self.fail_build:
            services.snapshots.mark_failed(sid, "source view broken")
            raise RuntimeError("source view broken")
        rules = load_rule_set(services.app_engine, tenant.tenant_id, request.submission_type)
        return sid, validate_snapshot(services.app_engine, services.snapshots, sid, rules)

    def generate_file(self, services, tenant, request, snapshot, bureau):
        if request.submission_type == "CONSUMER":
            return self.files.generate_file(services, tenant, request, snapshot, bureau)
        from app.pipeline import get_pipeline

        return get_pipeline("COMMERCIAL").generate_file(services, tenant, request, snapshot, bureau)


@pytest.fixture
def pipeline(monkeypatch):
    p = GoldenPipeline()
    monkeypatch.setattr("app.worker.runner.get_pipeline", lambda t: p if str(t) in ("CONSUMER", "COMMERCIAL") else None)
    return p


@pytest.fixture
def worker(engine, tmp_path, pipeline):
    bureaus = BureauRepository(engine)
    seed_missing(bureaus, "nbfc_001")
    services = Services(engine, engine, SnapshotRepository(engine), LocalFileStore(tmp_path))
    return Worker(services, RequestRepository(engine), TenantRepository(engine), bureaus, "test-worker")


def count(engine, table, **where) -> int:
    with engine.connect() as conn:
        q = select(func.count()).select_from(table).where(*(table.c[k] == v for k, v in where.items()))
        return conn.execute(q).scalar()


def validated(worker, submission_type="CONSUMER"):
    batch = worker.requests.create_batch("nbfc_001", AS_OF, M.VALIDATE, [submission_type])
    worker.run_once()
    (row,) = worker.requests.list_batch(batch)
    assert row.status is S.SUCCEEDED, row.error_message
    return row


def test_consumer_and_commercial_are_enabled():
    assert [str(t) for t in enabled_types()] == ["CONSUMER", "COMMERCIAL"]


def test_validate_then_generate_reuses_the_snapshot(worker, pipeline, engine):
    row = validated(worker)
    assert row.total_records == row.accepted_records + row.rejected_records > 0
    assert row.excluded_accounts == 0 and row.overridden_accounts == 0
    gen = worker.requests.create_batch(
        "nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL", "EXPERIAN"], snapshot_id=row.snapshot_id
    )
    assert worker.run_once() == 2
    files = worker.requests.list_batch(gen)
    assert {f.status for f in files} == {S.SUCCEEDED} and {f.snapshot_id for f in files} == {row.snapshot_id}
    assert all(Path(f.output_file_path).is_file() and Path(f.excel_file_path).is_file() for f in files)
    assert pipeline.builds == 1  # generation reused the validated snapshot


def test_rows_of_one_batch_share_one_snapshot(worker, pipeline, engine):
    batch = worker.requests.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER", "COMMERCIAL"])
    worker.run_once()
    rows = worker.requests.list_batch(batch)
    assert {r.status for r in rows} == {S.SUCCEEDED}, [r.error_message for r in rows]
    assert len({r.snapshot_id for r in rows}) == 2
    assert pipeline.builds == 2


def test_a_reused_snapshot_must_match_the_request(worker):
    row = validated(worker, "COMMERCIAL")
    other_date = worker.requests.create_batch(
        "nbfc_001", date(2026, 9, 29), M.GENERATE, ["COMMERCIAL"], ["CIBIL"], snapshot_id=row.snapshot_id
    )
    worker.run_once()
    (bad,) = worker.requests.list_batch(other_date)
    assert bad.status is S.FAILED and "not to this request" in bad.error_message


def test_a_busy_snapshot_releases_the_row(worker, engine):
    batch = worker.requests.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    SnapshotRepository(engine).create("nbfc_001", "CONSUMER", AS_OF, batch, "v", "another-worker")  # being built
    assert worker.run_once() == 0
    (row,) = worker.requests.list_batch(batch)
    assert row.status is S.PENDING


def test_an_inactive_client_or_an_unsupported_type_fails_the_row(worker, pipeline, monkeypatch, engine):
    TenantRepository(engine).set_active("nbfc_001", False)
    batch = worker.requests.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    worker.run_once()
    (row,) = worker.requests.list_batch(batch)
    assert row.status is S.FAILED and "inactive" in row.error_message
    TenantRepository(engine).set_active("nbfc_001", True)
    monkeypatch.setattr("app.worker.runner.get_pipeline", lambda t: None)
    worker.requests.requeue(row.request_id)
    worker.run_once()
    assert "not supported yet" in worker.requests.get(row.request_id).error_message


def test_a_missing_bureau_profile_fails_generate(worker, engine):
    row = validated(worker)
    TenantRepository(engine).upsert(Tenant("nbfc_002", "Two", "nbfc_002"))
    gen = worker.requests.create_batch(
        "nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=row.snapshot_id
    )
    from app.bureaus.tables import bureau_profile

    with engine.begin() as conn:
        conn.execute(bureau_profile.delete())
    worker.run_once()
    (g,) = worker.requests.list_batch(gen)
    assert g.status is S.FAILED and "No bureau profile" in g.error_message


def test_a_retry_rebuilds_a_failed_snapshot(worker, pipeline, engine):
    pipeline.fail_build = True
    batch = worker.requests.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["COMMERCIAL"])
    worker.run_once()
    (row,) = worker.requests.list_batch(batch)
    assert row.status is S.FAILED and "source view broken" in row.error_message
    pipeline.fail_build = False  # fixed
    worker.requests.requeue(row.request_id)
    worker.run_once()
    retried = worker.requests.get(row.request_id)
    assert retried.status is S.SUCCEEDED and retried.attempt_count == 2, retried.error_message
    assert SnapshotRepository(engine).get(retried.snapshot_id).status is SnapshotStatus.VALIDATED


def test_run_request_handles_one_dispatched_row(worker):
    batch = worker.requests.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["COMMERCIAL"])
    (row,) = worker.requests.list_batch(batch)
    assert worker.run_request(row.request_id) is True
    assert worker.requests.get(row.request_id).status is S.SUCCEEDED
    assert worker.run_request(row.request_id) is True  # a duplicate delivery: nothing to do


# ---------------------------------------------------------------- recovery
T0 = datetime(2026, 10, 21, 9, 0, 0)  # the FakeClock start


def test_stuck_running_rows_are_released_then_failed(engine, requests_repo):
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    req = requests_repo.claim_next("dead-worker")  # started at T0, attempt 1
    later = lambda: T0 + timedelta(hours=3)  # noqa: E731
    settings = RecoverySettings(run_timeout=timedelta(hours=2), max_attempts=2)
    result = recover(engine, requests_repo, SnapshotRepository(engine), settings, clock=later)
    assert result.released == [req.request_id] and requests_repo.get(req.request_id).status is S.PENDING
    requests_repo.claim_next("dead-worker-2")  # attempt 2, also dies
    result = recover(engine, requests_repo, SnapshotRepository(engine), settings, clock=later)
    failed = requests_repo.get(req.request_id)
    assert result.failed == [req.request_id] and failed.status is S.FAILED and "Timed out" in failed.error_message
    assert [e.action for e in audit.recent(engine)] == ["REQUEST_RECOVERED", "REQUEST_RECOVERED"]


def test_recent_running_rows_are_left_alone(engine, requests_repo):
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, ["CONSUMER"])
    req = requests_repo.claim_next("busy-worker")
    result = recover(engine, requests_repo, SnapshotRepository(engine), clock=lambda: T0 + timedelta(minutes=30))
    assert result.released == [] and requests_repo.get(req.request_id).status is S.RUNNING


def test_an_abandoned_snapshot_build_is_failed_and_cleaned(engine, clock, requests_repo):
    snapshots = SnapshotRepository(engine, clock=clock)
    sid = snapshots.create("nbfc_001", "CONSUMER", AS_OF, "b1", "v", "dead-worker")
    with engine.begin() as conn:
        conn.execute(ENTITY_TABLES["party"].insert(), [dict(snapshot_id=sid, party_key="C1")])
    result = recover(engine, requests_repo, snapshots, clock=lambda: T0 + timedelta(hours=3))
    assert result.snapshots_failed == [sid]
    snap = snapshots.get(sid)
    assert snap.status is SnapshotStatus.FAILED and "abandoned" in snap.error_message
    assert count(engine, ENTITY_TABLES["party"], snapshot_id=sid) == 0


# ---------------------------------------------------------------- retention
def test_purge_removes_pii_and_files_and_keeps_the_summary(worker, engine, tmp_path):
    row = validated(worker)
    gen = worker.requests.create_batch(
        "nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=row.snapshot_id
    )
    worker.run_once()
    (g,) = worker.requests.list_batch(gen)
    sid, files = g.snapshot_id, [Path(g.output_file_path), Path(g.excel_file_path), Path(g.review_file_path)]
    assert g.status is S.SUCCEEDED and all(f.exists() for f in files)
    summary_rows = count(engine, validation_summary, snapshot_id=sid)

    future = lambda: datetime.now() + timedelta(days=200)  # noqa: E731
    preview = purge(engine, timedelta(days=90), LocalFileStore(tmp_path), dry_run=True, clock=future)
    assert preview.snapshots == [sid] and len(preview.files) == 3 and all(f.exists() for f in files)

    purge(engine, timedelta(days=90), LocalFileStore(tmp_path), clock=future)
    assert SnapshotRepository(engine).get(sid).status is SnapshotStatus.PURGED
    assert count(engine, ENTITY_TABLES["party"], snapshot_id=sid) == 0
    assert (
        count(engine, validation_finding, snapshot_id=sid) == 0 and count(engine, record_outcome, snapshot_id=sid) == 0
    )
    assert count(engine, validation_summary, snapshot_id=sid) == summary_rows > 0
    assert not any(f.exists() for f in files) and worker.requests.get(g.request_id).output_file_path is None
    assert audit.recent(engine)[0].action == "RETENTION_PURGE"
    assert purge(engine, timedelta(days=90), LocalFileStore(tmp_path)).snapshots == []  # recent data kept


def test_generate_from_a_purged_snapshot_is_refused(worker, engine, tmp_path):
    row = validated(worker)
    purge(engine, timedelta(days=90), LocalFileStore(tmp_path), clock=lambda: datetime.now() + timedelta(days=200))
    g = worker.requests.create_batch(
        "nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=row.snapshot_id
    )
    worker.run_once()
    (bad,) = worker.requests.list_batch(g)
    assert bad.status is S.FAILED and "PURGED" in bad.error_message
