"""Pipeline contract per submission type, and the shared services a pipeline uses.

A pipeline knows how to turn the client's source data into a validated snapshot and how to write a bureau file
from it. The worker only orchestrates; it never knows about views, rules or file layouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import Engine

from app.bureaus import BureauProfile
from app.ports.files import FileStore
from app.requests import RequestResult, SubmissionRequest
from app.validation.run import ValidationSummary as ValidationResult
from canonical.snapshot import Snapshot, SnapshotRepository
from canonical.tenants import Tenant


@dataclass(frozen=True)
class Services:
    app_engine: Engine
    source_engine: Engine
    snapshots: SnapshotRepository
    store: FileStore  # where generated files are written (Blob storage on Azure)


class FileGenerationUnavailable(Exception):
    """The submission type can validate but cannot write files yet."""


class SubmissionPipeline(Protocol):
    def build_snapshot(
        self, services: Services, tenant: Tenant, request: SubmissionRequest, owner: str
    ) -> tuple[int, ValidationResult]:
        """Extract and validate. Must create the snapshot keyed by the request's batch (SnapshotExistsError
        if another worker already did) and leave it VALIDATED."""

    def generate_file(
        self, services: Services, tenant: Tenant, request: SubmissionRequest, snapshot: Snapshot, bureau: BureauProfile
    ) -> RequestResult:
        """Write one bureau file from a VALIDATED snapshot."""
