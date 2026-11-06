"""Client configuration store (step 4, issue #16): every change to an NBFC's settings makes a new, numbered version.

The settings stay where they are (bureau_profile, validation_policy, validation_parameter, validation_rule) and the
pages change them as before. After each saved change the API calls `record`, which reads the settings back into one
document (clientconfig.document, JSON Schema) and appends it to automn.config_version (migration 008): who, when,
what changed, why, and the whole document. Rows are only ever added.

- A request records the version that was current when a worker started it (submission_request.config_version), so a
  past file can be explained with the settings of the day (`get`).
- Export is the current document (or any version); import checks a document against the schema and the app's own
  rules first, then applies it and records one version.
- Product defaults (the rule pack, parameter defaults in YAML) are not in the document: only the client's overrides,
  so a new release's defaults reach every client that has not overridden them.

Maker-checker (a second admin approves) is deferred (owner decision 2026-11-06): changes apply directly.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Table, Text, UniqueConstraint, func, select
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect

from canonical.db import metadata

_PK = BigInteger().with_variant(Integer(), "sqlite")

config_version = Table(
    "config_version",
    metadata,
    Column("version_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", String(30), nullable=False),
    Column("version_no", Integer, nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("created_by", String(100), nullable=False),
    Column("action", String(50), nullable=False),
    Column("target", String(200)),
    Column("reason", String(500)),
    Column("changes", Text),  # JSON list of changed paths
    Column("document", Text, nullable=False),
    UniqueConstraint("tenant_id", "version_no", name="uq_config_version_tenant_no"),
)


def current_version_query(tenant_column: ColumnElement[str]) -> ScalarSelect[int]:
    """Correlated subquery: the tenant's latest version number (stamped on a request when a worker claims it)."""
    t = config_version
    return select(func.max(t.c.version_no)).where(t.c.tenant_id == tenant_column).scalar_subquery()
