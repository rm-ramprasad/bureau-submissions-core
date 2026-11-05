"""Read validation results (for the UI and exports). No writes here."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import Engine, and_, func, select, union

from app.enums import Outcome, RecordOutcome
from app.validation.tables import record_outcome, validation_finding, validation_summary
from bureau_core.validation.rules import ReportHints
from canonical.schema import ENTITY_TABLES

OUTCOME_ORDER = ["REJECT_FILE", "REJECT_RECORD", "REJECT_SEGMENT", "REJECT_FIELD", "WARN"]
EXPORT_COLUMNS = ["rule_id", "outcome", "segment", "field", "account_key", "party_key", "value", "message"]

# What each outcome does to the file, in business terms (the outcomes are the same for every submission type).
EFFECTS = {
    "REJECT_FILE": "Blocks the whole file",
    "REJECT_RECORD": "Not reported",
    "REJECT_SEGMENT": "Partly reported",
    "REJECT_FIELD": "Value dropped",
    "WARN": "Warning only",
}
_GAP_OUTCOMES = (str(Outcome.REJECT_SEGMENT), str(Outcome.REJECT_FIELD))


def _hints(engine: Engine, snapshot_id: int) -> ReportHints:
    from bureau_core.validation.rules import report_hints
    from canonical.snapshot import SnapshotRepository

    return report_hints(SnapshotRepository(engine).get(snapshot_id).submission_type)


@dataclass(frozen=True)
class FindingFilter:
    rule_id: str | None = None
    outcome: str | None = None
    account_key: str | None = None
    party_key: str | None = None
    rule_ids: tuple[str, ...] | None = None  # a category, resolved to its rule IDs by the caller

    def where(self, snapshot_id: int):
        f = validation_finding.c
        clauses = [f.snapshot_id == snapshot_id]
        for name in ("rule_id", "outcome", "account_key", "party_key"):
            value = getattr(self, name)
            if value:
                clauses.append(f[name] == value)
        if self.rule_ids is not None:
            clauses.append(f.rule_id.in_(self.rule_ids))
        return clauses


def summary(engine: Engine, snapshot_id: int) -> list:
    """Findings per rule and outcome, most severe first."""
    s = validation_summary.c
    with engine.connect() as conn:
        rows = conn.execute(select(s.rule_id, s.outcome, s.finding_count).where(s.snapshot_id == snapshot_id)).all()
    rank = {o: i for i, o in enumerate(OUTCOME_ORDER)}
    return sorted(rows, key=lambda r: (rank.get(r.outcome, 99), -r.finding_count, r.rule_id))


def record_counts(engine: Engine, snapshot_id: int) -> dict[str, int]:
    o = record_outcome.c
    with engine.connect() as conn:
        return dict(
            conn.execute(select(o.outcome, func.count()).where(o.snapshot_id == snapshot_id).group_by(o.outcome)).all()
        )


def _finding_records(snapshot_id: int):
    """Distinct (rule_id, outcome, account_key, party_key) per finding and record (account x holder) it applies to.
    A finding with only a party key hits every loan of that borrower; with only an account key, every holder."""
    f, h = validation_finding.c, ENTITY_TABLES["account_holder"].c
    cols = (f.rule_id, f.outcome, h.account_key, h.party_key)
    on = {
        "both": and_(f.account_key == h.account_key, f.party_key == h.party_key),
        "account": and_(f.account_key == h.account_key, f.party_key.is_(None)),
        "party": and_(f.party_key == h.party_key, f.account_key.is_(None)),
    }
    return union(
        *(
            select(*cols)
            .select_from(
                validation_finding.join(ENTITY_TABLES["account_holder"], and_(h.snapshot_id == f.snapshot_id, cond))
            )
            .where(f.snapshot_id == snapshot_id)
            for cond in on.values()
        )
    ).subquery()


EXPECTED_EFFECT = "Spec rule (no action)"


def report(engine: Engine, snapshot_id: int, hints: ReportHints | None = None) -> dict:
    """Findings for business users: record KPIs, rejected records by main reason's category, and per category the
    rules with their message, effect, findings and records affected. Counts are records, not findings.
    Categories come from the snapshot type's rule pack (`hints`, loaded when not given); its `expected` rules (the
    spec's normal handling) are listed last and never count as gaps."""
    hints = hints or _hints(engine, snapshot_id)
    category, expected = hints.category, hints.expected
    o = record_outcome.c
    fr = _finding_records(snapshot_id)
    with engine.connect() as conn:
        totals = dict(
            conn.execute(select(o.outcome, func.count()).where(o.snapshot_id == snapshot_id).group_by(o.outcome)).all()
        )
        reasons = conn.execute(
            select(o.reason_rule_id, func.count())
            .where(o.snapshot_id == snapshot_id, o.outcome == str(RecordOutcome.REJECTED))
            .group_by(o.reason_rule_id)
        ).all()
        hits = conn.execute(
            select(fr.c.rule_id, fr.c.outcome, fr.c.account_key, fr.c.party_key, o.outcome.label("rec")).select_from(
                fr.join(
                    record_outcome,
                    and_(
                        o.snapshot_id == snapshot_id, o.account_key == fr.c.account_key, o.party_key == fr.c.party_key
                    ),
                )
            )
        ).all()
        f = validation_finding.c
        rules = conn.execute(
            select(
                f.rule_id,
                f.outcome,
                func.min(f.segment).label("segment"),
                func.min(f.message).label("message"),
                func.count().label("findings"),
            )
            .where(f.snapshot_id == snapshot_id)
            .group_by(f.rule_id, f.outcome)
        ).all()

    records = defaultdict(set)  # (rule_id, outcome) -> records it hits
    gaps, gaps_by_cat = set(), defaultdict(set)
    for x in hits:
        key = (x.account_key, x.party_key)
        records[(x.rule_id, x.outcome)].add(key)
        if x.outcome in _GAP_OUTCOMES and x.rec == str(RecordOutcome.ACCEPTED) and x.rule_id not in expected:
            gaps.add(key)
            gaps_by_cat[category(x.rule_id)].add(key)
    not_reported = defaultdict(int)
    for rule_id, n in reasons:
        not_reported[category(rule_id or "")] += n

    rank = {x: i for i, x in enumerate(OUTCOME_ORDER)}
    by_cat = defaultdict(list)
    for r in rules:
        is_expected = r.rule_id in expected and r.outcome in (*_GAP_OUTCOMES, str(Outcome.WARN))
        by_cat[category(r.rule_id)].append(
            dict(
                rule_id=r.rule_id,
                outcome=r.outcome,
                message=r.message,
                expected=is_expected,
                effect=EXPECTED_EFFECT if is_expected else EFFECTS.get(r.outcome, r.outcome),
                findings=r.findings,
                records=len(records[(r.rule_id, r.outcome)]),
            )
        )
    categories = [
        dict(
            key=k,
            label=label,
            not_reported=not_reported.get(k, 0),
            with_gaps=len(gaps_by_cat[k]),
            rules=sorted(
                by_cat[k], key=lambda r: (r["expected"], rank.get(r["outcome"], 99), -r["records"], r["rule_id"])
            ),
        )
        for k, label in hints.categories.items()
        if by_cat[k]
    ]
    accepted, rejected = totals.get(str(RecordOutcome.ACCEPTED), 0), totals.get(str(RecordOutcome.REJECTED), 0)
    return dict(
        kpis=dict(records=accepted + rejected, reported=accepted, not_reported=rejected, with_gaps=len(gaps)),
        not_reported_by_category=[
            dict(key=c["key"], label=c["label"], records=c["not_reported"])
            for c in sorted(categories, key=lambda c: -c["not_reported"])
            if c["not_reported"]
        ],
        categories=categories,
        effects=EFFECTS,
        expected_rules=sorted(expected),
    )


