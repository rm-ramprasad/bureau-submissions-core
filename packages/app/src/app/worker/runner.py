"""The worker: claims submission_request rows and runs them through their pipeline. Locally it polls; on a cloud a
queue trigger hands it one row (run_request). Its command line comes with the settings (step 1.3e)."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass, field, replace

from app.adjustments import applied_summary
from app.bureaus import BureauRepository
from app.enums import RunMode, SnapshotStatus
from app.logs import correlation
from app.ops.recovery import RecoverySettings, recover
from app.pipeline import Services, get_pipeline
from app.pipeline.outputs import OutputsNotMatched
from app.requests import RequestRepository, RequestResult, SubmissionRequest
from app.validation.run import ValidationSummary as ValidationResult
from app.validation.run import snapshot_result
from canonical.snapshot import Snapshot, SnapshotExistsError
from canonical.tenants import Tenant, TenantRepository

log = logging.getLogger(__name__)


class JobFailed(Exception):
    """Expected, user-facing failure of one row (recorded on the row, no stack trace). `review_file_path`: a review
    Excel kept for the failed row (a generation whose reconciliation did not match)."""

    def __init__(self, message: str, review_file_path: str | None = None):
        super().__init__(message)
        self.review_file_path = review_file_path


class SnapshotBusy(Exception):
    """The batch's snapshot is being built by another worker; retry the row later."""


