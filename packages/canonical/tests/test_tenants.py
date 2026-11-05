"""The installation's client row (tenant): one per installation, identified by tenant_id in every table."""

from dataclasses import replace

import pytest

from canonical.tenants import (
    InactiveTenantError,
    InvalidTenantError,
    Tenant,
    TenantNotFoundError,
    TenantRepository,
)
from canonical.testing import TENANT


@pytest.fixture
def repo(engine, clock):
    return TenantRepository(engine, clock=clock)


def test_the_client_is_seeded_by_the_fixture(repo) -> None:
    assert [t.tenant_id for t in repo.list()] == [TENANT.tenant_id]
    assert repo.get(TENANT.tenant_id) == TENANT


def test_upsert_inserts_and_updates(repo) -> None:
    repo.upsert(Tenant("rarfincare", "Rar Fincare", "wip", extract_strategy="insert_select"))
    repo.upsert(replace(repo.get("rarfincare"), display_name="Rar Fincare Ltd"))
    client = repo.get("rarfincare")
    assert client.display_name == "Rar Fincare Ltd" and client.extract_strategy == "insert_select"


def test_deactivated_client_is_hidden_and_blocked(repo) -> None:
    repo.set_active(TENANT.tenant_id, False)
    assert repo.list() == []
    assert len(repo.list(active_only=False)) == 1
    with pytest.raises(InactiveTenantError):
        repo.get_active(TENANT.tenant_id)


def test_missing_client(repo) -> None:
    with pytest.raises(TenantNotFoundError):
        repo.get("nbfc_999")
    with pytest.raises(TenantNotFoundError):
        repo.set_active("nbfc_999", False)


@pytest.mark.parametrize(
    "tenant_id,schema",
    [
        ("NBFC_001", "nbfc_001"),  # uppercase id
        ("1nbfc", "nbfc_001"),  # starts with digit
        ("nbfc-001", "nbfc_001"),  # hyphen
        ("n" * 31, "nbfc_001"),  # too long
        ("nbfc_001", "nbfc;DROP TABLE x"),  # schema injection attempt
        ("nbfc_001", "nbfc 001"),  # space in schema
        ("nbfc_001", "s" * 65),  # schema too long
    ],
)
def test_identifiers_are_whitelisted(tenant_id, schema):
    with pytest.raises(InvalidTenantError):
        Tenant(tenant_id, "X", schema)


def test_display_name_and_strategy_validated():
    with pytest.raises(InvalidTenantError):
        Tenant("nbfc_003", "  ", "nbfc_003")
    with pytest.raises(ValueError):
        Tenant("nbfc_003", "X", "nbfc_003", extract_strategy="copy")
