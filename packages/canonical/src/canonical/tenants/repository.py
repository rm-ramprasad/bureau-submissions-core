"""Read and maintain tenants."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine, select, true, update

from canonical.db import utcnow
from canonical.tenants.models import Tenant
from canonical.tenants.tables import tenant as t


class TenantNotFoundError(LookupError):
    pass


class InactiveTenantError(Exception):
    pass


class TenantRepository:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] = utcnow):
        self.engine = engine
        self.clock = clock

    def get(self, tenant_id: str) -> Tenant:
        with self.engine.connect() as conn:
            row = conn.execute(select(t).where(t.c.tenant_id == tenant_id)).first()
        if row is None:
            raise TenantNotFoundError(f"Tenant {tenant_id!r} not found")
        return Tenant.from_row(row)

    def get_active(self, tenant_id: str) -> Tenant:
        """For callers about to start work for a tenant (web form, worker)."""
        tenant = self.get(tenant_id)
        if not tenant.is_active:
            raise InactiveTenantError(f"Tenant {tenant_id!r} is inactive")
        return tenant

    def list(self, active_only: bool = True) -> list[Tenant]:
        q = select(t).order_by(t.c.display_name)
        if active_only:
            q = q.where(t.c.is_active == true())
        with self.engine.connect() as conn:
            return [Tenant.from_row(r) for r in conn.execute(q)]

    def upsert(self, tenant: Tenant) -> None:
        now = self.clock()
        values = dict(
            display_name=tenant.display_name,
            source_schema=tenant.source_schema,
            mapping_path=tenant.mapping_path,
            extract_strategy=str(tenant.extract_strategy),
            is_active=tenant.is_active,
            updated_at=now,
        )
        with self.engine.begin() as conn:
            if conn.execute(update(t).where(t.c.tenant_id == tenant.tenant_id).values(**values)).rowcount == 0:
                conn.execute(t.insert().values(tenant_id=tenant.tenant_id, created_at=now, **values))

    def set_active(self, tenant_id: str, is_active: bool) -> None:
        with self.engine.begin() as conn:
            changed = conn.execute(
                update(t).where(t.c.tenant_id == tenant_id).values(is_active=is_active, updated_at=self.clock())
            ).rowcount
        if changed == 0:
            raise TenantNotFoundError(f"Tenant {tenant_id!r} not found")
