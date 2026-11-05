"""Tenant (NBFC) record and identifier rules."""

from __future__ import annotations

import re
from dataclasses import dataclass

from canonical.enums import ExtractStrategy

# The schema name is interpolated into generated SQL, so both identifiers are strictly whitelisted.
TENANT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")
SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class InvalidTenantError(ValueError):
    pass


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    display_name: str
    source_schema: str
    mapping_path: str | None = None
    extract_strategy: ExtractStrategy = ExtractStrategy.STREAM
    is_active: bool = True

    def __post_init__(self):
        if not TENANT_ID_RE.match(self.tenant_id):
            raise InvalidTenantError(
                f"Invalid tenant_id {self.tenant_id!r}: lowercase letters, digits, underscore; max 30; starts with a letter"
            )
        if not SCHEMA_RE.match(self.source_schema):
            raise InvalidTenantError(
                f"Invalid source_schema {self.source_schema!r}: letters, digits, underscore; max 64"
            )
        if not self.display_name.strip():
            raise InvalidTenantError("display_name is required")
        object.__setattr__(self, "extract_strategy", ExtractStrategy(self.extract_strategy))

    @classmethod
    def from_row(cls, row) -> Tenant:
        m = dict(row._mapping)
        m.pop("created_at", None)
        m.pop("updated_at", None)
        m["is_active"] = bool(m["is_active"])
        return cls(**m)
