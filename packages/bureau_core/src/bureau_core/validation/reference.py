"""Reference data for validation: the packaged rows (model.yaml code lists, each type's codes.yaml translations and
groups) and the translations frozen per snapshot. The app database seeds its own tables from the same rows."""

from __future__ import annotations

from sqlalchemy import Connection, delete, insert, literal, select, true

from bureau_core.types import type_definitions
from bureau_core.validation.tables import MODEL, ref_code_translation, snapshot_code_translation


def ref_code_rows() -> list[dict]:
    return [
        dict(code_list=cl.name, code=c.code, description=c.description, is_active=c.active)
        for cl in MODEL.code_lists.values()
        for c in cl.codes
    ]


def ref_state_pin_rows() -> list[dict]:
    return [
        dict(state_code=c.code, pin_prefix_min=c.pin_min, pin_prefix_max=c.pin_max)
        for c in MODEL.code_lists["state_code"].codes
        if c.pin_min is not None
    ]


def translation_rows() -> list[dict]:
    return [
        dict(
            submission_type=str(d.submission_type),
            code_list=list_name,
            code=value,
            output_code=t.output_code,
            is_active=t.active,
        )
        for d in type_definitions().values()
        for list_name, values in d.translations.items()
        for value, t in values.items()
    ]


def group_rows() -> list[dict]:
    return [
        dict(submission_type=str(d.submission_type), group_name=name, code=value)
        for d in type_definitions().values()
        for name, values in d.groups.items()
        for value in values
    ]


class TranslationsNotFrozen(RuntimeError):
    pass


def freeze_translations(conn: Connection, snapshot_id: int, submission_type: str) -> int:
    """Copy the type's active translations to the snapshot (replacing an earlier copy); rows copied."""
    t, s = ref_code_translation.c, snapshot_code_translation
    conn.execute(delete(s).where(s.c.snapshot_id == snapshot_id))
    return conn.execute(
        insert(s).from_select(
            ["snapshot_id", "code_list", "code", "output_code"],
            select(literal(snapshot_id), t.code_list, t.code, t.output_code).where(
                t.submission_type == str(submission_type), t.is_active == true()
            ),
        )
    ).rowcount


def snapshot_translations(conn: Connection, snapshot_id: int) -> dict[str, dict[str, str]]:
    """code list -> canonical value -> file code, as frozen when the snapshot was validated."""
    s = snapshot_code_translation.c
    out: dict[str, dict[str, str]] = {}
    for r in conn.execute(select(s.code_list, s.code, s.output_code).where(s.snapshot_id == snapshot_id)):
        out.setdefault(r.code_list, {})[r.code] = r.output_code
    if not out:
        raise TranslationsNotFrozen(f"Snapshot {snapshot_id} has no frozen code translations")
    return out
