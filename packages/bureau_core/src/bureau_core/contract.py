"""The one input of file generation, whatever the source: a CanonicalBatch (canonical rows, codes only), plus the
ValidationResult that decides what is written. Both are plain JSON, typed by the canonical model (model.yaml).

Sources (user decisions, 2026-11-02): (a) all data in SQL and (b) a workbook staged into SQL are turned into canonical
codes by SQL views, outside the core; (c, consumer only) the bureau Excel, codes already filled in. The core never
translates labels: a value outside its code list is a problem (check_batch), never passed through silently.

    python -m bureau_core.contract --schema          # the JSON Schema of a batch, from model.yaml
    python -m bureau_core.contract batch.json        # check a batch file

Row values in JSON: dates as YYYY-MM-DD, decimals as strings (exact), integers as numbers, code / string as strings.
Every row carries row_id (unique per entity), which validation drops refer to.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace

from bureau_core.canonical import CanonicalModel, ColumnDef, canonical_model
from bureau_core.data import Drops, SnapshotData

BATCH_FORMAT = "canonical-batch/1"
RESULT_FORMAT = "validation-result/1"
ACCEPTED, REJECTED = "ACCEPTED", "REJECTED"


@dataclass
class CanonicalBatch:
    """Canonical rows of one submission: entity -> rows (dicts with row_id and the entity's columns)."""

    submission_type: str
    as_of_date: date
    canonical_version: str
    entities: dict[str, list[dict]]
    source: dict = field(default_factory=dict)  # free metadata (tenant, snapshot, upload); not read by the core
    # The user exceptions in force (Exceptions step), as frozen with the snapshot: "adjustments" (snapshot_adjustment
    # rows) and "effects" (snapshot_adjustment_effect rows). They are already applied to the rows; validation only
    # reads them to report an exception that matched nothing. Left out of the JSON when there are none.
    exceptions: dict[str, list[dict]] = field(default_factory=dict)

    def to_json(self) -> str:
        doc = {
            "format": BATCH_FORMAT,
            "canonical_version": self.canonical_version,
            "submission_type": self.submission_type,
            "as_of_date": self.as_of_date.isoformat(),
            "source": self.source,
            "entities": {e: [{k: _encode(v) for k, v in r.items()} for r in rows] for e, rows in self.entities.items()},
        }
        if any(self.exceptions.values()):
            doc["exceptions"] = {
                k: [{c: _encode(v) for c, v in r.items()} for r in rows] for k, rows in self.exceptions.items()
            }
        return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str, model: CanonicalModel | None = None) -> CanonicalBatch:
        doc = _doc(text, BATCH_FORMAT)
        model = model or canonical_model()
        entities = {}
        for name, rows in doc["entities"].items():
            cols = {c.name: c for c in model.entities[name].columns} if name in model.entities else {}
            entities[name] = [{k: _decode(cols.get(k), v) for k, v in r.items()} for r in rows]
        exceptions = {
            k: [{c: _decode_exception(c, v) for c, v in r.items()} for r in rows]
            for k, rows in (doc.get("exceptions") or {}).items()
        }
        return cls(
            doc["submission_type"],
            date.fromisoformat(doc["as_of_date"]),
            doc["canonical_version"],
            entities,
            doc.get("source") or {},
            exceptions,
        )


@dataclass
class ValidationResult:
    """What validation decided for a batch: one outcome per record (account x party), field and segment drops
    (account_key None = for every record), and the code translations the file writers use, as validated."""

    # (account_key, party_key, ACCEPTED / REJECTED, rule that rejected it or None), in record order
    outcomes: list[tuple[str, str, str, str | None]]
    field_drops: list[tuple[str, int, str, str | None]]  # (entity, row_id, column, account_key)
    segment_drops: list[tuple[str, int, str | None]]  # (entity, row_id, account_key)
    translations: dict[str, dict[str, str]]  # code list -> canonical value -> file code
    findings: list[dict] = field(default_factory=list)  # rule_id, entity, row_id, account_key, party_key,
    # segment, field, outcome, value, message

    def to_json(self) -> str:
        doc = {
            "format": RESULT_FORMAT,
            "outcomes": [list(x) for x in self.outcomes],
            "drops": {"fields": [list(x) for x in self.field_drops], "segments": [list(x) for x in self.segment_drops]},
            "translations": self.translations,
            "findings": self.findings,
        }
        return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"

    @classmethod
    def from_json(cls, text: str) -> ValidationResult:
        doc = _doc(text, RESULT_FORMAT)
        return cls(
            [(*x, None)[:4] for x in doc["outcomes"]],
            [tuple(x) for x in doc["drops"]["fields"]],
            [tuple(x) for x in doc["drops"]["segments"]],
            doc["translations"],
            doc.get("findings") or [],
        )


