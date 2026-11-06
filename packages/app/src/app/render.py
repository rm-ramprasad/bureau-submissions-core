"""Files from a snapshot in the app database: read the snapshot (rows, accepted records, validation drops; the frozen
translations), then render it with the core's engines (bureau_core.formats), which never read a database.

Only ACCEPTED records (record_outcome) are written. Validation findings decide what is left out: REJECT_FIELD drops
that field, REJECT_SEGMENT drops that segment instance. The formatter makes no rule decisions of its own.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import Connection, Engine, select

from app.enums import Outcome, RecordOutcome
from app.validation.tables import record_outcome, validation_finding
from bureau_core.bureau import BureauProfile
from bureau_core.data import Drops, SnapshotData
from bureau_core.formats.delimited import engine as delimited
from bureau_core.formats.excel import engine as excel
from bureau_core.formats.excel import excel_layout
from bureau_core.formats.tudf import engine as tudf
from bureau_core.formats.tudf.engine import FileStats
from canonical.reference import snapshot_translations
from canonical.schema import ENTITY_TABLES


def load_snapshot(conn: Connection, snapshot_id: int, layout: Any) -> SnapshotData:  # a TUDF or delimited Layout
    def rows(entity):
        t = ENTITY_TABLES[entity]
        return conn.execute(select(t).where(t.c.snapshot_id == snapshot_id).order_by(t.c.row_id)).all()

    children = {}
    for seg in layout.segments:
        if seg.rows and seg.rows not in children:
            grouped = defaultdict(list)
            for r in rows(seg.rows):
                grouped[getattr(r, seg.by)].append(r)
            children[seg.rows] = grouped

    drops = Drops()
    f = validation_finding.c
    for x in conn.execute(
        select(f.entity, f.row_id, f.field, f.account_key, f.outcome).where(
            f.snapshot_id == snapshot_id,
            f.outcome.in_([str(Outcome.REJECT_FIELD), str(Outcome.REJECT_SEGMENT)]),
            f.row_id.is_not(None),
        )
    ):
        if x.outcome == Outcome.REJECT_FIELD and x.field:
            drops.fields[(x.entity, x.row_id, x.field)].append(x.account_key)
        elif x.outcome == Outcome.REJECT_SEGMENT:
            drops.segments[(x.entity, x.row_id)].append(x.account_key)

    o = record_outcome.c
    outcomes = conn.execute(
        select(o.account_key, o.party_key, o.outcome)
        .where(o.snapshot_id == snapshot_id)
        .order_by(o.account_key, o.party_key)
    ).all()
    return SnapshotData(
        parties={r.party_key: r for r in rows("party")},
        accounts={r.account_key: r for r in rows("account")},
        holders={(r.account_key, r.party_key): r for r in rows("account_holder")},
        children=children,
        accepted=[(x.account_key, x.party_key) for x in outcomes if x.outcome == RecordOutcome.ACCEPTED],
        rejected=sum(1 for x in outcomes if x.outcome == RecordOutcome.REJECTED),
        drops=drops,
    )


def tudf_file(
    engine: Engine,
    snapshot_id: int,
    as_of_date: date,
    bureau: BureauProfile,
    cycle,
    layout,
    translations: dict[str, dict[str, str]],
) -> tuple[bytes, FileStats]:
    with engine.connect() as conn:
        data = load_snapshot(conn, snapshot_id, layout)
    return tudf.render_file(data, as_of_date, bureau, cycle, layout, translations)


def delimited_file(
    engine: Engine,
    snapshot_id: int,
    as_of_date: date,
    bureau: BureauProfile,
    cycle,
    layout,
    translations: dict[str, dict[str, str]],
    created_date: date,
) -> tuple[bytes, FileStats]:
    with engine.connect() as conn:
        data = load_snapshot(conn, snapshot_id, layout)
    return delimited.render_file(data, as_of_date, bureau, cycle, layout, translations, created_date)


def workbook(
    engine: Engine,
    snapshot_id: int,
    bureau: BureauProfile,
    layout,
    extra_sheets: dict[str, list[list[str]]] | None = None,
) -> excel.ExcelFile:
    """The snapshot's accepted records as a workbook for this bureau (`extra_sheets`: name -> rows, appended)."""
    children = [SimpleNamespace(rows=entity, by=key) for entity, key in layout.child_entities.items()]
    with engine.connect() as conn:
        data = load_snapshot(conn, snapshot_id, SimpleNamespace(segments=children))
        translations = snapshot_translations(conn, snapshot_id)
    return excel.render_workbook(data, translations, bureau, layout, extra_sheets)


def build_excel(
    engine: Engine, snapshot_id: int, bureau: BureauProfile, submission_type: str, extra_sheets: dict | None = None
) -> excel.ExcelFile:
    """The snapshot as the type's Excel for one bureau (types/<type>/excel_layout.yaml)."""
    layout = excel_layout(submission_type)
    if layout is None:
        raise ValueError(f"{submission_type} has no Excel layout (bureau_core/types/<type>/excel_layout.yaml)")
    return workbook(engine, snapshot_id, bureau, layout, extra_sheets)