@dataclass
class Worker:
    services: Services
    requests: RequestRepository
    tenants: TenantRepository
    bureaus: BureauRepository
    worker_id: str = f"{socket.gethostname()}-worker"
    recovery: RecoverySettings = field(default_factory=RecoverySettings)

    # ------------------------------------------------------------------ loop
    def run_once(self) -> int:
        """Recover stuck work, then process pending rows until none are left (or one has to wait)."""
        recover(self.services.app_engine, self.requests, self.services.snapshots, self.recovery)
        processed = 0
        while (req := self.requests.claim_next(self.worker_id)) is not None:
            if not self.process(req):
                break  # released: let the snapshot builder finish first
            processed += 1
        return processed

    def run_request(self, request_id: int) -> bool:
        """Run one dispatched row (queue-triggered worker). Returns False if it was released to retry later (its snapshot
        is being built by another invocation); True if it finished, or was not PENDING (nothing to do)."""
        req = self.requests.claim(request_id, self.worker_id)
        if req is None:
            log.info("Request %s is not pending (duplicate delivery or already handled): skipped", request_id)
            return True
        return self.process(req)

    def run_forever(self, poll_seconds: float = 5.0) -> None:
        log.info("Worker %s polling every %ss", self.worker_id, poll_seconds)
        while True:
            try:
                self.run_once()
            except Exception:
                log.exception("Worker loop error")
            time.sleep(poll_seconds)

    # ------------------------------------------------------------------ one row
    def process(self, req: SubmissionRequest) -> bool:
        """Run one claimed row. Returns False if it was released to retry later."""
        with correlation(f"req-{req.request_id}"):  # every log line of this row carries its id
            return self._process(req)

    def _process(self, req: SubmissionRequest) -> bool:
        log.info(
            "Request %s: %s %s %s %s",
            req.request_id,
            req.tenant_id,
            req.run_mode,
            req.submission_type,
            req.bureau_code or "",
        )
        try:
            tenant = self._tenant(req)
            snapshot, result = self._snapshot(req, tenant)
            if req.run_mode is RunMode.VALIDATE:
                outcome = RequestResult(
                    total_records=result.records, accepted_records=result.accepted, rejected_records=result.rejected
                )
            else:
                outcome = self._generate(req, tenant, snapshot, result)
            adjusted = applied_summary(self.services.app_engine, snapshot.snapshot_id)
            outcome = replace(
                outcome, excluded_accounts=adjusted.excluded_accounts, overridden_accounts=adjusted.overridden_accounts
            )
            self.requests.mark_succeeded(req.request_id, outcome)
            return True
        except SnapshotBusy as exc:
            log.info("Request %s released: %s", req.request_id, exc)
            self.requests.release(req.request_id)
            return False
        except JobFailed as exc:
            self.requests.mark_failed(req.request_id, str(exc), review_file_path=exc.review_file_path)
        except Exception as exc:
            log.exception("Request %s failed", req.request_id)
            self.requests.mark_failed(req.request_id, f"{type(exc).__name__}: {exc}")
        return True

    def _tenant(self, req: SubmissionRequest) -> Tenant:
        try:
            return self.tenants.get_active(req.tenant_id)
        except Exception as exc:
            raise JobFailed(str(exc)) from exc

    def _snapshot(self, req: SubmissionRequest, tenant: Tenant) -> tuple[Snapshot, ValidationResult]:
        snapshots = self.services.snapshots
        if req.snapshot_id is not None:  # "generate from this validation"
            snap = snapshots.get(req.snapshot_id)
            if (snap.tenant_id, snap.submission_type, snap.as_of_date) != (
                req.tenant_id,
                req.submission_type,
                req.as_of_date,
            ):
                raise JobFailed(
                    f"Snapshot {snap.snapshot_id} belongs to {snap.tenant_id} {snap.submission_type} "
                    f"{snap.as_of_date}, not to this request"
                )
            if snap.status is not SnapshotStatus.VALIDATED:
                raise JobFailed(f"Snapshot {snap.snapshot_id} is {snap.status}, not VALIDATED")
            return snap, snapshot_result(self.services.app_engine, snap.snapshot_id)

        pipeline = get_pipeline(req.submission_type)
        if pipeline is None:
            raise JobFailed(f"{req.submission_type} submissions are not supported yet")
        try:
            snapshot_id, result = pipeline.build_snapshot(self.services, tenant, req, self.worker_id)
        except SnapshotExistsError as exc:  # shared with the other rows of this batch
            snap = snapshots.get(exc.snapshot_id)
            if snap.status is SnapshotStatus.FAILED and req.attempt_count > 1:
                # a retried row: the failure belongs to an earlier attempt, so rebuild from scratch
                log.info("Request %s: rebuilding failed snapshot %s", req.request_id, snap.snapshot_id)
                snapshots.delete(snap.snapshot_id)
                return self._snapshot(req, tenant)
            if snap.status is SnapshotStatus.FAILED:
                raise JobFailed(f"Snapshot {snap.snapshot_id} failed: {snap.error_message}") from None
            if snap.status is not SnapshotStatus.VALIDATED:
                raise SnapshotBusy(f"snapshot {snap.snapshot_id} is {snap.status}") from None
            self.requests.assign_snapshot(req.request_id, snap.snapshot_id)
            return snap, snapshot_result(self.services.app_engine, snap.snapshot_id)
        except Exception as exc:
            raise JobFailed(f"Snapshot build failed: {exc}") from exc
        self.requests.assign_snapshot(req.request_id, snapshot_id)
        return snapshots.get(snapshot_id), result

    def _generate(
        self, req: SubmissionRequest, tenant: Tenant, snapshot: Snapshot, result: ValidationResult
    ) -> RequestResult:
        if result.file_rejected:
            raise JobFailed("Validation found a REJECT_FILE problem. No file can be generated from this snapshot")
        pipeline = get_pipeline(req.submission_type)
        if pipeline is None:
            raise JobFailed(f"{req.submission_type} submissions are not supported yet")
        try:
            bureau = self.bureaus.get(req.tenant_id, req.bureau_code, req.submission_type)
        except Exception as exc:
            raise JobFailed(str(exc)) from exc
        try:
            return pipeline.generate_file(self.services, tenant, req, snapshot, bureau)
        except OutputsNotMatched as exc:
            raise JobFailed(str(exc), review_file_path=exc.review_file_path) from exc
        except Exception as exc:
            raise JobFailed(str(exc)) from exc
