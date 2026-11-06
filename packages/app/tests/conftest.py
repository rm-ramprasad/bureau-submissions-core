import sys
from pathlib import Path

import pytest

from app.bureaus import BureauRepository
from app.requests import RequestRepository
from canonical.testing import clock, engine  # noqa: F401  (fixtures)

sys.path.insert(0, str(Path(__file__).parent))  # app_helpers (prefixed: unique across packages)


@pytest.fixture
def requests_repo(engine, clock):  # noqa: F811
    return RequestRepository(engine, clock=clock)


@pytest.fixture
def bureau_repo(engine, clock):  # noqa: F811
    return BureauRepository(engine, clock=clock)


@pytest.fixture(autouse=True)
def second_client_id(engine):  # noqa: F811
    """tenant_id stays on every table (Option A, one client per installation); a second id lets the tests keep
    checking that queries are scoped by it."""
    from datetime import datetime

    from canonical.tenants import Tenant, TenantRepository

    TenantRepository(engine, clock=lambda: datetime(2026, 10, 1)).upsert(
        Tenant("nbfc_002", "Demo NBFC Two", "nbfc_002")
    )


class RecordingDispatcher:
    """Collects the rows the API dispatched (in tests the polling worker runs them)."""

    def __init__(self):
        self.dispatched: list[int] = []

    def dispatch(self, requests):
        self.dispatched += [r.request_id for r in requests]

    def dispatch_later(self, requests, delay_seconds):
        self.dispatch(requests)


class Api:
    """JSON test client for the API: get/post return (status, json)."""

    def __init__(self, client, tenant=None):
        self.client, self.tenant = client, tenant

    def get(self, path, headers=None, **query):
        if self.tenant and "tenant" not in query:
            query["tenant"] = self.tenant
        resp = self.client.get(f"/api{path}", query_string=query, headers=headers)
        return resp.status_code, resp.get_json(silent=True)

    def post(self, path, headers=None, **body):
        if self.tenant and "tenant" not in body:
            body["tenant"] = self.tenant
        resp = self.client.post(f"/api{path}", json=body, headers=headers)
        return resp.status_code, resp.get_json(silent=True)

    def raw(self, path, **query):
        return self.client.get(f"/api{path}", query_string=query)


@pytest.fixture
def dispatcher():
    return RecordingDispatcher()


@pytest.fixture
def make_api(engine, tmp_path, dispatcher):  # noqa: F811
    """make_api(store=None, tenant=None, exports=None, auth="none", entra_..., require_inputs=False) -> Api."""
    from app.api import ApiServices, create_app
    from app.api.auth import Groups, OidcConfig, identity_provider
    from app.ports.files import LocalFileStore

    def make(store=None, tenant=None, exports=None, auth="none", require_inputs=False, app_dispatcher=None, **entra):
        tid, cid = entra.get("entra_tenant_id"), entra.get("entra_client_id")
        identify = identity_provider(
            auth,
            entra.get("oidc") or (OidcConfig.entra(tid, cid) if tid and cid else None),
            Groups(entra.get("entra_user_group_id"), entra.get("entra_admin_group_id"),
                   entra.get("app_user_fallback", True)),
        )  # fmt: skip
        services = ApiServices.build(
            engine, store or LocalFileStore(tmp_path), app_dispatcher or dispatcher, exports, tenant=None,
            require_inputs=require_inputs,
        )  # fmt: skip
        app = create_app(services, identify)
        app.config.update(TESTING=True)
        return Api(app.test_client(), tenant)

    return make
