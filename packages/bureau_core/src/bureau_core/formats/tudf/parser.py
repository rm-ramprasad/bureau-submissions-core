"""Parse a TUDF file body back into records and segments, using the same layout that wrote it (checks and tests)."""

from __future__ import annotations

from dataclasses import dataclass

from bureau_core.formats.tudf.encoding import EncodingError
from bureau_core.formats.tudf.layout import Layout


@dataclass(frozen=True)
class ParsedSegment:
    tag: str  # PN, ID, ..., ES
    seq: str  # N01, I01, T001, ...
    fields: dict[str, str]


def parse_records(body: str, layout: Layout) -> list[list[ParsedSegment]]:
    """Parse everything between the header and the trailer into records of segments."""
    enc = layout.encoding
    seq_length = {s.tag: s.seq_length for s in layout.segments}
    records, current, i = [], [], 0
    while i < len(body):
        if enc.record_separator and body.startswith(enc.record_separator, i):
            i += len(enc.record_separator)
            continue
        if body.startswith(enc.record_end, i):
            current.append(ParsedSegment(enc.record_end[:2], "", {}))
            records.append(current)
            current, i = [], i + len(enc.record_end)
            continue
        tag = body[i : i + 2]
        if tag not in seq_length:
            raise EncodingError(f"unexpected bytes at {i}: {body[i : i + 10]!r}")
        i += 2
        length = seq_length[tag]
        if enc.segment_tag_style == "tlv":
            length = int(body[i : i + 2])
            i += 2
        seq, i = body[i : i + length], i + length
        fields = {}
        while body[i : i + 2].isdigit():
            ftag, flen = body[i : i + 2], int(body[i + 2 : i + 4])
            fields[ftag] = body[i + 4 : i + 4 + flen]
            i += 4 + flen
        current.append(ParsedSegment(tag, seq, fields))
    if current:
        raise EncodingError("file ends inside a record (no record end)")
    return records
