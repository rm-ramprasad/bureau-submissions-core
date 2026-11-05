"""Reconcile a TUDF file with the Excel form it was made from: every cell, at the place it must be in the TUDF.

1. Column map, derived from the two layouts (types/<type>/excel_layout.yaml and layout.yaml), not written by hand:
   each form column -> TUDF segment, field tag(s) and, for repeating segments, which instance (the ID segment whose
   ID01 is the PAN code; the first e-mail segment ...). A column with no place, or two columns at one place, is an
   error before anything is compared.
2. Each form row is paired with its TUDF record (account number + name). For every column, the value at its TUDF
   place is compared with the cell, after the spec's own formatting (whole rupees, trailing '-' for a credit
   balance, 3-decimal rate, text cut to the field length, address split into lines ...). Differences the spec makes
   (a cut, a cap, an unusable phone left out, a default) are counted as "spec rule", with the rule; anything else is
   a mismatch.
3. The header's member ID / short name, record and segment counts, and control totals are compared as well.

The result is NOT MATCHED if there is any mismatch; `sheets()` gives the Reconciliation / Column map / Sample /
Mismatches sheets that go into the bureau's Excel.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal, InvalidOperation

from bureau_core.formats.excel import excel_layout
from bureau_core.formats.excel.layout import DIRECT
from bureau_core.formats.tudf.encoding import printable
from bureau_core.formats.tudf.layout import FieldSpec, Layout, SegmentSpec
from bureau_core.formats.tudf.parser import parse_records
from bureau_core.text import cell_text, normalise_header
from bureau_core.tudf.convert import _read_form, _translations, _tudf_layout

SAMPLE_RECORDS = 10
MAX_MISMATCHES = 1000
AMOUNT_COLUMNS = ("Current  Balance", "Amt Overdue", "High Credit/Sanctioned Amt")  # control totals


class ReconciliationError(ValueError):
    """The column map cannot be built (a column with no TUDF place, or two columns at one place)."""


@dataclass(frozen=True)
class Place:
    """Where a form column's value is in a TUDF record."""

    header: str
    segment: str  # PN, ID, PT, EC, PA, TL
    tags: tuple[str, ...]  # one tag, or several (name parts, address lines) compared joined
    spec: FieldSpec
    select_tag: str | None = None  # repeating segment: the instance whose <select_tag> is <select_value>
    select_value: str | None = None
    rank: int | None = None  # ... or the n-th instance (1-based) among the form's filled slots
    rank_group: tuple = ()  # the columns of the same slot family (e.g. all e-mail columns)

    @property
    def label(self) -> str:
        where = f"{self.segment} {'+'.join(self.tags)}"
        if self.select_tag:
            where += f" (the {self.segment} whose {self.segment}{self.select_tag} = {self.select_value})"
        elif self.rank:
            where += f" (filled slot {self.rank})"
        return where


@dataclass
class ColumnResult:
    place: Place
    compared: int = 0
    matched: int = 0
    both_empty: int = 0
    spec_rules: Counter = field(default_factory=Counter)
    mismatched: int = 0


