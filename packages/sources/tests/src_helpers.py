"""Shared test helpers for sources (prefixed: unique across packages)."""

import io

import openpyxl

from canonical.staging.spec import load_spec


def workbook(submission_type, rows_by_sheet=None, *, headers=None, hidden=(), drop=(), extra_sheets=()) -> bytes:
    """An .xlsx with the layout's sheets: row 1 = the layout's headers (or `headers[sheet]`), then the given rows
    (dicts staging column -> value)."""
    spec = load_spec(submission_type)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name in extra_sheets:
        wb.create_sheet(name).append(["anything"])
    for s in spec.sheets:
        if s.sheet in drop:
            continue
        ws = wb.create_sheet(s.sheet)
        cols = list(s.columns)
        ws.append((headers or {}).get(s.sheet) or [s.columns[c] for c in cols] + ["Formula", "Final Formula"])
        for r in (rows_by_sheet or {}).get(s.sheet, []):
            ws.append([r.get(c) for c in cols] + ["=A2", "BS|..."])
        if s.sheet in hidden:
            ws.sheet_state = "hidden"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
