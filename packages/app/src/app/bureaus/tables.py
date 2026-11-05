"""SQLAlchemy Core definition of bureau_profile (mirrors migrations/001 + 002)."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, Table

import canonical.tenants.tables  # noqa: F401  (tenant table must exist for the foreign key)
from canonical.db import metadata

bureau_profile = Table(
    "bureau_profile",
    metadata,
    Column("tenant_id", String(30), ForeignKey("automn.tenant.tenant_id"), primary_key=True),
    Column("bureau_code", String(20), primary_key=True),
    Column("submission_type", String(20), primary_key=True),
    Column("display_name", String(50), nullable=False),
    Column("member_id", String(30), nullable=False),
    Column("member_short_name", String(16)),
    Column("header_segment_tag", String(4), nullable=False),
    Column("file_name_pattern", String(200), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("updated_at", DateTime, nullable=False),
)