@dataclass
class Reconciliation:
    matched: bool
    form_rows: int
    tudf_records: int
    columns: list[ColumnResult]
    mismatches: list[tuple]  # (form row, column, form value, TUDF place, TUDF value, problem)
    checks: list[tuple[str, str, str, bool]]  # (check, form, TUDF, ok)
    segment_counts: dict[str, int]
    samples: list[list[tuple]]  # per sampled record: (column, form value, place, TUDF value)

    def sheets(self) -> dict[str, list[list[str]]]:
        """name -> rows, for the bureau's Excel."""
        summary = [
            ["Result", "MATCHED" if self.matched else "NOT MATCHED"],
            ["Form rows", str(self.form_rows)],
            ["TUDF records", str(self.tudf_records)],
            ["Mismatches", str(sum(c.mismatched for c in self.columns) + sum(1 for c in self.checks if not c[3]))],
            [],
            ["Check", "Form", "TUDF", "OK"],
        ]
        summary += [[c, f, t, "yes" if ok else "NO"] for c, f, t, ok in self.checks]
        summary += [[], ["Segment", "Count in TUDF"]] + [[k, str(v)] for k, v in sorted(self.segment_counts.items())]
        summary += [
            [],
            ["Form column", "TUDF place", "Cells compared", "Matched", "Both empty", "Spec rule", "Mismatched"],
        ]
        for c in self.columns:
            rules = "; ".join(f"{n} x {r}" for r, n in c.spec_rules.items())
            summary.append(
                [
                    c.place.header,
                    c.place.label,
                    str(c.compared),
                    str(c.matched),
                    str(c.both_empty),
                    rules,
                    str(c.mismatched),
                ]
            )
        column_map = [["Form column", "TUDF segment", "TUDF field tag(s)", "Instance", "TUDF format"]]
        for c in self.columns:
            p = c.place
            instance = (
                f"{p.segment}{p.select_tag} = {p.select_value}"
                if p.select_tag
                else f"filled slot {p.rank}"
                if p.rank
                else ""
            )
            column_map.append(
                [
                    p.header,
                    p.segment,
                    ", ".join(p.tags),
                    instance,
                    p.spec.format + (f" (max {p.spec.max_len})" if p.spec.max_len else ""),
                ]
            )
        sample = [["Record", "Form column", "Form value", "TUDF place", "TUDF value"]]
        for i, rec in enumerate(self.samples, 1):
            sample += [[str(i), *map(str, r)] for r in rec] + [[]]
        mism = [["Form row", "Form column", "Form value", "TUDF place", "TUDF value", "Problem"]]
        mism += [[str(x) for x in m] for m in self.mismatches]
        return {"Reconciliation": summary, "Column map": column_map, "Sample": sample, "Mismatches": mism}


# ---------------------------------------------------------------- 1. column map
def _qualified(seg: SegmentSpec, source: str) -> str:
    if "." in source:
        return source
    own = seg.rows if seg.source == "rows" else seg.source
    return f"{own}.{source}"


def column_map(submission_type: str) -> list[Place]:
    layout, tudf = excel_layout(submission_type), _tudf_layout(submission_type)
    forward, _ = _translations(submission_type)
    places, problems, seen = [], [], {}
    slot_families = defaultdict(set)  # (entity, match key) -> match values used by the form
    for c in layout.columns:
        for k, v in c.match.items():
            slot_families[(c.source, k)].add(v)
    for c in layout.columns:
        want = f"{'account_holder' if c.source == 'holder' else c.source}.{c.field}"
        want_alt = f"{c.source}.{c.field}"  # TUDF layouts write holder.x / bureau.x / party.x
        found = None
        for seg in tudf.segments:
            if c.source not in DIRECT and seg.rows != c.source:
                continue
            for spec in seg.fields:
                for alt in (spec, *spec.alternatives):
                    sources = alt.sources or ((alt.source,) if alt.source else ())
                    if any(_qualified(seg, s) in (want, want_alt) for s in sources):
                        tags = alt.tags or spec.tags or ((alt.tag or spec.tag),)
                        if alt.sources and c.field in [s.split(".")[-1] for s in alt.sources]:
                            tags = spec.tags  # address lines: all lines, compared joined
                        found = (seg, spec, tuple(tags))
                        break
                if found:
                    break
            if found:
                break
        if not found:
            problems.append(f"{c.header}: no place in the TUDF layout for {want}")
            continue
        seg, spec, tags = found
        select_tag = select_value = rank = None
        group = ()
        for k, v in c.match.items():
            key_spec = next((f for f in seg.fields if f.source == k), None)
            if key_spec is not None and key_spec.translate:
                select_tag = key_spec.tag
                select_value = forward.get(key_spec.translate, {}).get(str(v), str(v))
            else:  # no field to tell instances apart: by filled-slot order
                rank = sorted(slot_families[(c.source, k)]).index(v) + 1
                group = (c.source, k)
        place = Place(c.header, seg.tag, tags, spec, select_tag, select_value, rank, group)
        key = (seg.tag, tags, select_tag, select_value, rank)
        if key in seen:
            problems.append(f"{c.header} and {seen[key]} are at the same TUDF place {place.label}")
        seen[key] = c.header
        places.append(place)
    if problems:
        raise ReconciliationError("Column map: " + "; ".join(problems))
    return places


