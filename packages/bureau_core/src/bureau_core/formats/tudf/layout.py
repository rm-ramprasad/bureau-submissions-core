"""TUDF layout: the whole file format as data (YAML), checked when it is loaded.

A layout declares the encoding (segment tag style, record separator, record end, trailer), the fixed-width header,
and the segments of a record in order. Each segment says which rows it is written from (`from`, plus `rows` for
repeating segments), its sequence label, how many it may repeat, and its fields. Each field says its tag, where
its value comes from (`source`), whether it must be present (`check`), when it applies (`when`), limits (`cap`,
`max_len`), a `default`, the canonical code list whose file code is written (`translate`), and the named `format`
that turns the value into text (formatters.py). Snapshot values are canonical (`FEMALE`), so `when`, `default` and
formatter args use canonical values; `translate` turns them into the file's code (`1`) just before formatting.

Source paths: `column` (the segment's own row), `party.<col>`, `account.<col>`, `holder.<col>`,
`bureau.<attribute>`, `run.as_of_date` / `run.cycle`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path

import yaml

from bureau_core.canonical import canonical_model
from bureau_core.formats.tudf.formatters import MULTI_FORMATTERS, VALUE_FORMATTERS

RECORD_SOURCES = {"party": "party", "account": "account", "holder": "account_holder"}  # path prefix -> entity
OTHER_SOURCES = {"bureau", "run"}
CHECKS = {"present", "not_null", "none"}
TAG_RE = re.compile(r"^\d{2}$")


class LayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Encoding:
    """Choices a bureau may set; the spec leaves some open (§14 #16). Overridable per build (FormatOptions)."""

    segment_tag_style: str = "plain"  # "plain": PNN01 | "tlv": PN03N01
    record_separator: str = ""
    record_end: str = "ES02**"
    trailer: str = "TRLR"
    charset: str = "ascii"


@dataclass(frozen=True)
class HeaderField:
    name: str
    width: int
    source: str | None = None
    const: str | None = None
    format: str | None = None
    default: str = ""
    pad: str = "space"  # "space": text left-justified | "zero": numbers right-justified


@dataclass(frozen=True)
class FieldSpec:
    tag: str | None = None  # one field ...
    tags: tuple[str, ...] = ()  # ... or several from a multi formatter
    source: str | None = None
    sources: tuple[str, ...] = ()
    format: str = "raw"
    args: dict = field(default_factory=dict)
    check: str = "present"  # NULL is never written; present: also not empty and not dropped by
    # validation | not_null / none: written whenever there is a value
    when: dict = field(default_factory=dict)  # {source path: [allowed values]}
    only_positive: bool = False
    cap: int | None = None  # numbers above it are written as the cap
    max_len: int | None = None  # text cut to this length after formatting
    default: object = None  # value (or source path "run.as_of_date" ...) used when missing
    translate: str | None = None  # canonical code list: write this type's file code for the value
    required: bool = False  # no value -> the whole segment instance is left out
    alternatives: tuple[FieldSpec, ...] = ()  # first one that gives a value
    warn: str | None = None  # message when a multi formatter had to cut text


@dataclass(frozen=True)
class SegmentSpec:
    tag: str
    seq: str  # e.g. "N01", "I{n:02d}"
    source: str  # party | account | holder | rows
    rows: str | None = None  # repeating segments: canonical entity (child rows)
    by: str | None = None  # ... grouped by party_key or account_key
    order_by: tuple[str, ...] = ()  # columns; "-col" = descending; NULL sorts last
    max: int | None = None
    drops: bool = True  # leave out instances that validation rejected (REJECT_SEGMENT)
    fields: tuple[FieldSpec, ...] = ()

    @property
    def seq_length(self) -> int:
        return len(self.seq.format(n=1))


@dataclass(frozen=True)
class Layout:
    name: str
    encoding: Encoding
    header_length: int
    header: tuple[HeaderField, ...]
    segments: tuple[SegmentSpec, ...]

    def with_encoding(self, **changes) -> Layout:
        return replace(self, encoding=replace(self.encoding, **{k: v for k, v in changes.items() if v is not None}))


# ---------------------------------------------------------------- loading and checking
def _known(where: str, spec: dict, allowed: set[str]) -> None:
    unknown = set(spec) - allowed
    if unknown:
        raise LayoutError(f"{where}: unknown keys {sorted(unknown)}")


def _check_path(where: str, path: str) -> None:
    head, _, rest = path.partition(".")
    if rest and head not in RECORD_SOURCES and head not in OTHER_SOURCES:
        raise LayoutError(f"{where}: unknown source {path!r} (use a column, party., account., holder., bureau., run.)")


def _field(where: str, spec: dict) -> FieldSpec:
    _known(
        where,
        spec,
        {
            "tag",
            "tags",
            "source",
            "sources",
            "format",
            "args",
            "check",
            "when",
            "only_positive",
            "cap",
            "max_len",
            "default",
            "translate",
            "required",
            "alternatives",
            "warn",
        },
    )
    alternatives = tuple(
        _field(f"{where} alternative {i}", {"tag": spec.get("tag"), **alt})
        for i, alt in enumerate(spec.get("alternatives", []), 1)
    )
    f = FieldSpec(
        tag=str(spec["tag"]) if "tag" in spec else None,
        tags=tuple(str(t) for t in spec.get("tags", [])),
        source=spec.get("source"),
        sources=tuple(spec.get("sources", [])),
        format=spec.get("format", "raw"),
        args=spec.get("args", {}),
        check=spec.get("check", "present"),
        when=spec.get("when", {}),
        only_positive=bool(spec.get("only_positive", False)),
        cap=spec.get("cap"),
        max_len=spec.get("max_len"),
        default=spec.get("default"),
        translate=spec.get("translate"),
        required=bool(spec.get("required", False)),
        alternatives=alternatives,
        warn=spec.get("warn"),
    )
    if bool(f.tag) == bool(f.tags):
        raise LayoutError(f"{where}: give either tag or tags")
    for t in (f.tag,) if f.tag else f.tags:
        if not TAG_RE.match(t):
            raise LayoutError(f'{where}: field tag {t!r} must be two digits (quote it in YAML: "01")')
    if f.tags:
        if f.format not in MULTI_FORMATTERS:
            raise LayoutError(f"{where}: tags need a multi formatter ({sorted(MULTI_FORMATTERS)}), not {f.format!r}")
        if not (f.source or f.sources):
            raise LayoutError(f"{where}: give source or sources")
    elif f.alternatives:
        if f.source:
            raise LayoutError(f"{where}: give source or alternatives, not both")
    else:
        if f.format not in VALUE_FORMATTERS:
            raise LayoutError(f"{where}: unknown format {f.format!r} (known: {sorted(VALUE_FORMATTERS)})")
        if f.source is None and f.default is None:
            raise LayoutError(f"{where}: give a source (or a default)")
    if f.check not in CHECKS:
        raise LayoutError(f"{where}: check must be one of {sorted(CHECKS)}")
    for path in (f.source, *f.sources, *f.when):
        if path:
            _check_path(where, path)
    if f.format == "lookup" and not isinstance(f.args.get("map"), dict):
        raise LayoutError(f"{where}: format lookup needs args.map")
    if f.translate is not None:
        if f.tags:
            raise LayoutError(f"{where}: translate works on one value, not tags")
        if f.translate not in canonical_model().code_lists:
            raise LayoutError(f"{where}: translate: unknown canonical code list {f.translate!r}")
    return f


def _segment(where: str, spec: dict) -> SegmentSpec:
    _known(where, spec, {"tag", "seq", "from", "rows", "by", "order_by", "max", "drops", "fields"})
    s = SegmentSpec(
        tag=spec["tag"],
        seq=spec["seq"],
        source=spec["from"],
        rows=spec.get("rows"),
        by=spec.get("by"),
        order_by=tuple(spec.get("order_by", [])),
        max=spec.get("max"),
        drops=bool(spec.get("drops", True)),
        fields=tuple(_field(f"{where} field {i}", f) for i, f in enumerate(spec["fields"], 1)),
    )
    if not re.match(r"^[A-Z]{2}$", s.tag):
        raise LayoutError(f"{where}: segment tag must be two capital letters")
    if s.source == "rows":
        if not (s.rows and s.by in ("party_key", "account_key")):
            raise LayoutError(f"{where}: from: rows needs rows: <entity> and by: party_key | account_key")
    elif s.source not in RECORD_SOURCES:
        raise LayoutError(f"{where}: from must be party, account, holder or rows")
    try:
        s.seq.format(n=1)
    except (KeyError, IndexError, ValueError) as exc:
        raise LayoutError(f"{where}: bad seq {s.seq!r}: {exc}") from None
    tags = [t for f in s.fields for t in ((f.tag,) if f.tag else f.tags)]
    if len(tags) != len(set(tags)):
        raise LayoutError(f"{where}: duplicate field tags")
    return s


def parse_layout(raw: dict) -> Layout:
    _known("layout", raw, {"layout", "encoding", "header", "segments"})
    _known("encoding", raw.get("encoding", {}), set(Encoding.__dataclass_fields__))
    encoding = Encoding(**raw.get("encoding", {}))
    if encoding.segment_tag_style not in ("plain", "tlv"):
        raise LayoutError("encoding.segment_tag_style must be plain or tlv")
    hdr = raw["header"]
    _known("header", hdr, {"length", "fields"})
    header = []
    for i, h in enumerate(hdr["fields"], 1):
        _known(f"header field {i}", h, set(HeaderField.__dataclass_fields__))
        hf = HeaderField(**h)
        if (hf.source is None) == (hf.const is None) and hf.name != "filler":
            raise LayoutError(f"header field {hf.name}: give source or const")
        if hf.source:
            _check_path(f"header field {hf.name}", hf.source)
        if hf.format and hf.format not in VALUE_FORMATTERS:
            raise LayoutError(f"header field {hf.name}: unknown format {hf.format!r}")
        header.append(hf)
    if sum(h.width for h in header) != hdr["length"]:
        raise LayoutError(f"header fields add up to {sum(h.width for h in header)} bytes, not {hdr['length']}")
    segments = tuple(_segment(f"segment {s.get('tag', i)}", s) for i, s in enumerate(raw["segments"], 1))
    if len({s.tag for s in segments}) != len(segments):
        raise LayoutError("duplicate segment tags")
    return Layout(
        name=raw["layout"], encoding=encoding, header_length=hdr["length"], header=tuple(header), segments=segments
    )


@cache
def load_layout(path: Path) -> Layout:
    return parse_layout(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
