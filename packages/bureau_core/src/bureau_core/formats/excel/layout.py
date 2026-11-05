"""Excel layouts: which columns, in which order, with which header, and where each value comes from.

  sheet: Data Submission Form
  order: party_key_suffix            # row order: party_key_suffix (the upload's Excel row when party_key is
                                     # <account>-<row>) or account_party (account, then borrower)
  columns:                           # in output order; `header` is written exactly as given
    - {header: Consumer Name, source: party, field: full_name}
    - {header: Gender, source: party, field: gender, translate: gender}
    - {header: Income Tax ID Number, source: party_identifier, match: {id_type: PAN}, field: id_number}
    - {header: Current/New Member Code, source: bureau, field: member_id}
    - {header: Date Opened/Disbursed, source: account, field: date_opened, format: ddmmyyyy}

Sources: party, account, holder (account_holder of the record), bureau (the bureau profile the file is for), or a child
entity of the borrower or the account (party_identifier, party_phone, party_email, party_address, account_history ...):
the first of its rows (row order) whose columns equal `match`.
Formats (value -> text): text (default), ddmmyyyy, amount (whole rupees), signed_amount (whole rupees, leading '-'),
integer, rate (up to 3 decimals, no trailing zeros). `translate: <code list>` writes the bureau code of a standard
value (the snapshot's frozen translations).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

from bureau_core.canonical import canonical_model

FORMATS = {"text", "ddmmyyyy", "amount", "signed_amount", "integer", "rate"}
ORDERS = {"party_key_suffix", "account_party"}
DIRECT = {"party", "account", "holder", "bureau"}
BUREAU_FIELDS = {"member_id", "member_short_name", "bureau_code", "display_name"}


class ExcelLayoutError(ValueError):
    pass


@dataclass(frozen=True)
class ExcelColumn:
    header: str
    source: str
    field: str
    format: str = "text"
    translate: str | None = None
    match: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ExcelLayout:
    sheet: str
    order: str
    columns: tuple[ExcelColumn, ...]

    @property
    def child_entities(self) -> dict[str, str]:
        """child entity -> the key column linking it to the record (party_key or account_key)."""
        model = canonical_model()
        return {
            c.source: (model.entities[c.source].party_key or model.entities[c.source].account_key)
            for c in self.columns
            if c.source not in DIRECT
        }


def parse(raw: dict, where: str = "excel_layout.yaml") -> ExcelLayout:
    if not isinstance(raw, dict) or set(raw) - {"sheet", "order", "columns"} or not raw.get("columns"):
        raise ExcelLayoutError(f"{where}: needs 'sheet', 'order' and a non-empty 'columns' list")
    if raw.get("order") not in ORDERS:
        raise ExcelLayoutError(f"{where}: order must be one of {sorted(ORDERS)}")
    model, columns, headers = canonical_model(), [], set()
    for i, c in enumerate(raw["columns"], 1):
        label = f"{where}: column {i}"
        if (
            not isinstance(c, dict)
            or not {"header", "source", "field"} <= set(c)
            or set(c) - {"header", "source", "field", "format", "translate", "match"}
        ):
            raise ExcelLayoutError(f"{label}: needs header, source, field (optional format, translate, match)")
        col = ExcelColumn(
            str(c["header"]),
            str(c["source"]),
            str(c["field"]),
            str(c.get("format", "text")),
            c.get("translate"),
            dict(c.get("match") or {}),
        )
        if col.header in headers:
            raise ExcelLayoutError(f"{label}: header {col.header!r} appears twice")
        headers.add(col.header)
        if col.format not in FORMATS:
            raise ExcelLayoutError(f"{label} ({col.header}): format must be one of {sorted(FORMATS)}")
        if col.source == "bureau":
            if col.field not in BUREAU_FIELDS:
                raise ExcelLayoutError(f"{label} ({col.header}): bureau field must be one of {sorted(BUREAU_FIELDS)}")
        else:
            entity = {"holder": "account_holder"}.get(col.source, col.source)
            edef = model.entities.get(entity)
            if edef is None:
                raise ExcelLayoutError(f"{label} ({col.header}): unknown source {col.source!r}")
            names = {x.name for x in edef.columns}
            if col.field not in names or set(col.match) - names:
                raise ExcelLayoutError(
                    f"{label} ({col.header}): not columns of {entity}: {sorted(({col.field} | set(col.match)) - names)}"
                )
            if col.source not in DIRECT and not (edef.party_key or edef.account_key):
                raise ExcelLayoutError(f"{label} ({col.header}): {entity} has no party or account key")
            if col.source in DIRECT and col.match:
                raise ExcelLayoutError(f"{label} ({col.header}): match is only for child entities")
        columns.append(col)
    return ExcelLayout(str(raw["sheet"]), raw["order"], tuple(columns))


@cache
def load_excel_layout(path: Path) -> ExcelLayout:
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