# ---------------------------------------------------------------- 2. comparison
def _num(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation, AttributeError:
        return None


def _signed(text: str) -> Decimal | None:
    return -_num(text[:-1]) if text.endswith("-") and _num(text[:-1]) is not None else _num(text)


def compare(place: Place, form: str | None, tudf: str | None, as_of: str) -> tuple[str, str | None]:
    """('match' | 'both_empty' | 'spec' | 'mismatch', rule or problem)."""
    spec, fmt = place.spec, place.spec.format
    if form is None and tudf is None:
        return "both_empty", None
    if form is None:
        if spec.default == "run.as_of_date" and tudf == as_of:
            return "spec", "empty: the as-of date is written"
        if spec.default is not None and spec.default != "run.as_of_date":
            return "spec", f"empty: default {spec.default} is written"
        return "mismatch", "in the TUDF but empty in the form"
    if tudf is None:
        if fmt == "phone_list":
            return "spec", "not a usable phone number: left out"
        if spec.when:
            return "spec", "not written for this account type"
        return "mismatch", "in the form but missing in the TUDF"
    if fmt in ("amount", "signed_amount", "int", "int3"):
        f = _num(form)
        t = _signed(tudf) if fmt == "signed_amount" else _num(tudf)
        if f is None or t is None:
            return "mismatch", "not a number"
        f = f.quantize(Decimal(1)) if fmt != "int3" else f
        if spec.cap is not None and f > spec.cap and t == spec.cap:
            return "spec", f"capped at {spec.cap}"
        return ("match", None) if f == t or abs(f - t) < 1 else ("mismatch", "different amount")
    if fmt == "rate":
        f, t = _num(form), _num(tudf)
        if f is None or t is None:
            return "mismatch", "not a number"
        return ("match", None) if f.quantize(Decimal("0.001"), ROUND_DOWN) == t else ("mismatch", "different rate")
    if fmt == "phone_list":  # digits on both sides: the TUDF keeps e.g. "0674-2734014"
        digits = re.sub(r"\D", "", form)
        written = [re.sub(r"\D", "", n) for n in tudf.split(",")]
        return ("match", None) if digits in written else ("mismatch", "different number")
    if fmt in ("pin_code", "alnum"):
        clean = re.sub(r"[^A-Za-z0-9]", "", form)
        return ("match", None) if clean == tudf else ("mismatch", "different value")
    if fmt == "id_number":
        a, b = re.sub(r"\s", "", form).upper(), re.sub(r"\s", "", tudf).upper()
        return ("match", None) if a == b else ("mismatch", "different number")
    expected = " ".join(printable(form).split())
    got = " ".join(tudf.split())
    if fmt == "lower_text":
        expected = expected.lower()
    if expected == got:
        return "match", None
    if got.replace(" ", "") == expected.replace(" ", ""):
        return "spec", f"split into {len(place.tags)} parts"
    if spec.max_len and expected.startswith(got) and len(got) <= spec.max_len < len(expected):
        return "spec", f"cut to {spec.max_len} characters"
    return "mismatch", "different value"


def header_value(layout: Layout, head: str, source: str) -> str:
    """A header field's text, by its source (fixed-width header: positions from the layout's field widths)."""
    pos = 0
    for f in layout.header:
        if f.source == source:
            return head[pos : pos + f.width].strip()
        pos += f.width
    return ""


def _segments(record) -> dict[str, list]:
    out = defaultdict(list)
    for s in record:
        out[s.tag].append(s)
    return out


def _value_at(place: Place, segs: dict, filled_rank: dict) -> str | None:
    instances = segs.get(place.segment, [])
    if place.select_tag:
        instances = [s for s in instances if s.fields.get(place.select_tag) == place.select_value]
        inst = instances[0] if instances else None
    elif place.rank:
        n = filled_rank.get(place.header)
        inst = instances[n - 1] if n and n <= len(instances) else None
    else:
        inst = instances[0] if instances else None
    if inst is None:
        return None
    parts = [inst.fields[t] for t in place.tags if inst.fields.get(t)]
    return " ".join(parts) if parts else None


def reconcile(excel: bytes, tudf: bytes, submission_type: str = "CONSUMER") -> Reconciliation:
    layout, tudf_layout = excel_layout(submission_type), _tudf_layout(submission_type)
    places = column_map(submission_type)
    header_row, rows = _read_form(excel, layout.sheet)
    position = {normalise_header(h): i for i, h in enumerate(header_row)}
    form_rows = []
    for n, raw in rows:
        cells = {
            p.header: cell_text(raw[position[normalise_header(p.header)]])
            if position[normalise_header(p.header)] < len(raw)
            else None
            for p in places
        }
        if any(v is not None for v in cells.values()):
            form_rows.append((n, cells))

    enc = tudf_layout.encoding
    text = tudf.decode(enc.charset)
    head, body = text[: tudf_layout.header_length], text[tudf_layout.header_length :]
    trailer_ok = body.endswith(enc.trailer)
    records = parse_records(body[: len(body) - len(enc.trailer)] if trailer_ok else body, tudf_layout)

    def key_of_record(rec):
        segs = _segments(rec)
        pn, tl = segs.get("PN", [None])[0], segs.get("TL", [None])[0]
        name = " ".join(pn.fields[t] for t in ("01", "02", "03", "04", "05") if pn and pn.fields.get(t))
        return (tl.fields.get("03") if tl else None, " ".join(name.split()))

    account_header = next(p.header for p in places if p.segment == "TL" and p.tags == ("03",))
    name_header = next(p.header for p in places if p.segment == "PN" and "01" in p.tags)
    pending = defaultdict(deque)
    for rec in records:
        pending[key_of_record(rec)].append(rec)

    results = {p.header: ColumnResult(p) for p in places}
    mismatches, samples, seg_counts = [], [], Counter(s.tag for r in records for s in r)
    as_of = header_value(tudf_layout, head, "run.as_of_date")
    unpaired = 0
    families = defaultdict(list)
    for p in places:
        if p.rank:
            families[p.rank_group].append(p)
    for n, cells in form_rows:
        key = (cells[account_header], " ".join(printable(cells[name_header] or "").split()))
        if not pending[key]:
            unpaired += 1
            mismatches.append((n, account_header, cells[account_header], "TL 03", "", "no TUDF record for this row"))
            continue
        segs = _segments(pending[key].popleft())
        filled_rank = {}
        for group in families.values():  # a slot (e-mail 2, address 1 ...) is filled if any of its
            filled = sorted({p.rank for p in group if cells[p.header] is not None})  # columns has a value; the
            for p in group:  # n-th filled slot is the
                if p.rank in filled:  # n-th segment instance
                    filled_rank[p.header] = filled.index(p.rank) + 1
        sample = []
        for p in places:
            got = _value_at(p, segs, filled_rank)
            outcome, note = compare(p, cells[p.header], got, as_of)
            r = results[p.header]
            r.compared += 1
            if outcome == "match":
                r.matched += 1
            elif outcome == "both_empty":
                r.both_empty += 1
            elif outcome == "spec":
                r.spec_rules[note] += 1
            else:
                r.mismatched += 1
                if len(mismatches) < MAX_MISMATCHES:
                    mismatches.append((n, p.header, cells[p.header] or "", p.label, got or "", note))
            if len(samples) < SAMPLE_RECORDS and (cells[p.header] is not None or got is not None):
                sample.append((p.header, cells[p.header] or "", p.label, got or ""))
        if len(samples) < SAMPLE_RECORDS:
            samples.append(sample)
    leftover = sum(len(q) for q in pending.values())

    members = {
        (c[next(p.header for p in places if p.tags == ("01",) and p.segment == "TL")] or "") for _, c in form_rows
    }
    header_member = header_value(tudf_layout, head, "bureau.member_id")
    checks = [
        (
            "Records: form rows = TUDF records",
            str(len(form_rows)),
            str(len(records)),
            len(form_rows) == len(records) and unpaired == 0 and leftover == 0,
        ),
        ("Member ID in the TUDF header", ", ".join(sorted(members)), header_member, members == {header_member}),
        ("Trailer present", "TRLR", body[-len(enc.trailer) :] if body else "", trailer_ok),
    ]
    for col in AMOUNT_COLUMNS:
        place = next((p for p in places if p.header == col), None)
        if place is None:
            continue
        form_total = sum((_num(c[col]) or Decimal(0)).quantize(Decimal(1)) for _, c in form_rows if c[col])
        tudf_total = Decimal(0)
        for rec in records:
            v = _value_at(place, _segments(rec), {})
            if v:
                tudf_total += (_signed(v) if place.spec.format == "signed_amount" else _num(v)) or 0
        checks.append((f"Total {col.strip()}", str(form_total), str(tudf_total), abs(form_total - tudf_total) < 1))
    matched = not any(r.mismatched for r in results.values()) and all(c[3] for c in checks)
    return Reconciliation(
        matched, len(form_rows), len(records), list(results.values()), mismatches, checks, dict(seg_counts), samples
    )
