"""Excel -> TUDF, standalone: read a filled-in Data Submission Form and write the bureau TUDF file, as the bureaus'
upload portals do. No database, no Azure: usable in this app (each bureau's Excel -> its TUDF) or on its own.

How: the type's Excel layout (types/<type>/excel_layout.yaml, the form's columns) is read in reverse to turn each
form row into in-memory records (borrower, account, holder, IDs, phones, e-mails, addresses); the bureau codes in the
form become standard values with types/<type>/codes.yaml; the type's TUDF layout (types/<type>/layout.yaml) is then
rendered by the same engine the app uses (formats/tudf/engine.render_file). So the spec logic lives in one place.

The form is taken as given (the portal validates it): an empty or unreadable value, or a code that is not in
codes.yaml, is reported as a warning and left out of the file. Member ID and short name come from the form's
member columns unless given.

  from bureau_core.tudf import convert
  result = convert(Path("form.xlsx"), bureau="CIBIL")        # result.content: the TUDF bytes; result.warnings
"""

from __future__ import annotations

import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO

from bureau_core.bureau import BureauProfile
from bureau_core.canonical import canonical_model
from bureau_core.data import Drops, SnapshotData
from bureau_core.enums import BureauCode, ReportingCycle, SubmissionType
from bureau_core.formats.excel import excel_layout
from bureau_core.formats.excel.layout import DIRECT, ExcelColumn
from bureau_core.formats.tudf.engine import FileStats, render_file
from bureau_core.formats.tudf.layout import load_layout
from bureau_core.text import as_date, cell_text, normalise_header
from bureau_core.types import type_definition

HEADER_TAGS = {
    BureauCode.CIBIL: "TUDF",
    BureauCode.EXPERIAN: "CONS",
    BureauCode.CRIF_HIGHMARK: "CONS",
    BureauCode.EQUIFAX: "CONS",
}  # header segment tag per bureau when not given
FILE_NAME = "{member_id}-{created:%d%m%Y}-{created:%H%M%S}.txt"
MAX_WARNINGS = 500


class ConversionError(ValueError):
    """The form cannot be converted (wrong sheet or columns, no member ID, no rows)."""


@dataclass
class ConversionResult:
    content: bytes  # the TUDF file
    file_name: str
    stats: FileStats
    member_id: str
    as_of_date: date
    rows: int  # form rows converted
    warnings: list[str] = field(default_factory=list)


def _tudf_layout(submission_type: str):
    return load_layout(type_definition(submission_type).path("layout.yaml"))


def _translations(submission_type: str) -> tuple[dict, dict]:
    """(forward: code list -> standard value -> bureau code, reverse: code list -> bureau code -> standard value).
    Shared codes reverse to the first (general) value; it translates back to the same code."""
    forward, reverse = {}, {}
    for name, values in type_definition(submission_type).translations.items():
        for value, t in values.items():
            if t.active:
                forward.setdefault(name, {})[value] = t.output_code
                reverse.setdefault(name, {}).setdefault(t.output_code, value)
    return forward, reverse


def _parse(col: ExcelColumn, text: str | None, reverse: dict, where: str, warn) -> object:
    """A form cell (text) back to a standard value, or None."""
    if text is None:
        return None
    if col.translate:
        value = reverse.get(col.translate, {}).get(text)
        if value is None:
            warn(f"{where}: {col.header} {text!r} is not a {col.translate} code; left out")
        return value
    if col.format == "ddmmyyyy":
        d = as_date(text)
        if d is None:
            warn(f"{where}: {col.header} {text!r} is not a date (DDMMYYYY); left out")
        return d
    if col.format in ("amount", "signed_amount", "rate", "integer"):
        try:
            number = Decimal(text.replace(",", ""))
        except InvalidOperation:
            warn(f"{where}: {col.header} {text!r} is not a number; left out")
            return None
        return int(number) if col.format == "integer" else number
    return text


def _row(entity: str, row_id: int, values: dict) -> SimpleNamespace:
    """A record row with every column of the entity (unmapped ones None), as the TUDF engine reads it."""
    blank = {c.name: None for c in canonical_model().entities[entity].columns}
    return SimpleNamespace(**{**blank, **values, "row_id": row_id, "snapshot_id": 0})


def _read_form(workbook, sheet_name: str) -> tuple[list[str], list[tuple[int, tuple]]]:
    import openpyxl
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        source = (
            workbook
            if isinstance(workbook, (str, Path))
            else io.BytesIO(workbook if isinstance(workbook, bytes) else workbook.read())
        )
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    except (InvalidFileException, zipfile.BadZipFile, OSError, KeyError, ValueError) as exc:
        raise ConversionError(f"Not an .xlsx workbook ({type(exc).__name__})") from exc
    try:
        if sheet_name not in wb.sheetnames:
            raise ConversionError(f"The workbook has no sheet {sheet_name!r}")
        rows = wb[sheet_name].iter_rows(values_only=True)
        header = [str(h) if h is not None else "" for h in next(rows, ())]
        return header, [(n, r) for n, r in enumerate(rows, start=2)]
    finally:
        wb.close()


