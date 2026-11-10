"""Generic delimited writer: renders a validated snapshot into a file as described by a layout (layout.py).

Only ACCEPTED records are written (record_outcome), grouped by borrower: the group line (e.g. BS) and its child
lines (AS, RS) once per borrower (and split value, e.g. branch), then each accepted record's line (CR) and its child
lines (GS, SS, CD). Validation drops apply: REJECT_FIELD empties the field, REJECT_SEGMENT leaves the row out. Values
are canonical; `translate` writes the file code from the snapshot's frozen translations.
"""

from __future__ import annotations

import functools
from collections import Counter
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from bureau_core.bureau import BureauProfile
from bureau_core.checks.phones import valid_numbers
from bureau_core.data import SnapshotData
from bureau_core.formats.delimited.formatters import VALUE_FORMATTERS
from bureau_core.formats.delimited.layout import Field, FileSpec, Layout, LineLayout, Segment
from bureau_core.formats.tudf.engine import FileStats, _compare

ENTITY_OF = {"party": "party", "account": "account"}
MOBILE = "MOBILE"  # canonical phone_type: mobile numbers vs other telephones


@dataclass
class _Context:
    """What the source paths of one line resolve against."""

    row: object
    entity: str | None
    party: object
    account: object
    account_key: str | None


class Renderer:
    def __init__(
        self,
        layout: Layout | LineLayout,
        data: SnapshotData,
        bureau: BureauProfile,
        run: SimpleNamespace,
        translations: dict[str, dict[str, str]],
    ):
        self.layout, self.data, self.bureau, self.run, self.translations = layout, data, bureau, run, translations
        self.counts: Counter = Counter()
        self.file_key: str | None = None  # line layouts: the file being written (file.key)
        enc = layout.encoding
        self._strip = str.maketrans("", "", enc.strip_chars + enc.delimiter + "\r\n")

    # ---------------------------------------------------------------- values
    def _resolve(self, path: str, ctx: _Context) -> tuple[object, str | None, object]:
        """(value, entity, row) for a source path; entity / row are used for validation drops."""
        head, _, rest = path.partition(".")
        if not rest:  # the line's own row; none (e.g. an account without its group) gives empty values
            return (getattr(ctx.row, path), ctx.entity, ctx.row) if ctx.row is not None else (None, None, None)
        if head in ENTITY_OF:
            obj = getattr(ctx, head)
            return (getattr(obj, rest), ENTITY_OF[head], obj) if obj is not None else (None, None, None)
        if head == "id":
            return self._identifier(ctx, rest), None, None
        if head == "contacts":
            return self._contacts(ctx, rest), None, None
        if head == "count":
            return self.counts[rest], None, None
        if head == "address":  # address.<CATEGORY>.<col>
            category, _, column = rest.partition(".")
            row = self._first_child(ctx, "party_address", lambda r: r.address_category == category)
            return (getattr(row, column), "party_address", row) if row is not None else (None, None, None)
        if head == "relative":  # relative.<ROLE>.<n>.<col>
            role, _, more = rest.partition(".")
            n, _, column = more.partition(".")
            rows = self._sorted_children(ctx, "party_relative", lambda r: r.relative_role == role)
            row = rows[int(n) - 1] if len(rows) >= int(n) else None
            return (getattr(row, column), "party_relative", row) if row is not None else (None, None, None)
        if head == "group":  # group.<col>: the account's borrower group
            row = self.group_of(ctx.account)
            return (getattr(row, rest), "borrower_group", row) if row is not None else (None, None, None)
        if head == "file":
            return (self.file_key if rest == "key" else None), None, None
        return getattr(self.bureau if head == "bureau" else self.run, rest), None, None

    def _children(self, entity: str, key: str, account_key: str | None) -> list:
        rows = self.data.children.get(entity, {}).get(key, [])
        return [r for r in rows if not self.data.drops.segment_dropped(entity, r.row_id, account_key)]

    def _sorted_children(self, ctx: _Context, entity: str, keep) -> list:
        rows = [r for r in self._children(entity, ctx.party.party_key, ctx.account_key) if keep(r)]
        return sorted(
            rows, key=lambda r: (getattr(r, "priority", None) is None, getattr(r, "priority", None), r.row_id)
        )

    def _first_child(self, ctx: _Context, entity: str, keep):
        rows = self._sorted_children(ctx, entity, keep)
        return rows[0] if rows else None

    def group_of(self, account) -> object | None:
        """The account's borrower group (MFI), by account.group_key."""
        key = getattr(account, "group_key", None) if account is not None else None
        rows = self.data.children.get("borrower_group", {}).get(key, []) if key else []
        return rows[0] if rows else None

    def _identifier(self, ctx: _Context, id_type: str) -> str | None:
        for r in self._children("party_identifier", ctx.party.party_key, ctx.account_key):
            if r.id_type == id_type and r.id_number:
                return r.id_number
        return None

    def _contacts(self, ctx: _Context, kind: str) -> str | None:
        key = ctx.party.party_key
        if kind == "email":
            emails = sorted(
                self._children("party_email", key, ctx.account_key),
                key=lambda r: (r.priority is None, r.priority, r.row_id),
            )
            return emails[0].email if emails else None
        numbers = []
        for r in sorted(
            self._children("party_phone", key, ctx.account_key),
            key=lambda r: (r.priority is None, r.priority, r.row_id),
        ):
            if (r.phone_type == MOBILE) == (kind == "mobile"):
                numbers += [n for n in valid_numbers(r.phone_number, r.phone_type) if n not in numbers]
        return ",".join(numbers) or None

    def _present(self, spec: Field, value, entity, obj, column: str, ctx: _Context) -> bool:
        if value is None:
            return False
        if spec.check in ("none", "not_null"):
            return True
        if str(value).strip() == "":
            return False
        return not (
            entity
            and getattr(obj, "row_id", None) is not None
            and self.data.drops.field_dropped(entity, obj.row_id, column, ctx.account_key)
        )

    def _value(self, spec: Field, ctx: _Context) -> str | None:
        if spec.blank:
            return None
        if spec.alternatives:
            for alt in spec.alternatives:
                text = self._value(alt, ctx)
                if text is not None:
                    return text
            return None
        for path, allowed in spec.when.items():
            if str(self._resolve(path, ctx)[0]) not in {str(a) for a in allowed}:
                return None
        if spec.if_present and self._resolve(spec.if_present, ctx)[0] in (None, ""):
            return None
        if spec.const is not None:
            return spec.const
        value, entity, obj = self._resolve(spec.source, ctx)
        if not self._present(spec, value, entity, obj, spec.source.rpartition(".")[2], ctx):
            if spec.default is None:
                return None
            value = spec.default
        if spec.translate:  # no active file code: validation flagged it; left empty
            value = self.translations.get(spec.translate, {}).get(str(value))
            if value is None:
                return None
        if spec.cap is not None:
            value = min(int(value), spec.cap)
        text = VALUE_FORMATTERS[spec.format](value, ctx.row, spec.args)
        if text is None:
            return None
        text = text.translate(self._strip)
        return text[: spec.max_len] if spec.max_len else text

    # ---------------------------------------------------------------- lines
    def fixed_line(self, seg: Segment, ctx: _Context) -> str:
        """A fixed-width line (MFI header / trailer): every field cut or padded to its width, no delimiter."""
        parts = [seg.tag.ljust(seg.tag_width or len(seg.tag))]
        for f in seg.fields:
            text = (self._value(f, ctx) or "")[: f.width]
            parts.append(text.ljust(f.width or 0, f.pad) if f.align == "left" else text.rjust(f.width or 0, f.pad))
        self.counts[seg.tag] += 1
        return "".join(parts) + self.layout.encoding.line_end

    def values(self, seg: Segment, ctx: _Context) -> list[str]:
        """One segment's fields on a joined line (tag first)."""
        self.counts[seg.tag] += 1
        return [seg.tag] + [self._value(f, ctx) or "" for f in seg.fields]

    def line(self, seg: Segment, ctx: _Context) -> str:
        if seg.fixed:
            return self.fixed_line(seg, ctx)
        enc = self.layout.encoding
        values = [seg.tag] + [self._value(f, ctx) or "" for f in seg.fields]
        self.counts[seg.tag] += 1
        return enc.delimiter.join(values) + (enc.delimiter if enc.trailing_delimiter else "") + enc.line_end

    def child_lines(self, seg: Segment, party, account, account_key: str | None) -> list[str]:
        out = []
        for child in seg.children:
            key = account_key if child.by == "account_key" else party.party_key
            rows = list(self.data.children.get(child.rows, {}).get(key, []))
            if child.drops:
                rows = [r for r in rows if not self.data.drops.segment_dropped(child.rows, r.row_id, account_key)]
            if child.order_by:
                rows.sort(key=functools.cmp_to_key(lambda a, b, order=child.order_by: _compare(a, b, order)))
            for row in rows[: child.max] if child.max is not None else rows:
                out.append(self.line(child, _Context(row, child.rows, party, account, account_key)))
        return out


