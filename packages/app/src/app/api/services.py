"""What the API needs, handed in by the shell (Azure, AWS, local): the database, the file store, the job dispatcher,
CSV exports, and the deployment's settings."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine

from app.adjustments import AdjustmentRepository
from app.bureaus import BureauRepository
from app.ports.files import Exports, FileStore
from app.ports.jobs import Dispatcher
from app.requests import RequestRepository
from canonical.snapshot import SnapshotRepository
from canonical.tenants import TenantRepository


@dataclass(frozen=True)
class ApiServices:
    engine: Engine
    store: FileStore  # generated files (Blob Storage / S3 in real use)
    dispatcher: Dispatcher  # starts the worker for PENDING rows
    exports: Exports | None  # CSV exports as short-lived links, or None: streamed by the API
    requests: RequestRepository
    tenants: TenantRepository
    bureaus: BureauRepository
    snapshots: SnapshotRepository
    adjustments: AdjustmentRepository
    tenant: str | None = None  # the one NBFC this deployment serves; None = all (tests)
    require_inputs: bool = True  # submit needs a loaded input workbook per type

    @classmethod
    def build(
        cls,
        engine: Engine,
        store: FileStore,
        dispatcher: Dispatcher,
        exports: Exports | None = None,
        *,
        tenant: str | None = None,
        require_inputs: bool = True,
    ) -> ApiServices:
        return cls(
            engine=engine,
            store=store,
            dispatcher=dispatcher,
            exports=exports,
            requests=RequestRepository(engine),
            tenants=TenantRepository(engine),
            bureaus=BureauRepository(engine),
            snapshots=SnapshotRepository(engine),
            adjustments=AdjustmentRepository(engine),
            tenant=tenant,
            require_inputs=require_inputs,
        )