def convert(
    workbook: bytes | BinaryIO | Path | str,
    *,
    submission_type: str = "CONSUMER",
    bureau: str = "CIBIL",
    member_id: str | None = None,
    member_short_name: str | None = None,
    header_tag: str | None = None,
    as_of_date: date | None = None,
    cycle: str | None = None,
) -> ConversionResult:
    """Convert a filled-in form (bytes, file object or path) into the bureau's TUDF file."""
    submission_type, bureau_code = SubmissionType(submission_type.upper()), BureauCode(bureau.upper())
    layout = excel_layout(submission_type)
    if layout is None:
        raise ConversionError(f"{submission_type} has no form layout (types/<type>/excel_layout.yaml)")
    tudf_layout = _tudf_layout(submission_type)
    forward, reverse = _translations(submission_type)
    warnings: list[str] = []

    def warn(text):
        if len(warnings) < MAX_WARNINGS:
            warnings.append(text)

    header, rows = _read_form(workbook, layout.sheet)
    position = {normalise_header(h): i for i, h in enumerate(header)}
    missing = [c.header for c in layout.columns if normalise_header(c.header) not in position]
    if missing:
        raise ConversionError(f"Sheet {layout.sheet!r} lacks column(s): {', '.join(map(repr, missing))}")
    index = {c.header: position[normalise_header(c.header)] for c in layout.columns}
    account_col = next(c for c in layout.columns if c.source == "account" and c.field == "account_number")
    key_column = {e: k for e, k in layout.child_entities.items()}
    groups = defaultdict(list)  # (child entity, match) -> its columns
    for c in layout.columns:
        if c.source not in DIRECT:
            groups[(c.source, tuple(sorted(c.match.items())))].append(c)

    parties, accounts, holders, accepted = {}, {}, {}, []
    children = {seg.rows: defaultdict(list) for seg in tudf_layout.segments if seg.rows}
    for c in layout.child_entities:
        children.setdefault(c, defaultdict(list))
    members, row_id = set(), 0
    for n, raw in rows:
        text = {
            c.header: cell_text(raw[index[c.header]]) if index[c.header] < len(raw) else None for c in layout.columns
        }
        if all(v is None for v in text.values()):
            continue  # empty or formatted-only row
        where = f"row {n}"
        account = text[account_col.header]
        if account is None:
            warn(f"{where}: no {account_col.header}; row left out")
            continue
        parsed = {c.header: _parse(c, text[c.header], reverse, where, warn) for c in layout.columns}
        party_key = f"{account}-{n}"
        values = defaultdict(dict)
        for c in layout.columns:
            if c.source in DIRECT:
                values[c.source][c.field] = text[c.header] if c.source == "bureau" else parsed[c.header]
        members.add((values["bureau"].get("member_id"), values["bureau"].get("member_short_name")))
        row_id += 1
        parties[party_key] = _row("party", row_id, {**values["party"], "party_key": party_key})
        accounts.setdefault(account, _row("account", row_id, {**values["account"], "account_key": account}))
        holders[(account, party_key)] = _row(
            "account_holder", row_id, {**values["holder"], "account_key": account, "party_key": party_key}
        )
        for (entity, match), cols in groups.items():
            fields = {c.field: parsed[c.header] for c in cols}
            if all(v is None for v in fields.values()):
                continue
            key = party_key if key_column[entity] == "party_key" else account
            row_id += 1
            children[entity][key].append(_row(entity, row_id, {**dict(match), **fields, key_column[entity]: key}))
        accepted.append((account, party_key))
    if not accepted:
        raise ConversionError("The form has no rows with an account number")

    form_members = {m for m in members if m[0]}
    if member_id is None:
        if len(form_members) != 1:
            raise ConversionError(
                "Give the member ID: the form's member code column is "
                + ("empty" if not form_members else f"not one value ({sorted(form_members)})")
            )
        member_id, form_short = next(iter(form_members))
        member_short_name = member_short_name if member_short_name is not None else form_short
    if as_of_date is None:
        reported = [a.date_reported for a in accounts.values() if a.date_reported]
        if not reported:
            raise ConversionError("Give the as-of date: the form has no Date Reported")
        as_of_date = max(reported)
    profile = BureauProfile(
        "-",
        bureau_code,
        submission_type,
        str(bureau_code),
        member_id,
        member_short_name,
        header_tag if header_tag is not None else HEADER_TAGS[bureau_code],
        FILE_NAME,
    )
    data = SnapshotData(parties, accounts, holders, {k: dict(v) for k, v in children.items()}, accepted, 0, Drops())
    content, stats = render_file(
        data, as_of_date, profile, ReportingCycle(cycle) if cycle else None, tudf_layout, forward
    )
    return ConversionResult(
        content,
        profile.file_name(datetime.now(), as_of_date, cycle),
        stats,
        member_id,
        as_of_date,
        len(accepted),
        warnings,
    )
