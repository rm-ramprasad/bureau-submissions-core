"""Delimited layout: the whole file format as data (YAML), checked when it is loaded.

A file is: a header line; per borrower group a group line followed by its child lines (e.g. BS, then AS / RS); per
record of the group a record line followed by its child lines (e.g. CR, then GS / SS / CD); a trailer line. Every
line is <tag><delimiter><field 2><delimiter>...<field n>[<delimiter>]<line end>.

Each segment lists its fields 2..n in order (field 1 is the tag) and declares `fields:` count from the spec, which
the loader checks. A field says where its value comes from (`source`) or `const` / `blank`, and like the TUDF layout:
`check`, `when`, `if_present`, `default`, `translate`, `cap`, `max_len`, `format` (+ `args`), `alternatives`.

Source paths: `column` (the line's own row), `party.<col>`, `account.<col>`, `bureau.<attr>`, `run.<attr>`
(as_of_date, created_date, cycle), `id.<ID_TYPE>` (the borrower's valid identifier of that canonical type),
`contacts.mobile | telephone | email` (the borrower's valid phones / first e-mail), `count.<TAG>` (lines written),
and (line layouts) `address.<CATEGORY>.<col>` (the borrower's first address of that canonical category; `full` =
its address lines joined), `phone.<n>.<col>` (the borrower's n-th phone by priority),
`relative.<ROLE>.<n>.<col>` (the borrower's n-th relative of that role), `group.<col>` (the account's group,
borrower_group by account.group_key), `file.key` (the file being written).

Line layouts (ADR 0003, MFI): instead of group / record lines, `files:` lists one or more files written from the same
records; each file has a `select` (source path -> allowed canonical values), a `record_line` (the segments joined on
one line per accepted record, e.g. GRPCRD | CNSCRD | ADRCRD | ACTCRD) and the line's declared `columns`. Segments are
defined once under `segments:`. A segment can be `fixed` (no delimiter; every field has a `width`, the segment a
`length`), as MFI's header and trailer are.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml

from bureau_core.canonical import canonical_model
from bureau_core.formats.delimited.formatters import VALUE_FORMATTERS

HEADS = {
    "party",
    "account",
    "bureau",
    "run",
    "id",
    "contacts",
    "count",
    "address",
    "relative",
    "group",
    "file",
    "phone",
}
CHECKS = {"present", "not_null", "none"}
TAG_RE = re.compile(r"^[A-Z]{2,6}$")  # CR / BS ... ; HDR, CNSCRD ... (MFI)
SOURCES = {"party", "account", "group"}


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
    if_positive: str | None = None  # only when this source path's value is a number above 0 (e.g. DPD)
    default: object = None
    translate: str | None = None
    cap: int | None = None
    max_len: int | None = None
    alternatives: tuple[Field, ...] = ()
    width: int | None = None  # fixed segments: the field's width
    align: str = "left"  # fixed segments: left (pad right) or right (pad left)
    pad: str = " "


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
    fixed: bool = False  # no delimiter: each field padded to its width
    length: int | None = None  # fixed: the line length without the line end (checked)
    tag_width: int | None = None  # fixed: the tag's width (default its own length)


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
    if isinstance(spec, str):  # shorthand: a source path alone
        spec = {"source": spec}
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
        if_positive=spec.get("if_positive"),
        default=spec.get("default"),
        translate=spec.get("translate"),
        cap=spec.get("cap"),
        max_len=spec.get("max_len"),
        alternatives=alternatives,
        width=spec.get("width"),
        align=spec.get("align", "left"),
        pad=str(spec.get("pad", " ")),
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
    if f.align not in ("left", "right") or len(f.pad) != 1:
        raise LayoutError(f"{where}: align must be left or right, pad one character")
    if f.format == "lookup" and not isinstance(f.args.get("map"), dict):
        raise LayoutError(f"{where}: format lookup needs args.map")
    for path in (f.source, f.if_present, f.if_positive, *f.when):
        if path:
            _check_path(where, path)
    return f


def _segment(where: str, spec: dict, source: str | None = None) -> Segment:
    _known(
        where,
        spec,
        {
            "tag",
            "fields",
            "field_list",
            "from",
            "rows",
            "by",
            "order_by",
            "max",
            "drops",
            "children",
            "split_by",
            "fixed",
            "length",
            "tag_width",
        },
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
    fixed = bool(spec.get("fixed", False))
    if fixed:
        if any(f.width is None for f in fields):
            raise LayoutError(f"{where} {tag}: a fixed segment gives every field a width")
        total = (spec.get("tag_width") or len(tag)) + sum(f.width or 0 for f in fields)
        if spec.get("length") != total:
            raise LayoutError(f"{where} {tag}: declares length {spec.get('length')}, the widths add up to {total}")
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
        fixed=fixed,
        length=spec.get("length"),
        tag_width=spec.get("tag_width"),
    )


@dataclass(frozen=True)
class FileSpec:
    """One file of a line layout: which records, and the segments joined on each record's line."""

    key: str  # e.g. SHG / JLG; file.key in the header, part of the file name
    select: dict  # source path -> allowed canonical values (all must match); empty = every record
    record_line: tuple[Segment, ...]
    columns: int  # declared fields per line (the segments' fields, tags included), checked
    skip_empty: bool = True  # no file when no record is selected
    dedupe: bool = True  # identical lines written once (MFI 2.7)


