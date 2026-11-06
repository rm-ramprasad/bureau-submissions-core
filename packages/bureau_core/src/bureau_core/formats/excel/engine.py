"""Write a validated snapshot as an Excel workbook (layout: bureau_core/formats/excel/layout.py).

Exactly what is submitted: only ACCEPTED records (record_outcome); a field validation rejected (REJECT_FIELD) is left
empty; a child row validation rejected (REJECT_SEGMENT) is skipped. Codes come from the snapshot's frozen
translations. Every cell is text, so codes and account numbers keep their leading zeros.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Any

from bureau_core.bureau import BureauProfile
from bureau_core.data import SnapshotData
from bureau_core.formats.excel.layout import ExcelColumn, ExcelLayout
from bureau_core.formats.tudf.encoding import amount, ddmmyyyy, whole_rupees

ENTITY_OF = {"party": "party", "account": "account", "holder": "account_holder"}


@dataclass
class ExcelFile:
    content: bytes
    rows: int  # records written (accepted)
    rejected: int  # records left out by validation
    headers: list[str] = field(default_factory=list)
    values: list[list[str]] = field(default_factory=list)  # the written cells, for reconciliation


def format_value(value, fmt: str) -> str:
    if value is None or value == "":
        return ""
    if fmt == "ddmmyyyy":
        return ddmmyyyy(value)
    if fmt == "amount":
        return amount(value)
    if fmt == "signed_amount":
        return str(whole_rupees(value))
    if fmt == "integer":
        return str(int(value))
    if fmt == "rate":
        text = str(Decimal(str(value)).quantize(Decimal("0.001"), rounding=ROUND_DOWN))
        return text.rstrip("0").rstrip(".") if "." in text else text
    return str(value).strip()


def record_order(layout: ExcelLayout, records: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """(account_key, party_key) in output order."""
    if layout.order == "party_key_suffix":  # party_key = <account>-<Excel row>: the upload's row order

        def key(r):
            m = re.search(r"-(\d+)$", r[1])
            return (0, int(m[1]), r) if m else (1, 0, r)

        return sorted(records, key=key)
    return sorted(records)


class _Writer:
    def __init__(self, layout: ExcelLayout, data: SnapshotData, translations: dict, bureau: BureauProfile):
        self.layout, self.data, self.translations, self.bureau = layout, data, translations, bureau

    def _row_of(self, col: ExcelColumn, account_key: str, party_key: str):
        """(entity, row) the column reads, or (entity, None)."""
        if col.source in ENTITY_OF:
            entity = ENTITY_OF[col.source]
            row = {
                "party": lambda: self.data.parties.get(party_key),
                "account": lambda: self.data.accounts.get(account_key),
                "holder": lambda: self.data.holders.get((account_key, party_key)),
            }[col.source]()
            return entity, row
        key = party_key if self.layout.child_entities[col.source] == "party_key" else account_key
        for row in self.data.children.get(col.source, {}).get(key, []):
            if self.data.drops.segment_dropped(col.source, row.row_id, account_key):
                continue
            if all(str(getattr(row, k)) == str(v) for k, v in col.match.items()):
                return col.source, row
        return col.source, None

    def cell(self, col: ExcelColumn, account_key: str, party_key: str) -> str:
        if col.source == "bureau":
            return format_value(getattr(self.bureau, col.field), col.format)
        entity, row = self._row_of(col, account_key, party_key)
        if row is None or self.data.drops.field_dropped(entity, row.row_id, col.field, account_key):
            return ""
        value = getattr(row, col.field)
        if col.translate and value is not None:
            value = self.translations.get(col.translate, {}).get(str(value))  # untranslatable: validation flagged
        return format_value(value, col.format)


def add_sheets(content: bytes, sheets: dict[str, list[list[str]]]) -> bytes:
    """The workbook with these sheets appended (name -> rows of text); the existing sheets are left as they are."""
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    wb = load_workbook(io.BytesIO(content))
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        for r in rows:
            ws.append([str(v) if v is not None else "" for v in r])
        for cell in ws[1] if ws.max_row else []:
            cell.font = Font(bold=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def new_workbook(sheets: dict[str, list[list[Any]]]) -> bytes:
    """A new workbook of these sheets (name -> rows of text), header rows bold. Written row by row (write-only), so
    memory stays small however many rows a sheet has."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    wb = Workbook(write_only=True)
    bold = Font(bold=True)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name[:31])
        for i, r in enumerate(rows):
            values = [str(v) if v is not None else "" for v in r]
            if i == 0:
                cells = []
                for v in values:
                    c = WriteOnlyCell(ws, value=v)
                    c.font = bold
                    cells.append(c)
                ws.append(cells)
            else:
                ws.append(values)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def render_workbook(
    data: SnapshotData,
    translations: dict,
    bureau: BureauProfile,
    layout: ExcelLayout,
    extra_sheets: dict[str, list[list[str]]] | None = None,
) -> ExcelFile:
    """The workbook for records already in memory (no database): from a snapshot (build_workbook) or a capture."""
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    writer = _Writer(layout, data, translations, bureau)
    headers = [c.header for c in layout.columns]
    values = [[writer.cell(c, a, p) for c in layout.columns] for a, p in record_order(layout, data.accepted)]

    wb = Workbook(write_only=True)
    ws = wb.create_sheet(layout.sheet)
    bold = Font(bold=True)

    def text_row(cells, header=False):
        out = []
        for v in cells:
            cell = WriteOnlyCell(ws, value=v)
            cell.number_format = "@"  # text: leading zeros kept, no number guessing
            if header:
                cell.font = bold
            out.append(cell)
        return out

    ws.append(text_row(headers, header=True))
    for row in values:
        ws.append(text_row(row))
    for name, rows in (extra_sheets or {}).items():
        extra = wb.create_sheet(name)
        for r in rows:
            extra.append([str(v) if v is not None else "" for v in r])
    buf = io.BytesIO()
    wb.save(buf)
    return ExcelFile(buf.getvalue(), len(values), data.rejected, headers, values)
