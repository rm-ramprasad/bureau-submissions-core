"""Reference data: canonical code lists (model.yaml) and each submission type's translations / groups
(types/<type>/codes.yaml), written into ref_code, ref_state_pin, ref_code_translation and ref_code_group.

Runs after the migrations on every deploy (admin function `migrate`). The database is the master: packaged rows are
only inserted when missing (initial data for a new installation, or codes added in a release); rows edited or added
in the tables are never updated or deleted, so a new file type or an extra code can be entered as records.

The app's `migrate` admin job calls seed().
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection, Engine, Table, delete, insert, literal, select, true

from bureau_core.types import type_definitions
from canonical.schema import (
    ref_code,
    ref_code_group,
    ref_code_rows,
    ref_code_translation,
    ref_state_pin,
    ref_state_pin_rows,
    snapshot_code_translation,
)

log = logging.getLogger(__name__)


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


def _insert_missing(conn, table: Table, rows: list[dict]) -> int:
    keys = [c.name for c in table.primary_key.columns]
    existing = {tuple(r) for r in conn.execute(select(*(table.c[k] for k in keys)))}
    missing = [row for row in rows if tuple(row[k] for k in keys) not in existing]
    if missing:
        conn.execute(table.insert(), missing)
    return len(missing)


def seed(engine: Engine) -> dict[str, int]:
    """Insert the packaged reference rows that are missing; table -> rows inserted. Existing rows are left as they
    are: the database is the master, so a value edited there is never overwritten by a deploy."""
    work = [
        (ref_code, ref_code_rows()),
        (ref_state_pin, ref_state_pin_rows()),
        (ref_code_translation, translation_rows()),
        (ref_code_group, group_rows()),
    ]
    with engine.begin() as conn:
        return {t.name: _insert_missing(conn, t, rows) for t, rows in work}


class TranslationsNotFrozen(RuntimeError):
    pass


def freeze_translations(conn: Connection, snapshot_id: int, submission_type: str) -> int:
    """Copy the type's active translations to the snapshot (replacing an earlier copy on re-validation); rows copied.
    Validation's code rules and the file writer both read this copy."""
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
    """What a file writer uses: code list -> canonical value -> file code, as frozen when the snapshot was validated."""
    s = snapshot_code_translation.c
    out: dict[str, dict[str, str]] = {}
    for r in conn.execute(select(s.code_list, s.code, s.output_code).where(s.snapshot_id == snapshot_id)):
        out.setdefault(r.code_list, {})[r.code] = r.output_code
    if not out:
        raise TranslationsNotFrozen(
            f"Snapshot {snapshot_id} has no frozen code translations (validated before they "
            "were recorded): validate again (new request) before generating files"
        )
    return out