def render_file(
    data: SnapshotData,
    as_of_date: date,
    bureau: BureauProfile,
    cycle,
    layout: Layout,
    translations: dict[str, dict[str, str]],
    created_date: date,
) -> tuple[bytes, FileStats]:
    """The file for records already in memory (no database): from a snapshot (build_file) or a capture."""
    run = SimpleNamespace(as_of_date=as_of_date, created_date=created_date, cycle=str(cycle) if cycle else None)
    r = Renderer(layout, data, bureau, run, translations)

    groups: dict[tuple, list[str]] = {}  # (party_key, split value) -> account keys, in order
    for account_key, party_key in sorted(data.accepted, key=lambda x: (x[1], x[0])):
        account = data.accounts[account_key]
        split = None
        if layout.group_split:
            split = r._resolve(
                layout.group_split, _Context(account, "account", data.parties[party_key], account, account_key)
            )[0]
        groups.setdefault((party_key, split), []).append(account_key)

    body = []
    for (party_key, _), account_keys in groups.items():
        party, first = data.parties[party_key], data.accounts[account_keys[0]]
        body.append(r.line(layout.group, _Context(party, "party", party, first, account_keys[0])))
        body += r.child_lines(layout.group, party, first, account_keys[0])
        for account_key in account_keys:
            account = data.accounts[account_key]
            body.append(r.line(layout.record, _Context(account, "account", party, account, account_key)))
            body += r.child_lines(layout.record, party, account, account_key)
    nobody = _Context(None, None, None, None, None)
    header = r.line(layout.header, nobody)
    trailer = r.line(layout.trailer, nobody)
    content = header + "".join(body) + trailer
    written = r.counts[layout.record.tag]
    stats = FileStats(len(data.accepted) + data.rejected, written, data.rejected, 0, [])
    return content.encode(layout.encoding.charset), stats


