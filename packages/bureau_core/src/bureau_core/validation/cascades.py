"""Record decisions (cascades): after all rules ran, decide ACCEPTED / REJECTED per record and write record_outcome.

Each rule pack names its cascade (`cascade: {name, settings}`); the rule IDs, code values and parameter names a
cascade uses come from those settings, never from here. A new submission type with a different record structure
adds a function to CASCADES.

Signature: (conn, snapshot_id, rules by id, rule parameters, settings) -> (accepted, rejected).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date

from sqlalchemy import Connection, insert, select

from bureau_core.enums import Outcome, RecordOutcome
from bureau_core.validation.tables import (
    ENTITY_TABLES,
    extract_snapshot,
    record_outcome,
    ref_code_group,
    validation_finding,
)

SEGMENT_OUTCOMES = {Outcome.REJECT_SEGMENT}
RECORD_OUTCOMES = {Outcome.REJECT_RECORD}


def applies(f, account_key: str, party_key: str) -> bool:
    """Does a finding hit this record? A finding with only one key hits every record with that key."""
    if f.account_key is None and f.party_key is None:
        return False
    return f.account_key in (None, account_key) and f.party_key in (None, party_key)


class FindingIndex:
    """Record-level findings indexed by account and by party, so finding which hit one record does not scan them all
    (1 lakh records x every finding grew with the square of the data). The order is the findings' own, as before:
    the first one is the record's reason."""

    def __init__(self, findings: list) -> None:
        self.findings = findings
        self.by_account: dict[str, list[int]] = defaultdict(list)
        self.by_party: dict[str, list[int]] = defaultdict(list)
        for i, f in enumerate(findings):
            if f.account_key is not None:
                self.by_account[f.account_key].append(i)
            elif f.party_key is not None:  # account-less: hits every record of the party
                self.by_party[f.party_key].append(i)

    def reasons(self, account_key: str, party_key: str) -> list[str]:
        """The rule ids of the findings that hit this record (as `applies`), in the findings' order."""
        hits = [i for i in self.by_account.get(account_key, ()) if applies(self.findings[i], account_key, party_key)]
        hits += self.by_party.get(party_key, ())
        return [self.findings[i].rule_id for i in sorted(hits)]


def _rows(conn: Connection, snapshot_id: int, entity: str, *cols):
    t = ENTITY_TABLES[entity]
    return conn.execute(select(*(t.c[c] for c in cols)).where(t.c.snapshot_id == snapshot_id)).all()


def account_holder_minimums(
    conn: Connection, snapshot_id: int, rules: dict, parameters: dict, settings: dict
) -> tuple[int, int]:
    """One record = one account x holder (TUDF consumer). Rejected by any REJECT_RECORD finding on it, or when the
    minimums fail once segment rejects are applied: a valid address; a valid phone or a qualifying ID (accounts
    opened on/after a parameter date); a single-word name needs a valid ID or a mobile number."""
    single_word_finding = settings["single_word_name_finding"]
    qualifying_ids, mobile = set(settings["qualifying_id_types"]), settings["mobile_phone_type"]
    required_from = parameters[settings["phone_or_id_from_parameter"]]

    opened = {r.account_key: r.date_opened for r in _rows(conn, snapshot_id, "account", "account_key", "date_opened")}
    phones, ids, addresses = defaultdict(list), defaultdict(list), defaultdict(list)
    for r in _rows(conn, snapshot_id, "party_phone", "row_id", "party_key", "phone_type"):
        phones[r.party_key].append(r)
    for r in _rows(conn, snapshot_id, "party_identifier", "row_id", "party_key", "id_type"):
        ids[r.party_key].append(r)
    for r in _rows(conn, snapshot_id, "party_address", "row_id", "party_key"):
        addresses[r.party_key].append(r)

    f = validation_finding.c
    findings = conn.execute(
        select(f.rule_id, f.entity, f.row_id, f.account_key, f.party_key, f.outcome).where(
            f.snapshot_id == snapshot_id,
            f.outcome.in_([str(o) for o in SEGMENT_OUTCOMES | RECORD_OUTCOMES]) | (f.rule_id == single_word_finding),
        )
    ).all()
    record_rejects = [x for x in findings if x.outcome in RECORD_OUTCOMES]
    record_index = FindingIndex(record_rejects)
    segment_rejects = defaultdict(list)  # (entity, row_id) -> account restriction (None = all)
    for x in findings:
        if x.outcome in SEGMENT_OUTCOMES and x.row_id is not None:
            segment_rejects[(x.entity, x.row_id)].append(x.account_key)
    single_word = {x.party_key for x in findings if x.rule_id == single_word_finding}

    holders = {}
    for r in _rows(conn, snapshot_id, "account_holder", "row_id", "account_key", "party_key"):
        holders.setdefault((r.account_key, r.party_key), r.row_id)

    def enabled(rule_id):
        rule = rules.get(rule_id)
        return rule if rule is not None and rule.enabled else None

    outcomes, cascade_findings, accepted = [], [], 0
    for (account_key, party_key), holder_row in holders.items():

        def ok(entity, row_id):
            return not any(a is None or a == account_key for a in segment_rejects.get((entity, row_id), []))  # noqa: B023  (called in this iteration only)

        reasons = record_index.reasons(account_key, party_key)
        valid_ids = [i for i in ids[party_key] if ok("party_identifier", i.row_id)]
        valid_phones = [p for p in phones[party_key] if ok("party_phone", p.row_id)]
        checks = [
            (settings["no_valid_address_rule"], not any(ok("party_address", a.row_id) for a in addresses[party_key])),
            (
                settings["no_phone_or_id_rule"],
                (opened.get(account_key) or date.min) >= required_from
                and not valid_phones
                and not any(i.id_type in qualifying_ids for i in valid_ids),
            ),
            (
                settings["single_word_name_rule"],
                party_key in single_word and not valid_ids and not any(p.phone_type == mobile for p in valid_phones),
            ),
        ]
        for rule_id, failed in checks:
            rule = enabled(rule_id)
            if failed and rule:
                cascade_findings.append(
                    dict(
                        snapshot_id=snapshot_id,
                        rule_id=rule_id,
                        entity=rule.entity,
                        row_id=holder_row,
                        account_key=account_key,
                        party_key=party_key,
                        segment=rule.segment,
                        field=rule.field,
                        outcome=str(rule.outcome),
                        value=None,
                        message=rule.message[:500],
                    )
                )
                if rule.outcome in RECORD_OUTCOMES:
                    reasons.append(rule_id)
        rejected = bool(reasons)
        accepted += not rejected
        outcomes.append(
            dict(
                snapshot_id=snapshot_id,
                account_key=account_key,
                party_key=party_key,
                outcome=str(RecordOutcome.REJECTED if rejected else RecordOutcome.ACCEPTED),
                reason_rule_id=reasons[0] if reasons else None,
            )
        )
    if cascade_findings:
        conn.execute(insert(validation_finding), cascade_findings)
    if outcomes:
        conn.execute(insert(record_outcome), outcomes)
    return accepted, len(outcomes) - accepted


