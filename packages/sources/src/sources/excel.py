"""Load an uploaded workbook into its staging tables: one table per sheet (types/<type>/inputs.yaml), one row per
non-empty Excel row, values as text exactly as received (typing, code conversion and joins come later).

What stops a load (ERROR, nothing is kept): not a workbook, a listed sheet missing or hidden, a listed header missing
or present twice. What is reported and loaded around (WARN): a row without its key (skipped), an Excel error value
such as #N/A (stored as NULL), a value longer than the column (cut), a header the layout does not list.
Hidden sheets are never read. Each upload is one input_load row; its staging rows carry its load_id.

"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import BinaryIO

from sqlalchemy import Engine, delete, insert, select, update

from bureau_core.text import cell_text, normalise_header
from canonical.db import utcnow
from canonical.staging.spec import SheetSpec, WorkbookSpec, load_spec
from canonical.staging.tables import input_issue, input_load, staging_tables

log = logging.getLogger(__name__)

BATCH = 1000  # rows per INSERT
MAX_ISSUES = 2000  # issues kept per load; the rest are counted in a final issue
HELPER_HEADERS = {
    "formula",
    "final formula",
    "filler",
    "none",
    "",
    "sanctionreferencenumberformatted",
    "column16",
    "column17",
}  # the workbooks' own helper columns (not loaded, not reported)
EXCEL_ERRORS = {
    "#N/A",
    "#VALUE!",
    "#REF!",
    "#DIV/0!",
    "#NUM!",
    "#NAME?",
    "#NULL!",
    "#SPILL!",
    "#CALC!",
    "#GETTING_DATA",
}


@dataclass(frozen=True)
class Issue:
    severity: str  # ERROR | WARN
    code: str
    message: str
    sheet: str | None = None
    source_row: int | None = None
    column_name: str | None = None


@dataclass
class LoadResult:
    load_id: int
    status: str  # LOADED | FAILED
    row_counts: dict[str, int] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "ERROR"]


class _Stop(Exception):
    """An ERROR issue was recorded: the load is rolled back."""


class _Loader:
    def __init__(self, spec: WorkbookSpec):
        self.spec, self.tables = spec, staging_tables(spec)
        self.issues: list[Issue] = []
        self.dropped = 0

    def issue(self, severity, code, message, sheet=None, row=None, column=None):
        if len(self.issues) < MAX_ISSUES or severity == "ERROR":
            self.issues.append(Issue(severity, code, message, sheet, row, column))
        else:
            self.dropped += 1
        if severity == "ERROR":
            raise _Stop(message)

    def header_map(self, s: SheetSpec, header: tuple) -> dict[str, int]:
        """staging column -> 0-based column index; ERROR if a listed header is missing or repeated."""
        wanted, found, unmapped = s.headers, {}, []
        for i, h in enumerate(header):
            key = normalise_header(h)
            if key in wanted:
                if wanted[key] in found:
                    self.issue(
                        "ERROR",
                        "DUPLICATE_HEADER",
                        f"{s.sheet}: header '{h}' appears more than once",
                        s.sheet,
                        1,
                        wanted[key],
                    )
                found[wanted[key]] = i
            elif key not in HELPER_HEADERS:
                unmapped.append(str(h).strip())
        missing = [s.columns[c] for c in s.columns if c not in found]
        if missing:
            self.issue(
                "ERROR",
                "COLUMN_MISSING",
                f"{s.sheet}: missing column(s) {', '.join(map(repr, missing))} "
                "in row 1 (the layout is types/<type>/inputs.yaml)",
                s.sheet,
                1,
            )
        for h in dict.fromkeys(unmapped):
            self.issue(
                "WARN",
                "COLUMN_UNMAPPED",
                f"{s.sheet}: column '{h}' is not in the layout and was not loaded",
                s.sheet,
                1,
            )
        return found

    def rows(self, s: SheetSpec, ws) -> list[dict]:
        rows_iter = ws.iter_rows(min_row=1, max_row=1, max_col=500, values_only=True)
        header = next(rows_iter, ())
        cols = self.header_map(s, header)
        width = max(cols.values()) + 1
        limits = {name: self.tables[s.sheet].c[name].type.length for name in cols}
        out, seen = [], {}  # values (trimmed) -> the first Excel row with them
        for n, raw in enumerate(ws.iter_rows(min_row=2, max_col=width, values_only=True), start=2):
            row = {}
            for name, i in cols.items():
                value = raw[i] if i < len(raw) else None
                text = cell_text(value)
                if text is not None and text.upper() in EXCEL_ERRORS:
                    self.issue(
                        "WARN",
                        "EXCEL_ERROR",
                        f"{s.sheet} row {n}: {s.columns[name]} is {text}; loaded as empty",
                        s.sheet,
                        n,
                        name,
                    )
                    text = None
                if text is not None and len(text) > limits[name]:
                    self.issue(
                        "WARN",
                        "VALUE_TOO_LONG",
                        f"{s.sheet} row {n}: {s.columns[name]} has {len(text)} "
                        f"characters; the first {limits[name]} were loaded",
                        s.sheet,
                        n,
                        name,
                    )
                    text = text[: limits[name]]
                row[name] = text
            if all(v is None for v in row.values()):
                continue  # empty (or formatted-only) row
            if row[s.key] is None:
                self.issue(
                    "WARN",
                    "KEY_MISSING",
                    f"{s.sheet} row {n}: no {s.columns[s.key]}; row not loaded",
                    s.sheet,
                    n,
                    s.key,
                )
                continue
            values = tuple(row.values())
            if values in seen:  # an exact repeat (after trimming): loaded once (user, 2026-10-30)
                self.issue(
                    "WARN",
                    "DUPLICATE_ROW",
                    f"{s.sheet} row {n}: the same as row {seen[values]} (values trimmed); not loaded again",
                    s.sheet,
                    n,
                )
                continue
            seen[values] = n
            out.append({"source_row": n, **row})
        return out

    def run(self, conn, workbook, load_id: int) -> dict[str, int]:
        counts = {}
        for s in self.spec.sheets:
            if s.sheet not in workbook.sheetnames:
                self.issue("ERROR", "SHEET_MISSING", f"Sheet '{s.sheet}' is missing from the workbook", s.sheet)
            ws = workbook[s.sheet]
            if ws.sheet_state != "visible":
                self.issue(
                    "ERROR",
                    "SHEET_HIDDEN",
                    f"Sheet '{s.sheet}' is hidden; hidden sheets are not read. Unhide it if it holds this run's data",
                    s.sheet,
                )
            rows = self.rows(s, ws)
            table = self.tables[s.sheet]
            for i in range(0, len(rows), BATCH):
                conn.execute(insert(table), [{"load_id": load_id, **r} for r in rows[i : i + BATCH]])
            counts[s.sheet] = len(rows)
        return counts


def load_workbook(
    engine: Engine,
    submission_type: str,
    workbook: bytes | BinaryIO,
    *,
    tenant_id: str,
    file_name: str,
    loaded_by: str,
    batch_id: str | None = None,
    file_uri: str | None = None,
    as_of_date: date | None = None,
    clock: Callable[[], datetime] = utcnow,
) -> LoadResult:
    """Load one uploaded workbook. Always records an input_load row (LOADED or FAILED) and its issues. as_of_date: the
    data's date (the request's, or the one given on the Uploads page); source views without a loan system view (MFI)
    read it."""
    import openpyxl
    from openpyxl.utils.exceptions import InvalidFileException

    data = workbook if isinstance(workbook, bytes) else workbook.read()
    spec = load_spec(submission_type)
    with engine.begin() as conn:
        load_id = conn.execute(
            insert(input_load).values(
                tenant_id=tenant_id,
                submission_type=spec.submission_type,
                batch_id=batch_id,
                file_name=file_name[:255],
                file_uri=file_uri,
                sha256=hashlib.sha256(data).hexdigest(),
                status="FAILED",
                loaded_by=loaded_by,
                loaded_at=clock(),
                as_of_date=as_of_date,
            )
        ).inserted_primary_key[0]
    loader, counts, status = _Loader(spec), {}, "FAILED"
    try:
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except (zipfile.BadZipFile, InvalidFileException, KeyError, ValueError, OSError) as exc:
            loader.issue("ERROR", "NOT_A_WORKBOOK", f"{file_name} is not an .xlsx workbook ({type(exc).__name__})")
        try:
            with engine.begin() as conn:  # all sheets or nothing
                counts = loader.run(conn, wb, load_id)
        finally:
            wb.close()
        status = "LOADED"
    except _Stop:
        counts = {}
    if loader.dropped:
        loader.issues.append(Issue("WARN", "MORE_ISSUES", f"{loader.dropped} more issue(s) not listed"))
    errors = [i.message for i in loader.issues if i.severity == "ERROR"]
    with engine.begin() as conn:
        conn.execute(
            update(input_load)
            .where(input_load.c.load_id == load_id)
            .values(status=status, row_counts=json.dumps(counts), error_message="; ".join(errors)[:2000] or None)
        )
        if loader.issues:
            conn.execute(
                insert(input_issue),
                [
                    {
                        "load_id": load_id,
                        "severity": i.severity,
                        "code": i.code,
                        "sheet": i.sheet,
                        "source_row": i.source_row,
                        "column_name": i.column_name,
                        "message": i.message[:1000],
                    }
                    for i in loader.issues
                ],
            )
    log.info("Input load %s (%s, %s): %s %s", load_id, spec.submission_type, file_name, status, counts)
    return LoadResult(load_id, status, counts, loader.issues)


def purge_loads(engine: Engine, before: datetime) -> list[int]:
    """Delete the staged rows and issues of loads older than `before` (PII retention); the input_load header stays,
    marked PURGED. Returns the purged load ids."""
    with engine.begin() as conn:
        ids = [
            r.load_id
            for r in conn.execute(
                select(input_load.c.load_id).where(input_load.c.loaded_at < before, input_load.c.status != "PURGED")
            )
        ]
        for load_id in ids:
            for tables in _all_staging_tables():
                conn.execute(delete(tables).where(tables.c.load_id == load_id))
            conn.execute(delete(input_issue).where(input_issue.c.load_id == load_id))
        if ids:
            conn.execute(update(input_load).where(input_load.c.load_id.in_(ids)).values(status="PURGED"))
    return ids


def _all_staging_tables():
    from canonical.staging.tables import STAGING_TABLES

    return [t for by_sheet in STAGING_TABLES.values() for t in by_sheet.values()]
