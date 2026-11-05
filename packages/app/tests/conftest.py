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
