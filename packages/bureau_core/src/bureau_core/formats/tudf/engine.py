"""Generic TUDF writer: renders a validated snapshot into a file as described by a layout (layout.py).

File = header (fixed width) + records + trailer. A record (one account x one holder) is the layout's segments in
order; repeating segments are numbered from 1 in the order written. Byte-identical duplicate records are counted
and left out (spec §1.4).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from bureau_core.bureau import BureauProfile
from bureau_core.data import SnapshotData
from bureau_core.formats.tudf.encoding import fixed, tlv
from bureau_core.formats.tudf.formatters import MULTI_FORMATTERS, VALUE_FORMATTERS
from bureau_core.formats.tudf.layout import RECORD_SOURCES, FieldSpec, Layout, SegmentSpec

SOURCE_PATHS = ("party.", "account.", "holder.", "bureau.", "run.")


@dataclass
class FileStats:
    total_records: int  # accepted + rejected records in the snapshot
    written: int
    rejected: int
    duplicates: int
    truncated: list[str] = field(default_factory=list)  # text cut to fit (e.g. a name or address too long)


@dataclass
class _Record:
    account_key: str
    party: object
    account: object
    holder: object


class Renderer:
    def __init__(
        self,
        layout: Layout,
        data: SnapshotData,
        bureau: BureauProfile,
        run: SimpleNamespace,
        translations: dict[str, dict[str, str]] | None = None,
    ):
        self.layout, self.data, self.bureau, self.run = layout, data, bureau, run
        self.translations = translations or {}  # code list -> canonical value -> file code (active only)
        self.truncated: list[str] = []

    # ---------------------------------------------------------------- values
    def _resolve(self, path: str, row, row_entity: str | None, rec: _Record):
        """(value, entity, row object) for a source path; entity/row are used for validation drops."""
        head, _, column = path.partition(".")
        if not column:
            return getattr(row, path), row_entity, row
        if head in RECORD_SOURCES:
            obj = getattr(rec, head)
            return getattr(obj, column), RECORD_SOURCES[head], obj
        return getattr(self.bureau if head == "bureau" else self.run, column), None, None

    def _present(self, spec: FieldSpec, value, entity, obj, column: str, rec: _Record) -> bool:
        """A missing value (NULL) is never written. check none: no other check; not_null: nothing more;
        present: also not empty and not dropped by validation (REJECT_FIELD)."""
        if value is None:
            return False
        if spec.check in ("none", "not_null"):
            return True
        if str(value).strip() == "":
            return False
        return not (
            entity
            and getattr(obj, "row_id", None) is not None
            and self.data.drops.field_dropped(entity, obj.row_id, column, rec.account_key)
        )

    def _value(self, spec: FieldSpec, row, entity: str, rec: _Record) -> str | None:
        for path, allowed in spec.when.items():
            if str(self._resolve(path, row, entity, rec)[0]) not in {str(a) for a in allowed}:
                return None
        value, ok = None, False
        if spec.source:
            value, src_entity, obj = self._resolve(spec.source, row, entity, rec)
            ok = self._present(spec, value, src_entity, obj, spec.source.rpartition(".")[2], rec)
            if ok and spec.only_positive and not value > 0:
                ok = False
        if not ok:
            if spec.default is None:
                return None
            value = spec.default
            if isinstance(value, str) and value.startswith(SOURCE_PATHS):
                value = self._resolve(value, row, entity, rec)[0]
        if spec.translate:  # no active file code: validation flags it; left out here
            value = self.translations.get(spec.translate, {}).get(str(value))
            if value is None:
                return None
        if spec.cap is not None:
            value = min(int(value), spec.cap)
        text = VALUE_FORMATTERS[spec.format](value, row, spec.args)
        if text is None:
            return None
        return text[: spec.max_len] if spec.max_len else text

    def _field(self, spec: FieldSpec, row, entity: str, rec: _Record) -> list[tuple[str, str]]:
        if spec.tags:
            values = [self._resolve(p, row, entity, rec)[0] for p in (spec.sources or (spec.source,))]
            args = {"max_parts": len(spec.tags), **spec.args}
            parts, overflowed = MULTI_FORMATTERS[spec.format](values, args)
            if overflowed and spec.warn:
                self.truncated.append(spec.warn.format(row=row, party=rec.party, account=rec.account))
            return list(zip(spec.tags, parts, strict=False))
        if spec.alternatives:
            for alt in spec.alternatives:
                text = self._value(alt, row, entity, rec)
                if text is not None:
                    return [(spec.tag, text)]
            return []
        text = self._value(spec, row, entity, rec)
        return [] if text is None else [(spec.tag, text)]

    # ---------------------------------------------------------------- segments and records
    def _rows(self, seg: SegmentSpec, rec: _Record) -> tuple[list, str]:
        if seg.source != "rows":
            return [getattr(rec, seg.source)], RECORD_SOURCES[seg.source]
        key = rec.account_key if seg.by == "account_key" else rec.party.party_key
        rows = list(self.data.children[seg.rows].get(key, []))
        if seg.drops:
            rows = [r for r in rows if not self.data.drops.segment_dropped(seg.rows, r.row_id, rec.account_key)]
        if seg.order_by:
            rows.sort(key=functools.cmp_to_key(lambda a, b: _compare(a, b, seg.order_by)))
        return rows[: seg.max] if seg.max is not None else rows, seg.rows

    def _segment_header(self, tag: str, seq: str) -> str:
        return tlv(tag, seq) if self.layout.encoding.segment_tag_style == "tlv" else f"{tag}{seq}"

    def record(self, account_key: str, party_key: str) -> str:
        rec = _Record(
            account_key,
            self.data.parties[party_key],
            self.data.accounts[account_key],
            self.data.holders[(account_key, party_key)],
        )
        out = []
        for seg in self.layout.segments:
            rows, entity = self._rows(seg, rec)
            n = 0
            for row in rows:
                fields, skip = [], False
                for spec in seg.fields:
                    rendered = self._field(spec, row, entity, rec)
                    if spec.required and not rendered:
                        skip = True
                        break
                    fields += rendered
                if skip:
                    continue
                n += 1
                out.append(self._segment_header(seg.tag, seg.seq.format(n=n)) + "".join(tlv(t, v) for t, v in fields))
        out.append(self.layout.encoding.record_end)
        return "".join(out)

    def header(self) -> str:
        parts = []
        for h in self.layout.header:
            value = (
                h.const if h.const is not None else (self._resolve(h.source, None, None, None)[0] if h.source else None)
            )
            if value is None or value == "":
                value = h.default
            elif h.format:
                value = VALUE_FORMATTERS[h.format](value, None, {})
            parts.append(fixed(str(value), h.width, numeric=h.pad == "zero"))
        return "".join(parts)


def _compare(a, b, order_by: tuple[str, ...]) -> int:
    """Row order by the layout's order_by: "-col" descending; NULL always last."""
    for spec in order_by:
        desc, col = spec.startswith("-"), spec.lstrip("-")
        x, y = getattr(a, col), getattr(b, col)
        if x == y:
            continue
        if x is None or y is None:
            return 1 if x is None else -1
        return (-1 if x > y else 1) if desc else (-1 if x < y else 1)
    return 0


def render_file(
    data: SnapshotData,
    as_of_date: date,
    bureau: BureauProfile,
    cycle,
    layout: Layout,
    translations: dict[str, dict[str, str]],
) -> tuple[bytes, FileStats]:
    """The file for records already in memory (no database): from a snapshot (build_file) or from a filled-in
    form (bureau_core.tudf, the standalone Excel -> TUDF converter)."""
    renderer = Renderer(layout, data, bureau, SimpleNamespace(as_of_date=as_of_date, cycle=cycle), translations)
    seen, records, duplicates = set(), [], 0
    for account_key, party_key in data.accepted:
        rec = renderer.record(account_key, party_key)
        if rec in seen:  # byte-identical duplicate (§1.4): isolated, not written
            duplicates += 1
            continue
        seen.add(rec)
        records.append(rec)
    enc = layout.encoding
    sep = enc.record_separator
    content = renderer.header() + sep + sep.join(records) + (sep if records else "") + enc.trailer
    stats = FileStats(len(data.accepted) + data.rejected, len(records), data.rejected, duplicates, renderer.truncated)
    return content.encode(enc.charset), stats