def category_rule_ids(engine: Engine, snapshot_id: int, key: str, hints: ReportHints | None = None) -> tuple:
    """The rule IDs of this snapshot's findings that fall in one category (for the Findings page filter)."""
    hints = hints or _hints(engine, snapshot_id)
    f = validation_finding.c
    with engine.connect() as conn:
        rows = conn.execute(select(f.rule_id).where(f.snapshot_id == snapshot_id).distinct()).scalars().all()
    return tuple(r for r in rows if hints.category(r) == key)


def findings(
    engine: Engine, snapshot_id: int, flt: FindingFilter, limit: int = 200, offset: int = 0
) -> tuple[list, int]:
    f = validation_finding.c
    with engine.connect() as conn:
        total = conn.execute(
            select(func.count()).select_from(validation_finding).where(*flt.where(snapshot_id))
        ).scalar()
        rows = conn.execute(
            select(validation_finding)
            .where(*flt.where(snapshot_id))
            .order_by(f.account_key, f.party_key, f.rule_id, f.finding_id)
            .limit(limit)
            .offset(offset)
        ).all()
    return rows, total


def findings_csv(engine: Engine, snapshot_id: int, flt: FindingFilter) -> Iterator[str]:
    """CSV export, streamed in chunks."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(EXPORT_COLUMNS)
    yield buf.getvalue()
    f = validation_finding.c
    with engine.connect() as conn:
        result = conn.execution_options(stream_results=True).execute(
            select(*(f[c] for c in EXPORT_COLUMNS))
            .where(*flt.where(snapshot_id))
            .order_by(f.account_key, f.party_key, f.rule_id, f.finding_id)
        )
        for chunk in result.partitions(1000):
            buf.seek(0)
            buf.truncate()
            writer.writerows(chunk)
            yield buf.getvalue()
