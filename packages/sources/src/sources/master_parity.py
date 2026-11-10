"""Parity before the switch-over (ADR 0002 PR 6): for one upload and as-of date, the snapshot rows the current path
builds (the type's source views) next to the rows the new path builds (the upload applied to the demographics master,
then the master + account position). Equal rows mean equal files. Writes only the master (nothing reads it yet);
no snapshot is created.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import Engine, text

from canonical.schema import MODEL
from sources.extract import coerce
from sources.master import compile_master
from sources.master_load import entities_to_master, rows_from_views
from sources.views import bind_params

EXAMPLES = 5


def _row(entity: str, r: dict[str, Any]) -> tuple[str, ...]:
    return tuple("" if r.get(c.name) is None else str(coerce(c, r.get(c.name))) for c in MODEL.entities[entity].columns)


def _example(entity: str, row: tuple[str, ...]) -> dict[str, str]:
    return {c.name: v for c, v in zip(MODEL.entities[entity].columns, row, strict=True) if v != ""}


def compare(
    app_engine: Engine,
    source_engine: Engine,
    tenant_id: str,
    submission_type: str,
    as_of_date: date,
    load_id: int,
    *,
    history: bool = False,
) -> dict[str, Any]:
    """{"same": bool, "summary": [plain lines], "entities": {entity: {views, master, only_in_views, only_in_master}}}"""
    from canonical.master.apply import apply_upload

    submission_type = submission_type.upper()
    old = rows_from_views(source_engine, submission_type, as_of_date, load_id)
    parties, sanctions = entities_to_master(old, submission_type)
    applied = apply_upload(app_engine, tenant_id, load_id, parties, sanctions)
    compiled = compile_master(app_engine, submission_type, history=history)
    new: dict[str, list[dict[str, Any]]] = {}
    with app_engine.connect() as conn:
        for entity, sql in compiled.sql.items():
            params = bind_params(sql, {"as_of_date": as_of_date, "tenant_id": tenant_id})
            new[entity] = [dict(r._mapping) for r in conn.execute(text(sql), params)]

    entities: dict[str, Any] = {}
    summary: list[str] = []
    for entity in MODEL.entities:
        a = Counter(_row(entity, r) for r in old.get(entity, []))
        b = Counter(_row(entity, r) for r in new.get(entity, []))
        if not a and not b:
            continue
        only_old, only_new = a - b, b - a
        entities[entity] = {
            "views": sum(a.values()),
            "master": sum(b.values()),
            "only_in_views": [_example(entity, r) for r in sorted(only_old.elements())[:EXAMPLES]],
            "only_in_master": [_example(entity, r) for r in sorted(only_new.elements())[:EXAMPLES]],
        }
        if only_old or only_new:
            summary.append(
                f"{entity}: {sum(a.values())} rows today, {sum(b.values())} new; "
                f"{sum(only_old.values())} only today, {sum(only_new.values())} only new"
            )
    same = not summary
    head = (
        f"{submission_type} {as_of_date} upload {load_id}: "
        + ("IDENTICAL - every row the same" if same else f"{len(summary)} part(s) differ")
        + f" (master: {applied.parties_added} parties added, {applied.parties_updated} updated; "
        f"{applied.sanctions_added} accounts added, {applied.sanctions_updated} updated)"
    )
    return {"same": same, "summary": [head, *summary], "entities": entities}