def facility_borrower_minimums(
    conn: Connection, snapshot_id: int, rules: dict, parameters: dict, settings: dict
) -> tuple[int, int]:
    """One record = one credit facility x its borrower (commercial). Rejected by any REJECT_RECORD finding on it (a
    finding on the borrower alone rejects all its facilities), or when the borrower, once segment rejects are
    applied, has no valid identifier, no valid address, or no valid relationship. A relationship is not needed for
    facilities whose credit type is in the type's `relationship_optional_group` (ref_code_group)."""
    snap = conn.execute(
        select(extract_snapshot.c.submission_type).where(extract_snapshot.c.snapshot_id == snapshot_id)
    ).scalar_one()
    g = ref_code_group.c
    optional_types = set(
        conn.execute(
            select(g.code).where(g.submission_type == snap, g.group_name == settings["relationship_optional_group"])
        ).scalars()
    )
    account_type = {
        r.account_key: r.account_type for r in _rows(conn, snapshot_id, "account", "account_key", "account_type")
    }
    children = {e: defaultdict(list) for e in ("party_identifier", "party_address", "relationship")}
    for entity, rows in children.items():
        for r in _rows(conn, snapshot_id, entity, "row_id", "party_key"):
            rows[r.party_key].append(r.row_id)

    f = validation_finding.c
    findings = conn.execute(
        select(f.rule_id, f.entity, f.row_id, f.account_key, f.party_key, f.outcome).where(
            f.snapshot_id == snapshot_id, f.outcome.in_([str(o) for o in SEGMENT_OUTCOMES | RECORD_OUTCOMES])
        )
    ).all()
    record_rejects = [x for x in findings if x.outcome in RECORD_OUTCOMES]
    record_index = FindingIndex(record_rejects)
    segment_rejects = {(x.entity, x.row_id) for x in findings if x.outcome in SEGMENT_OUTCOMES and x.row_id is not None}

    def valid(entity, party_key):
        return any((entity, row_id) not in segment_rejects for row_id in children[entity][party_key])

    holders = {}
    for r in _rows(conn, snapshot_id, "account_holder", "row_id", "account_key", "party_key"):
        holders.setdefault((r.account_key, r.party_key), r.row_id)
    outcomes, cascade_findings, accepted = [], [], 0
    for (account_key, party_key), holder_row in holders.items():
        reasons = record_index.reasons(account_key, party_key)
        checks = [
            (settings["no_identifier_rule"], not valid("party_identifier", party_key)),
            (settings["no_valid_address_rule"], not valid("party_address", party_key)),
            (
                settings["no_relationship_rule"],
                account_type.get(account_key) not in optional_types and not valid("relationship", party_key),
            ),
        ]
        for rule_id, failed in checks:
            rule = rules.get(rule_id)
            if failed and rule is not None and rule.enabled:
                cascade_findings.append(
                    dict(
                        snapshot_id=snapshot_id,
                        rule_id=rule_id,
                        entity=rule.entity,
                        row_id=holder_row,
                        account_key=account_key,
                        party_key=party_key,
                        segment=rule.segment,
                        field=rule.field,
                        outcome=str(rule.outcome),
                        value=None,
                        message=rule.message[:500],
                    )
                )
                if rule.outcome in RECORD_OUTCOMES:
                    reasons.append(rule_id)
        accepted += not reasons
        outcomes.append(
            dict(
                snapshot_id=snapshot_id,
                account_key=account_key,
                party_key=party_key,
                outcome=str(RecordOutcome.REJECTED if reasons else RecordOutcome.ACCEPTED),
                reason_rule_id=reasons[0] if reasons else None,
            )
        )
    if cascade_findings:
        conn.execute(insert(validation_finding), cascade_findings)
    if outcomes:
        conn.execute(insert(record_outcome), outcomes)
    return accepted, len(outcomes) - accepted


CASCADES: dict[str, Callable[..., tuple[int, int]]] = {
    "account_holder_minimums": account_holder_minimums,
    "facility_borrower_minimums": facility_borrower_minimums,
}
