"""Shared test helpers for bureau_core (no host app, no database)."""

from __future__ import annotations

import io

import openpyxl

from bureau_core.enums import SubmissionType
from bureau_core.formats.tudf.layout import Layout, load_layout
from bureau_core.formats.tudf.parser import ParsedSegment
from bureau_core.formats.tudf.parser import parse_records as _parse_records
from bureau_core.types import type_definition


def consumer_layout() -> Layout:
    return load_layout(type_definition(SubmissionType.CONSUMER).path("layout.yaml"))


def parse_records(body: str) -> list[list[ParsedSegment]]:
    return _parse_records(body, consumer_layout())


def other_workbook(sheet: str = "Sanction") -> bytes:
    """An .xlsx without the bureau form's sheet."""
    wb = openpyxl.Workbook()
    wb.active.title = sheet
    wb.active.append(["anything"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
