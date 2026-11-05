"""Workbook layouts: which sheets of an uploaded workbook are loaded, into which staging table, and which header goes
into which column. One file per submission type, canonical/types/<type>/inputs.yaml:

  workbook: Commercial input file
  sheets:
    - sheet: BS                      # sheet name as in the workbook (hidden sheets are never read)
      table: stg_commercial_bs       # staging table (prefix stg_)
      key: ac_no                     # column every row must have; rows without it are reported, not loaded
      columns:                       # staging column: header text in row 1 (matched ignoring case, spacing, quotes)
        ac_no: A/c No.
        borrower_name: Borrower‟s Name

Every staging table gets load_id (the upload, input_load) and source_row (the Excel row number) as its key, then the
listed columns as text. A layout change in the workbook is an edit here plus a migration for the table.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from bureau_core.text import normalise_header

TYPES_DIR = Path(__file__).resolve().parent.parent / "types"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
TABLE_RE = re.compile(r"^stg_[a-z0-9_]{1,56}$")
RESERVED = {"load_id", "source_row"}
VALUE_LENGTH = 1000  # longer cell values are cut and reported


def column_name(header) -> str:
    """A staging column name derived from a header: 'Overdue Bucket 01\\n( 1 – 30 days)' -> overdue_bucket_01_1_30_days."""
    s = unicodedata.normalize("NFKD", normalise_header(header)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "_", s.replace("#", " no ").replace("%", " pct ")).strip("_")
    return s if s and s[0].isalpha() else f"c_{s}"


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class SheetSpec:
    sheet: str
    table: str
    key: str
    columns: dict[str, str]  # staging column -> header

    @property
    def headers(self) -> dict[str, str]:
        """normalised header -> staging column"""
        return {normalise_header(h): c for c, h in self.columns.items()}


@dataclass(frozen=True)
class WorkbookSpec:
    submission_type: str
    workbook: str
    sheets: tuple[SheetSpec, ...]


def parse(data: dict, submission_type: str) -> WorkbookSpec:
    where = f"{submission_type} inputs.yaml"
    if not isinstance(data, dict) or set(data) - {"workbook", "sheets"} or not data.get("sheets"):
        raise SpecError(f"{where}: needs 'workbook' and a non-empty 'sheets' list, nothing else")
    sheets, tables, names = [], set(), set()
    for i, raw in enumerate(data["sheets"]):
        if not isinstance(raw, dict) or set(raw) != {"sheet", "table", "key", "columns"}:
            raise SpecError(f"{where}: sheet #{i + 1} needs exactly sheet, table, key, columns")
        sheet, table, key, columns = str(raw["sheet"]), str(raw["table"]), str(raw["key"]), raw["columns"]
        if sheet in names or table in tables:
            raise SpecError(f"{where}: sheet {sheet!r} or table {table!r} listed twice")
        if not TABLE_RE.match(table):
            raise SpecError(f"{where}: table {table!r} must look like stg_<name> (a-z, 0-9, _)")
        if not isinstance(columns, dict) or not columns:
            raise SpecError(f"{where}: {sheet}: columns must map staging column -> header")
        bad = [c for c in columns if not NAME_RE.match(str(c)) or c in RESERVED]
        if bad:
            raise SpecError(f"{where}: {sheet}: invalid column names {bad} (a-z, 0-9, _; not load_id/source_row)")
        normalised = [normalise_header(h) for h in columns.values()]
        if len(set(normalised)) != len(normalised) or "" in normalised:
            raise SpecError(f"{where}: {sheet}: headers must be non-empty and distinct")
        if key not in columns:
            raise SpecError(f"{where}: {sheet}: key {key!r} is not one of its columns")
        names.add(sheet)
        tables.add(table)
        sheets.append(SheetSpec(sheet, table, key, {str(c): str(h) for c, h in columns.items()}))
    return WorkbookSpec(submission_type, str(data["workbook"]), tuple(sheets))


@cache
def load_spec(submission_type: str) -> WorkbookSpec:
    path = TYPES_DIR / submission_type.lower() / "inputs.yaml"
    if not path.is_file():
        raise SpecError(f"No input workbook layout for {submission_type} ({path})")
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")), submission_type.upper())


def available_specs() -> list[str]:
    return sorted(p.parent.name.upper() for p in TYPES_DIR.glob("*/inputs.yaml"))
