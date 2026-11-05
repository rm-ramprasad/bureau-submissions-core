"""Which uploaded workbook a request uses: the latest upload per submission type of its batch. While the request is a
draft a new upload replaces the previous one; after submit the uploads are locked, so the latest stays the one used."""

from __future__ import annotations

from sqlalchemy import Engine, desc, select

from canonical.staging.tables import input_load


def latest_loads(engine: Engine, batch_id: str) -> dict[str, dict]:
    """submission type -> its latest upload for this request (any status)."""
    with engine.connect() as conn:
        rows = conn.execute(
            select(input_load).where(input_load.c.batch_id == batch_id).order_by(desc(input_load.c.load_id))
        )
        latest: dict[str, dict] = {}
        for r in rows:
            latest.setdefault(r.submission_type, dict(r._mapping))
    return latest


def loaded_workbook(engine: Engine, batch_id: str | None, submission_type: str) -> int | None:
    """The load_id of the request's workbook for this type, or None if its latest upload did not load."""
    if not batch_id:
        return None
    load = latest_loads(engine, batch_id).get(str(submission_type))
    return load["load_id"] if load and load["status"] == "LOADED" else None
