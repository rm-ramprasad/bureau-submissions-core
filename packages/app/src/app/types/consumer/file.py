"""UCRF consumer file: the layout bureau_core/types/consumer/layout.yaml rendered by the core's TUDF engine
(bureau_core/formats/tudf), from a snapshot in the app database (submissions/render.py)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from sqlalchemy import Engine

from app import render
from app.bureaus import BureauProfile
from app.enums import ReportingCycle, SubmissionType
from bureau_core.data import Drops, SnapshotData
from bureau_core.formats.tudf import engine as tudf
from bureau_core.formats.tudf.engine import FileStats
from bureau_core.formats.tudf.layout import Layout, load_layout
from bureau_core.formats.tudf.parser import ParsedSegment
from bureau_core.formats.tudf.parser import parse_records as _parse_records
from bureau_core.types import type_definition
from canonical.reference import snapshot_translations

LAYOUT_FILE = type_definition(SubmissionType.CONSUMER).path("layout.yaml")


def layout() -> Layout:
    return load_layout(LAYOUT_FILE)


@dataclass(frozen=True)
class FormatOptions:
    """Per-build overrides of the layout's encoding (None = as in the layout)."""

    segment_tag_tlv: bool | None = None  # True: "PN03N01"; False: "PNN01"
    record_separator: str | None = None

    def apply(self, base: Layout) -> Layout:
        style = None if self.segment_tag_tlv is None else ("tlv" if self.segment_tag_tlv else "plain")
        return base.with_encoding(segment_tag_style=style, record_separator=self.record_separator)


def header_length() -> int:
    return layout().header_length


def header(bureau: BureauProfile, as_of_date: date, cycle: ReportingCycle | None) -> str:
    """The fixed-width header segment for this bureau and date."""
    empty = SnapshotData({}, {}, {}, {}, [], 0, Drops())
    return tudf.Renderer(layout(), empty, bureau, SimpleNamespace(as_of_date=as_of_date, cycle=cycle)).header()


def build_file(
    engine: Engine,
    snapshot_id: int,
    as_of_date: date,
    bureau: BureauProfile,
    cycle: ReportingCycle | None = None,
    options: FormatOptions = FormatOptions(),  # noqa: B008 (frozen: safe to share),
) -> tuple[bytes, FileStats]:
    with engine.connect() as conn:
        translations = snapshot_translations(conn, snapshot_id)  # as validated
    return render.tudf_file(engine, snapshot_id, as_of_date, bureau, cycle, options.apply(layout()), translations)


def parse_records(body: str, options: FormatOptions = FormatOptions()) -> list[list[ParsedSegment]]:  # noqa: B008
    return _parse_records(body, options.apply(layout()))


__all__ = [
    "LAYOUT_FILE",
    "FileStats",
    "FormatOptions",
    "ParsedSegment",
    "build_file",
    "header",
    "header_length",
    "layout",
    "parse_records",
]
