"""The bureau file for any type, by the engine its descriptor names (type.yaml `engine`): one generic writer instead
of a writer per type (step 5). tudf: fixed-width segments (bureau_core/formats/tudf); delimited: pipe-delimited
records (bureau_core/formats/delimited). The layout is the type folder's layout.yaml."""

from __future__ import annotations

from datetime import datetime

import yaml

from app import render
from app.bureaus import BureauProfile
from app.pipeline.base import Services
from app.requests import SubmissionRequest
from bureau_core.formats.tudf.engine import FileStats
from bureau_core.types import descriptor, type_definition
from canonical.reference import snapshot_translations
from canonical.snapshot import Snapshot

OutputFile = tuple[str | None, bytes, FileStats]  # (file key, content, stats); key None = the type's only file


def write_files(
    services: Services, request: SubmissionRequest, snapshot: Snapshot, bureau: BureauProfile, created: datetime
) -> list[OutputFile]:
    """The type's bureau file(s) for one bureau: a line layout (layout.yaml with `files:`, ADR 0003, e.g. MFI's SHG
    and JLG + Individual files) gives one per file that has records; every other layout gives exactly one."""
    layout_file = type_definition(request.submission_type).path("layout.yaml")
    raw = yaml.safe_load(layout_file.read_text(encoding="utf-8"))
    if "files" not in raw:
        content, stats = write_file(services, request, snapshot, bureau, created)
        return [(None, content, stats)]
    from bureau_core.formats.delimited.layout import load_line_layout

    with services.app_engine.connect() as conn:
        translations = snapshot_translations(conn, snapshot.snapshot_id)  # as validated
    files = render.delimited_files(
        services.app_engine,
        snapshot.snapshot_id,
        snapshot.as_of_date,
        bureau,
        request.reporting_cycle,
        load_line_layout(layout_file),
        translations,
        max(created.date(), snapshot.as_of_date),  # the header's creation date is never before the as-of date
    )
    return [(key, content, stats) for key, (content, stats) in files.items()]


def write_file(
    services: Services, request: SubmissionRequest, snapshot: Snapshot, bureau: BureauProfile, created: datetime
) -> tuple[bytes, FileStats]:
    engine = descriptor(request.submission_type).engine
    layout_file = type_definition(request.submission_type).path("layout.yaml")
    with services.app_engine.connect() as conn:
        translations = snapshot_translations(conn, snapshot.snapshot_id)  # as validated
    if engine == "tudf":
        from bureau_core.formats.tudf.layout import load_layout as tudf_layout

        return render.tudf_file(
            services.app_engine,
            snapshot.snapshot_id,
            snapshot.as_of_date,
            bureau,
            request.reporting_cycle,
            tudf_layout(layout_file),
            translations,
        )
    from bureau_core.formats.delimited.layout import load_layout as delimited_layout

    # HD field 4 (date of creation) is never before the as-of date
    return render.delimited_file(
        services.app_engine,
        snapshot.snapshot_id,
        snapshot.as_of_date,
        bureau,
        request.reporting_cycle,
        delimited_layout(layout_file),
        translations,
        max(created.date(), snapshot.as_of_date),
    )
