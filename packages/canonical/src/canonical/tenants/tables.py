"""SQLAlchemy Core definition of tenant (mirrors migrations/002_tenants_and_run_mode.sql)."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, String, Table, UniqueConstraint

from canonical.db import metadata

tenant = Table(
    "tenant",
    metadata,
    Column("tenant_id", String(30), primary_key=True),
    Column("display_name", String(100), nullable=False),
    Column("source_schema", String(64), nullable=False),
    Column("mapping_path", String(255)),
    Column("extract_strategy", String(20), nullable=False, default="stream"),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime, nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("source_schema", name="uq_tenant_source_schema"),
)