def render_files(
    data: SnapshotData,
    as_of_date: date,
    bureau: BureauProfile,
    cycle,
    layout: LineLayout,
    translations: dict[str, dict[str, str]],
    created_date: date,
) -> dict[str, tuple[bytes, FileStats]]:
    """The files of a line layout (ADR 0003): per file, the accepted records its `select` keeps, one line each with
    the `record_line` segments joined, between the header and the trailer. Files with no record are left out when
    `skip_empty`. Stats per file: records selected, lines written, identical lines dropped."""
    run = SimpleNamespace(as_of_date=as_of_date, created_date=created_date, cycle=str(cycle) if cycle else None)
    out: dict[str, tuple[bytes, FileStats]] = {}
    for spec in layout.files:
        content, stats = _render_one(data, run, bureau, layout, translations, spec)
        if stats.total_records or not spec.skip_empty:
            out[spec.key] = (content, stats)
    return out


def _selected(r: Renderer, spec: FileSpec, ctx: _Context) -> bool:
    return all(str(r._resolve(path, ctx)[0]) in allowed for path, allowed in spec.select.items())


def _render_one(data, run, bureau, layout: LineLayout, translations, spec: FileSpec) -> tuple[bytes, FileStats]:
    r = Renderer(layout, data, bureau, run, translations)
    r.file_key = spec.key
    enc = layout.encoding
    lines: list[str] = []
    seen: set[str] = set()
    selected = duplicates = 0
    for account_key, party_key in sorted(data.accepted, key=lambda x: (x[1], x[0])):
        party, account = data.parties[party_key], data.accounts[account_key]
        ctx = _Context(account, "account", party, account, account_key)
        if not _selected(r, spec, ctx):
            continue
        selected += 1
        values: list[str] = []
        for seg in spec.record_line:
            row, entity = {
                "party": (party, "party"),
                "account": (account, "account"),
                "group": (r.group_of(account), "borrower_group"),
            }[seg.source or "account"]
            values += r.values(seg, _Context(row, entity, party, account, account_key))
        assert len(values) == spec.columns, f"{spec.key}: {len(values)} columns, the layout declares {spec.columns}"
        text = enc.delimiter.join(values) + (enc.delimiter if enc.trailing_delimiter else "") + enc.line_end
        if spec.dedupe and text in seen:
            duplicates += 1
            continue
        seen.add(text)
        lines.append(text)
    nobody = _Context(None, None, None, None, None)
    content = r.line(layout.header, nobody) + "".join(lines) + r.line(layout.trailer, nobody)
    return content.encode(enc.charset), FileStats(selected, len(lines), 0, duplicates, [])