# ---------------------------------------------------------------- values
def _encode(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _decode(col: ColumnDef | None, value):
    """The typed value; a value that does not convert is kept as it is, for check_batch to report."""
    if col is None or value is None:
        return value
    try:
        if col.type == "date" and isinstance(value, str):
            return date.fromisoformat(value)
        if col.type == "decimal" and isinstance(value, (str, int)) and not isinstance(value, bool):
            return Decimal(str(value))
    except ValueError, InvalidOperation:
        pass
    return value


_EXCEPTION_AMOUNTS = {"override_value", "old_value", "new_value"}


def _decode_exception(column: str, value):
    """Exception rows: timestamps (*_at) and amounts back to their types; the rest as it is."""
    if value is None or not isinstance(value, str):
        return value
    if column.endswith("_at"):
        return datetime.fromisoformat(value)
    if column in _EXCEPTION_AMOUNTS:
        return Decimal(value)
    return value


def _doc(text: str, fmt: str) -> dict:
    doc = json.loads(text)
    if not isinstance(doc, dict) or doc.get("format") != fmt:
        raise ValueError(f"Not a {fmt} document: {doc.get('format') if isinstance(doc, dict) else type(doc)!r}")
    return doc


# ---------------------------------------------------------------- checks
@dataclass(frozen=True)
class Problem:
    entity: str
    row_id: int | None
    column: str | None
    kind: str  # ENTITY / COLUMN / ROW_ID / TYPE / LENGTH / CODE / VERSION
    message: str


def _type_ok(col: ColumnDef, v) -> bool:
    return {
        "string": lambda: isinstance(v, str),
        "code": lambda: isinstance(v, str),
        "date": lambda: isinstance(v, date),
        "decimal": lambda: isinstance(v, Decimal) and v.is_finite(),
        "integer": lambda: isinstance(v, int) and not isinstance(v, bool),
    }[col.type]()


def check_batch(batch: CanonicalBatch, model: CanonicalModel | None = None) -> list[Problem]:
    """Every way the batch breaks the contract, in a stable order (same batch -> same list). Codes are checked against
    the canonical code lists only: a source that sends a label or a file code instead of the canonical value fails."""
    model = model or canonical_model()
    out: list[Problem] = []
    if batch.canonical_version != model.version:
        out.append(
            Problem(
                "",
                None,
                None,
                "VERSION",
                f"canonical_version {batch.canonical_version!r}, the core expects {model.version!r}",
            )
        )
    codes = {n: {c.code for c in cl.codes} for n, cl in model.code_lists.items()}
    for name in sorted(batch.entities):
        rows = batch.entities[name]
        entity = model.entities.get(name)
        if entity is None:
            out.append(Problem(name, None, None, "ENTITY", f"unknown entity {name!r}"))
            continue
        cols = {c.name: c for c in entity.columns}
        seen: set = set()
        for r in rows:
            rid = r.get("row_id")
            if not isinstance(rid, int) or isinstance(rid, bool) or rid in seen:
                out.append(
                    Problem(
                        name,
                        rid if isinstance(rid, int) else None,
                        "row_id",
                        "ROW_ID",
                        "row_id must be a unique integer within the entity",
                    )
                )
            seen.add(rid)
            for k in sorted(r):
                if k == "row_id":
                    continue
                col, v = cols.get(k), r[k]
                if col is None:
                    out.append(Problem(name, rid, k, "COLUMN", f"unknown column {k!r}"))
                elif v is None:
                    continue
                elif not _type_ok(col, v):
                    out.append(Problem(name, rid, k, "TYPE", f"{v!r} is not a {col.type}"))
                elif col.type in ("string", "code") and col.length and len(v) > col.length:
                    out.append(Problem(name, rid, k, "LENGTH", f"{len(v)} characters, at most {col.length}"))
                elif col.type == "code" and v not in codes[col.code_list]:
                    out.append(Problem(name, rid, k, "CODE", f"{v!r} is not in code list {col.code_list}"))
    return out


# ---------------------------------------------------------------- what the format engines read
def _rows(batch: CanonicalBatch, entity: str, model: CanonicalModel) -> list[SimpleNamespace]:
    blank = {c.name: None for c in model.entities[entity].columns}  # a column the batch leaves out is NULL
    return [SimpleNamespace(**{**blank, **r}) for r in batch.entities.get(entity, [])]


def snapshot_data(
    batch: CanonicalBatch,
    result: ValidationResult,
    children: list[tuple[str, str]],
    model: CanonicalModel | None = None,
) -> SnapshotData:
    """The engines' input: `children` = (entity, key column) the layout reads, grouped in row_id order."""
    model = model or canonical_model()
    grouped = {}
    for entity, by in children:
        if entity not in grouped:
            g = defaultdict(list)
            for r in sorted(_rows(batch, entity, model), key=lambda x: x.row_id):
                g[getattr(r, by)].append(r)
            grouped[entity] = g
    drops = Drops()
    for entity, row_id, column, account_key in result.field_drops:
        drops.fields[(entity, row_id, column)].append(account_key)
    for entity, row_id, account_key in result.segment_drops:
        drops.segments[(entity, row_id)].append(account_key)
    return SnapshotData(
        parties={r.party_key: r for r in _rows(batch, "party", model)},
        accounts={r.account_key: r for r in _rows(batch, "account", model)},
        holders={(r.account_key, r.party_key): r for r in _rows(batch, "account_holder", model)},
        children=grouped,
        accepted=[(a, p) for a, p, x, *_ in result.outcomes if x == ACCEPTED],  # in the result's order
        rejected=sum(1 for _, _, x, *_ in result.outcomes if x == REJECTED),
        drops=drops,
    )


# ---------------------------------------------------------------- JSON Schema
def json_schema(model: CanonicalModel | None = None) -> dict:
    """The JSON Schema (2020-12) of a CanonicalBatch for this canonical model: the contract a source must meet."""
    model = model or canonical_model()

    def column(c: ColumnDef) -> dict:
        s = {
            "string": lambda: {"type": "string", "maxLength": c.length},
            "code": lambda: {"type": "string", "enum": sorted(x.code for x in model.code_lists[c.code_list].codes)},
            "date": lambda: {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "decimal": lambda: {"type": "string", "pattern": r"^-?\d+(\.\d+)?$"},
            "integer": lambda: {"type": "integer"},
        }[c.type]()
        if c.description:
            s["description"] = c.description
        return {"anyOf": [s, {"type": "null"}]}

    entities = {
        name: {
            "type": "array",
            "description": e.description,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["row_id"],
                "properties": {"row_id": {"type": "integer"}, **{c.name: column(c) for c in e.columns}},
            },
        }
        for name, e in model.entities.items()
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"CanonicalBatch (canonical model {model.version})",
        "type": "object",
        "required": ["format", "canonical_version", "submission_type", "as_of_date", "entities"],
        "properties": {
            "format": {"const": BATCH_FORMAT},
            "canonical_version": {"const": model.version},
            "submission_type": {"type": "string"},
            "as_of_date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "source": {"type": "object"},
            "entities": {"type": "object", "additionalProperties": False, "properties": entities},
        },
    }


def main() -> int:
    p = argparse.ArgumentParser(description="The CanonicalBatch contract: print its JSON Schema or check a batch.")
    p.add_argument("batch", nargs="?", type=Path)
    p.add_argument("--schema", action="store_true")
    args = p.parse_args()
    if args.schema:
        print(json.dumps(json_schema(), indent=1))
        return 0
    if not args.batch:
        p.error("give a batch file or --schema")
    problems = check_batch(CanonicalBatch.from_json(args.batch.read_text(encoding="utf-8")))
    for x in problems:
        print(f"{x.kind:8} {x.entity}#{x.row_id} {x.column or ''}: {x.message}")
    print(f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
