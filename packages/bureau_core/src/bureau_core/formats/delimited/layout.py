"""Delimited layout: the whole file format as data (YAML), checked when it is loaded.

A file is: a header line; per borrower group a group line followed by its child lines (e.g. BS, then AS / RS); per
record of the group a record line followed by its child lines (e.g. CR, then GS / SS / CD); a trailer line. Every
line is <tag><delimiter><field 2><delimiter>...<field n>[<delimiter>]<line end>.

Each segment lists its fields 2..n in order (field 1 is the tag) and declares `fields:` count from the spec, which
the loader checks. A field says where its value comes from (`source`) or `const` / `blank`, and like the TUDF layout:
`check`, `when`, `if_present`, `default`, `translate`, `cap`, `max_len`, `format` (+ `args`), `alternatives`.

Source paths: `column` (the line's own row), `party.<col>`, `account.<col>`, `bureau.<attr>`, `run.<attr>`
(as_of_date, created_date, cycle), `id.<ID_TYPE>` (the borrower's valid identifier of that canonical type),
`contacts.mobile | telephone | email` (the borrower's valid phones / first e-mail), `count.<TAG>` (lines written).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

from bureau_core.canonical import canonical_model
from bureau_core.formats.delimited.formatters import VALUE_FORMATTERS

HEADS = {"party", "account", "bureau", "run", "id", "contacts", "count"}
CHECKS = {"present", "not_null", "none"}
TAG_RE = re.compile(r"^[A-Z]{2}$")


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Encoding:
    delimiter: str = "|"
    line_end: str = "\r\n"
    trailing_delimiter: bool = True  # every line also ends with the delimiter
    charset: str = "ascii"
    strip_chars: str = "~^"  # control characters that reject a whole file; the delimiter is always stripped


@dataclass(frozen=True)
class Field:
    source: str | None = None
    const: str | None = None
    blank: bool = False  # filler / reserved: always empty
    format: str = "text"
    args: dict = field(default_factory=dict)
    check: str = "present"
    when: dict = field(default_factory=dict)
    if_present: str | None = None  # only when this source path has a value
    default: object = None
    translate: str | None = None
    cap: int | None = None
    max_len: int | None = None
    alternatives: tuple[Field, ...] = ()


@dataclass(frozen=True)
class Segment:
    tag: str
    fields: tuple[Field, ...]
    source: str | None = None  # party | account (group / record lines); None for header / trailer
    rows: str | None = None  # child lines: canonical entity ...
    by: str | None = None  # ... joined on party_key or account_key
    order_by: tuple[str, ...] = ()
    max: int | None = None
    drops: bool = True  # leave out rows validation rejected (REJECT_SEGMENT)
    children: tuple[Segment, ...] = ()


@dataclass(frozen=True)
class Layout:
    name: str
    encoding: Encoding
    header: Segment
    group: Segment  # one per borrower (and split value), from party
    group_split: str | None  # source path that splits a borrower into several groups (e.g. branch)
    record: Segment  # one per accepted record of the group, from account
    trailer: Segment
    aux: tuple[tuple[str, str], ...]  # (entity, by) loaded for id. / contacts. sources

    @property
    def segments(self) -> list:
        """Every child-row entity to load (snapshot.load_snapshot reads .rows / .by)."""
        children = [c for s in (self.group, self.record) for c in s.children]
        return children + [Segment(tag="--", fields=(), rows=e, by=b) for e, b in self.aux]


def _known(where: str, spec: dict, allowed: set[str]) -> None:
    unknown = set(spec) - allowed
    if unknown:
        raise LayoutError(f"{where}: unknown keys {sorted(unknown)}")


def _check_path(where: str, path: str) -> None:
    head, _, rest = path.partition(".")
    if rest and head not in HEADS:
        raise LayoutError(f"{where}: unknown source {path!r} (use a column or {', '.join(sorted(HEADS))})")


def _field(where: str, spec) -> Field:
    if spec is None or spec == "blank":
        return Field(blank=True)
    _known(where, spec, set(Field.__dataclass_fields__))
    alternatives = tuple(_field(f"{where} alternative {i}", a) for i, a in enumerate(spec.get("alternatives", []), 1))
    f = Field(
        source=spec.get("source"),
        const=None if spec.get("const") is None else str(spec["const"]),
        blank=bool(spec.get("blank", False)),
        format=spec.get("format", "text"),
        args=spec.get("args", {}),
        check=spec.get("check", "present"),
        when=spec.get("when", {}),
        if_present=spec.get("if_present"),
        default=spec.get("default"),
        translate=spec.get("translate"),
        cap=spec.get("cap"),
        max_len=spec.get("max_len"),
        alternatives=alternatives,
    )
    given = sum(map(bool, (f.source, f.const is not None, f.blank, f.alternatives)))
    if given != 1:
        raise LayoutError(f"{where}: give exactly one of source, const, blank, alternatives")
    if f.format not in VALUE_FORMATTERS:
        raise LayoutError(f"{where}: unknown format {f.format!r} (known: {sorted(VALUE_FORMATTERS)})")
    if f.check not in CHECKS:
        raise LayoutError(f"{where}: check must be one of {sorted(CHECKS)}")
    if f.translate is not None and f.translate not in canonical_model().code_lists:
        raise LayoutError(f"{where}: translate: unknown canonical code list {f.translate!r}")
    if f.format == "lookup" and not isinstance(f.args.get("map"), dict):
        raise LayoutError(f"{where}: format lookup needs args.map")
    for path in (f.source, f.if_present, *f.when):
        if path:
            _check_path(where, path)
    return f


def _segment(where: str, spec: dict, source: str | None = None) -> Segment:
    _known(
        where,
        spec,
        {"tag", "fields", "field_list", "from", "rows", "by", "order_by", "max", "drops", "children", "split_by"},
    )
    tag = spec.get("tag", "")
    if not TAG_RE.match(tag):
        raise LayoutError(f"{where}: tag must be two capital letters")
    fields = tuple(_field(f"{where} {tag} field {i}", f) for i, f in enumerate(spec.get("field_list", []), 2))
    if spec.get("fields") != len(fields) + 1:
        raise LayoutError(
            f"{where} {tag}: declares {spec.get('fields')} fields, field_list gives {len(fields) + 1} "
            "(field 1 is the tag)"
        )
    rows, by = spec.get("rows"), spec.get("by")
    if rows is not None and (rows not in canonical_model().entities or by not in ("party_key", "account_key")):
        raise LayoutError(f"{where} {tag}: rows must be a canonical entity and by party_key or account_key")
    children = tuple(_segment(f"{where} {tag} child", c) for c in spec.get("children", []))
    if any(c.rows is None for c in children):
        raise LayoutError(f"{where} {tag}: child lines need rows and by")
    return Segment(
        tag=tag,
        fields=fields,
        source=spec.get("from", source),
        rows=rows,
        by=by,
        order_by=tuple(spec.get("order_by", [])),
        max=spec.get("max"),
        drops=bool(spec.get("drops", True)),
        children=children,
    )


def parse_layout(raw: dict) -> Layout:
    _known("layout", raw, {"layout", "encoding", "header", "group", "record", "trailer", "uses"})
    _known("encoding", raw.get("encoding", {}), set(Encoding.__dataclass_fields__))
    encoding = Encoding(**raw.get("encoding", {}))
    group = _segment("group", raw["group"], "party")
    record = _segment("record", raw["record"], "account")
    if group.source != "party" or record.source != "account":
        raise LayoutError("group lines come from party, record lines from account")
    split = raw["group"].get("split_by")
    if split:
        _check_path("group split_by", split)
    aux = tuple((u["rows"], u["by"]) for u in raw.get("uses", []))
    for entity, by in aux:
        if entity not in canonical_model().entities or by not in ("party_key", "account_key"):
            raise LayoutError(f"uses: {entity} / {by}: unknown entity or key")
    tags = [s.tag for s in (_segment("header", raw["header"]), group, record, *group.children, *record.children)]
    if len(tags) != len(set(tags)):
        raise LayoutError("duplicate segment tags")
    return Layout(
        name=raw["layout"],
        encoding=encoding,
        header=_segment("header", raw["header"]),
        group=group,
        group_split=split,
        record=record,
        trailer=_segment("trailer", raw["trailer"]),
        aux=aux,
    )


@cache
def load_layout(path: Path) -> Layout:
    return parse_layout(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
