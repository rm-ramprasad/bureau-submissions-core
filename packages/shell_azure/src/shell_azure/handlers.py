"""Azure Functions entry logic (shell_azure.functions holds only the trigger declarations). All functions run in one
Function App (Flex Consumption) on the same code package.

  api       HTTP api/{*route} -> the Flask JSON API (WSGI)
  worker    queue message {"request_id": ...} -> run that one request (validate-jobs / generate-jobs)
  jobs      timers and the admin HTTP function -> migrate | users | views | recovery | retention

Clients and engines are created once per worker process and reused while the instance stays warm.
"""

from __future__ import annotations

import logging
import os
from functools import cache
from typing import Any

from flask import Flask
from sqlalchemy import Engine

from app.logs import configure_logging
from app.ports.files import Exports, FileStore
from app.ports.jobs import Dispatcher, request_id_from_message
from app.worker.runner import Worker
from shell_azure import admin
from shell_azure.settings import Settings

log = logging.getLogger(__name__)
BUSY_RETRY_SECONDS = 120  # a request whose snapshot another run is building is re-queued this late


@cache
def settings() -> Settings:
    s = Settings.from_env()
    configure_logging(s.log_level)
    return s


@cache
def engine() -> Engine:
    from shell_azure.sql import make_azure_engine

    # The extract holds a connection while it writes the snapshot over another one: allow a few per instance.
    return make_azure_engine(settings().database_url, pool_size=2, max_overflow=3, pool_recycle=280)


@cache
def source_engine() -> Engine:
    from shell_azure.sql import make_azure_engine

    s = settings()
    return engine() if s.source_database_url == s.database_url else make_azure_engine(s.source_database_url)


@cache
def store() -> FileStore:
    from shell_azure.storage import make_store

    return make_store(settings())


@cache
def exports() -> Exports | None:
    from shell_azure.storage import make_exports

    return make_exports(settings())


@cache
def dispatcher() -> Dispatcher:
    from shell_azure.queues import make_dispatcher

    return make_dispatcher(settings())


# ---------------------------------------------------------------- API
@cache
def flask_app() -> Flask:
    from app.api import ApiServices, create_app
    from app.api.auth import Groups, identity_provider

    s = settings()
    services = ApiServices.build(
        engine(), store(), dispatcher(), exports(), tenant=s.tenant, require_inputs=s.require_inputs
    )
    groups = Groups(s.entra_user_group_id, s.entra_admin_group_id, s.app_user_fallback)
    identify = identity_provider(s.auth, s.entra_tenant_id, s.entra_client_id, groups)
    return create_app(services, identify, s.frontend_dir)


# ---------------------------------------------------------------- workers
@cache
def _worker() -> Worker:
    from app.bureaus import BureauRepository
    from app.ops.recovery import RecoverySettings
    from app.pipeline import Services
    from app.requests import RequestRepository
    from canonical.snapshot import SnapshotRepository
    from canonical.tenants import TenantRepository

    app_engine = engine()
    services = Services(app_engine, source_engine(), SnapshotRepository(app_engine), store())
    return Worker(
        services,
        RequestRepository(app_engine),
        TenantRepository(app_engine),
        BureauRepository(app_engine),
        worker_id=os.environ.get("WEBSITE_SITE_NAME", "azure-worker"),
        recovery=RecoverySettings(run_timeout=admin.RUN_TIMEOUT, build_timeout=admin.RUN_TIMEOUT),
    )


def worker(message_body: str) -> dict[str, Any]:
    """Run one dispatched request. A malformed message raises (and ends in the poison queue). A request whose
    snapshot another run is building is put back on its queue with a delay instead of failing: a raised error would
    spend one of the message's dequeue attempts."""
    request_id = request_id_from_message(message_body)
    log.info("Running request %s", request_id)
    w = _worker()
    if w.run_request(request_id):
        return {"ok": True, "request_id": request_id}
    req = w.requests.get(request_id)
    log.info("Request %s: its snapshot is being built by another run; retrying in %ss", request_id, BUSY_RETRY_SECONDS)
    dispatcher().dispatch_later([req], BUSY_RETRY_SECONDS)
    return {"ok": True, "request_id": request_id, "requeued": True}


# ---------------------------------------------------------------- scheduled and admin jobs
def jobs(event: dict[str, Any], actor: str = "admin-function") -> dict[str, Any]:
    log.info("Job %s", (event or {}).get("job"))
    s = settings()
    return admin.run_job(
        event,
        engine(),
        tenant=s.tenant,
        dispatcher=dispatcher,
        store=store,
        retention_days=s.retention_days,
        actor=actor,
    )
