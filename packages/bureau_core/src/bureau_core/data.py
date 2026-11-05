"""What the format engines render from: the records of one validated batch and the validation drops."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class Drops:
    """Field and segment drops from validation findings, with their account restriction (None = all records)."""

    fields: dict[tuple[str, int, str], list[str | None]] = field(default_factory=lambda: defaultdict(list))
    segments: dict[tuple[str, int], list[str | None]] = field(default_factory=lambda: defaultdict(list))

    def field_dropped(self, entity: str, row_id: int, column: str, account_key: str) -> bool:
        return any(a is None or a == account_key for a in self.fields.get((entity, row_id, column), []))

    def segment_dropped(self, entity: str, row_id: int, account_key: str) -> bool:
        return any(a is None or a == account_key for a in self.segments.get((entity, row_id), []))


@dataclass
class SnapshotData:
    parties: dict[str, object]
    accounts: dict[str, object]
    holders: dict[tuple[str, str], object]
    children: dict[str, dict[str, list]]  # entity -> party_key / account_key -> rows (in row_id order)
    accepted: list[tuple[str, str]]  # (account_key, party_key), one record each
    rejected: int
    drops: Drops
