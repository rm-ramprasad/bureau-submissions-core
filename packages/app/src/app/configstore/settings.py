"""The client's own settings that have no table of their own (step 6, admin page): one row per NBFC and name in
automn.client_setting (migration 009), the value as JSON. Changed on the Admin page; each change is audited and makes
a configuration version (app.configstore.store). A missing row means the product default.

- reporting_cycles: the cycles offered for new requests (default: all). Hiding one affects new requests only; past
  requests and files are untouched.
- data_source: per submission type, where a validation builds its snapshot (ADR 0002 switch-over, owner 2026-11-10):
  "views" (default: the type's source views, as before) or "master" (the upload is applied to the demographics master
  through those views, then the snapshot is built from the master + account position). {"CONSUMER": "master"}.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Column, DateTime, Engine, String, Table, Text, delete, select, update

from app.enums import ReportingCycle
from canonical.db import metadata, utcnow

client_setting = Table(
    "client_setting",
    metadata,
    Column("tenant_id", String(30), primary_key=True),
    Column("name", String(50), primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_by", String(100), nullable=False),
    Column("updated_at", DateTime, nullable=False),
)

NAMES = ("reporting_cycles", "data_source")
SOURCES = ("views", "master")


def get_all(engine: Engine, tenant_id: str) -> dict[str, Any]:
    t = client_setting
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.name, t.c.value).where(t.c.tenant_id == tenant_id)).all()
    return {r.name: json.loads(r.value) for r in rows if r.name in NAMES}


def put(engine: Engine, tenant_id: str, name: str, value: Any, user: str) -> None:
    """Store a setting; None removes it (back to the default)."""
    if name not in NAMES:
        raise ValueError(f"Unknown setting {name!r}")
    t = client_setting
    key = (t.c.tenant_id == tenant_id, t.c.name == name)
    with engine.begin() as conn:
        if value is None:
            conn.execute(delete(t).where(*key))
            return
        values = dict(value=json.dumps(value), updated_by=user[:100], updated_at=utcnow())
        if conn.execute(update(t).where(*key).values(**values)).rowcount == 0:
            conn.execute(t.insert().values(tenant_id=tenant_id, name=name, **values))


def enabled_cycles(engine: Engine, tenant_id: str | None) -> list[ReportingCycle]:
    """The cycles offered for new requests, in the product's order (all when not set, or with no NBFC chosen)."""
    chosen = get_all(engine, tenant_id).get("reporting_cycles") if tenant_id else None
    return [c for c in ReportingCycle if chosen is None or c.value in chosen]


def check_cycles(values: object) -> list[str]:
    """The codes, checked: known cycles, at least one. Raises ValueError with a message for the user."""
    if not isinstance(values, list) or not values:
        raise ValueError("Choose at least one reporting cycle.")
    known = {c.value for c in ReportingCycle}
    unknown = [v for v in values if v not in known]
    if unknown:
        raise ValueError(f"Unknown reporting cycle {', '.join(map(str, unknown))}")
    return [c.value for c in ReportingCycle if c.value in values]


def data_source(engine: Engine, tenant_id: str | None, submission_type: str) -> str:
    """Where a validation of this type builds its snapshot: "views" (default) or "master"."""
    chosen = get_all(engine, tenant_id).get("data_source") if tenant_id else None
    value = (chosen or {}).get(str(submission_type).upper(), "views")
    return value if value in SOURCES and on_customer_tables_possible(submission_type) else "views"


def on_customer_tables_possible(submission_type: str) -> bool:
    """True if the type has a customer-table model (sources.master.SELECTION; MFI: not yet, owner 2026-11-11)."""
    from sources.master import SELECTION

    return str(submission_type).upper() in SELECTION


def check_data_source(values: object) -> dict[str, str]:
    """{type: source}, checked; only "master" entries are kept (views is the default)."""
    if not isinstance(values, dict):
        raise ValueError("data_source must map submission types to views / master")
    bad = {k: v for k, v in values.items() if v not in SOURCES}
    if bad:
        raise ValueError(f"Unknown data source {bad}: use views or master")
    return {str(k).upper(): v for k, v in sorted(values.items()) if v == "master"}
