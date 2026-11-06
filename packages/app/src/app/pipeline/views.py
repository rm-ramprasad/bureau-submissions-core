"""The standard pipeline: the type's source views -> canonical snapshot (with the request's exclusions and
overrides) -> the client's rule set, validated by the core -> the type's files per bureau. A type only supplies its
file writer (app.types.<type>.file)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from app.adjustments.extract import SqlAdjustments
from app.bureaus import BureauProfile
from app.pipeline.base import Services
from app.requests import RequestResult, SubmissionRequest
from app.validation.run import ValidationSummary, validate_snapshot
from app.validation.settings import load_rule_set
from bureau_core.formats.tudf.engine import FileStats
from canonical.snapshot import Snapshot
from canonical.staging.loads import loaded_workbook
from canonical.tenants import Tenant
from sources.extract import run_extract
from sources.views import compile_views

# (services, request, snapshot, bureau, created) -> (file content, stats)
FileWriter = Callable[[Services, SubmissionRequest, Snapshot, BureauProfile, datetime], tuple[bytes, FileStats]]


class ViewsPipeline:
    def __init__(self, write_file: FileWriter) -> None:
        self.write_file = write_file

    def build_snapshot(
        self, services: Services, tenant: Tenant, request: SubmissionRequest, owner: str
    ) -> tuple[int, ValidationSummary]:
        extract = run_extract(
            tenant=tenant,
            compiled=compile_views(services.app_engine, request.submission_type),
            submission_type=request.submission_type,
            as_of_date=request.as_of_date,
            app_engine=services.app_engine,
            source_engine=services.source_engine,
            snapshots=services.snapshots,
            owner=owner,
            batch_id=request.batch_id,
            load_id=loaded_workbook(services.app_engine, request.batch_id, request.submission_type),
            adjustments=SqlAdjustments(services.app_engine),
        )
        rules = load_rule_set(services.app_engine, tenant.tenant_id, request.submission_type)
        result = validate_snapshot(services.app_engine, services.snapshots, extract.snapshot_id, rules)
        return extract.snapshot_id, result

    def generate_file(
        self, services: Services, tenant: Tenant, request: SubmissionRequest, snapshot: Snapshot, bureau: BureauProfile
    ) -> RequestResult:
        from bureau_core.formats.excel import excel_layout

        if excel_layout(str(request.submission_type)) is not None:  # bureau Excel -> TUDF -> review Excel
            from app.pipeline.outputs import generate_outputs

            return generate_outputs(services, tenant, request, snapshot, bureau)
        from app.pipeline.outputs import generate_file_with_review  # the file + a review Excel

        return generate_file_with_review(services, tenant, request, snapshot, bureau, self.write_file)
