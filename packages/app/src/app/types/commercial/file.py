"""Commercial UCRF file: the layout bureau_core/types/commercial/layout.yaml rendered by the core's delimited engine
(bureau_core/formats/delimited), from a snapshot in the app database (submissions/render.py)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine

from app import render
from app.bureaus import BureauProfile
from app.enums import ReportingCycle, SubmissionType
from bureau_core.formats.delimited.layout import Layout, load_layout
from bureau_core.formats.tudf.engine import FileStats
from bureau_core.types import type_definition
from canonical.reference import snapshot_translations

LAYOUT_FILE = type_definition(SubmissionType.COMMERCIAL).path("layout.yaml")


def layout() -> Layout:
    return load_layout(LAYOUT_FILE)


def build_file(
    engine: Engine,
    snapshot_id: int,
    as_of_date: date,
    bureau: BureauProfile,
    cycle: ReportingCycle | None,
    created_date: date,
) -> tuple[bytes, FileStats]:
    """The file for one bureau. `created_date` is HD field 4 (date of creation), never before the as-of date."""
    with engine.connect() as conn:
        translations = snapshot_translations(conn, snapshot_id)  # as validated
    return render.delimited_file(
        engine, snapshot_id, as_of_date, bureau, cycle, layout(), translations, max(created_date, as_of_date)
    )


__all__ = ["LAYOUT_FILE", "FileStats", "build_file", "layout"]