@dataclass(frozen=True)
class LineLayout:
    """A layout written as one line per accepted record, into one or more files (ADR 0003)."""

    name: str
    encoding: Encoding
    header: Segment
    trailer: Segment
    files: tuple[FileSpec, ...]
    aux: tuple[tuple[str, str], ...]

    @property
    def segments(self) -> list:
        """Every child-row entity to load (as Layout.segments)."""
        return [Segment(tag="--", fields=(), rows=e, by=b) for e, b in self.aux]


def parse_line_layout(raw: dict) -> LineLayout:
    _known("layout", raw, {"layout", "encoding", "header", "trailer", "segments", "files", "uses"})
    _known("encoding", raw.get("encoding", {}), set(Encoding.__dataclass_fields__))
    segments = {}
    for name, spec in (raw.get("segments") or {}).items():
        seg = _segment(f"segment {name}", spec)
        if seg.source not in SOURCES:
            raise LayoutError(f"segment {name}: from must be one of {sorted(SOURCES)}")
        segments[name] = seg
    files = []
    for i, spec in enumerate(raw.get("files") or [], 1):
        _known(f"file {i}", spec, {"key", "select", "record_line", "columns", "skip_empty", "dedupe"})
        missing = [n for n in spec.get("record_line", []) if n not in segments]
        if missing or not spec.get("record_line"):
            raise LayoutError(f"file {i}: record_line names unknown segments {missing}")
        line = tuple(segments[n] for n in spec["record_line"])
        columns = sum(len(s.fields) + 1 for s in line)
        if spec.get("columns") != columns:
            raise LayoutError(
                f"file {spec.get('key')}: declares {spec.get('columns')} columns, the segments give {columns}"
            )
        for path in spec.get("select") or {}:
            _check_path(f"file {spec.get('key')} select", path)
        files.append(
            FileSpec(
                key=str(spec["key"]),
                select={p: tuple(str(v) for v in vals) for p, vals in (spec.get("select") or {}).items()},
                record_line=line,
                columns=columns,
                skip_empty=bool(spec.get("skip_empty", True)),
                dedupe=bool(spec.get("dedupe", True)),
            )
        )
    if not files:
        raise LayoutError("a line layout needs at least one file")
    keys = [f.key for f in files]
    if len(keys) != len(set(keys)):
        raise LayoutError("duplicate file keys")
    aux = tuple((u["rows"], u["by"]) for u in raw.get("uses", []))
    for entity, by in aux:
        if entity not in canonical_model().entities or by not in ("party_key", "account_key", "group_key"):
            raise LayoutError(f"uses: {entity} / {by}: unknown entity or key")
    return LineLayout(
        name=raw["layout"],
        encoding=Encoding(**raw.get("encoding", {})),
        header=_segment("header", raw["header"]),
        trailer=_segment("trailer", raw["trailer"]),
        files=tuple(files),
        aux=aux,
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


@cache
def load_line_layout(path: Path) -> LineLayout:
    return parse_line_layout(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
