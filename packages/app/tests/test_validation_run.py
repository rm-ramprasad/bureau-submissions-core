"""Validation in the app: the core validates a snapshot with the client's settings and the result is stored where the
rest of the app reads it. Regression: the stored result of each golden batch equals the result the reference app
recorded (compared like for like, as in bureau_core's golden test)."""

import pytest
from app_helpers import golden, load_snapshot
from sqlalchemy import func, select

from app.enums import Outcome, SnapshotStatus
from app.validation.run import ValidationError, capture_batch, validate_snapshot
from app.validation.settings import load_rule_set, set_policy
from app.validation.tables import record_outcome, validation_finding, validation_summary
from bureau_core.validation.rules import builtin_rules
from bureau_core.validation.validate import finding_value, read_result, value_scale
from canonical.snapshot import SnapshotRepository


def validated(engine, name):
    batch, expected = golden(name)
    sid = load_snapshot(engine, batch, skip=("account_history",))  # history was added after the capture's validation
    summary = validate_snapshot(
        engine, SnapshotRepository(engine), sid, load_rule_set(engine, "nbfc_001", batch.submission_type)
    )
    return sid, summary, expected


@pytest.mark.parametrize("name", ["consumer_nbfc_001", "commercial_nbfc_001"])
def test_the_stored_result_is_the_recorded_one(engine, name):
    sid, summary, expected = validated(engine, name)
    scales = {r.rule_id: value_scale(r) for r in builtin_rules(name.split("_")[0].upper())}
    for f in expected.findings:
        f["value"] = finding_value(scales.get(f["rule_id"]), f["value"])
    with engine.connect() as conn:
        stored = read_result(conn, sid)
    assert stored.outcomes == expected.outcomes

    def key(f):
        return tuple(
            str(f[k]) for k in ("rule_id", "entity", "row_id", "account_key", "party_key", "field", "outcome", "value")
        )

    assert sorted(map(key, stored.findings)) == sorted(map(key, expected.findings))
    assert summary.records == len(expected.outcomes)
    assert summary.accepted + summary.rejected == summary.records
    assert SnapshotRepository(engine).get(sid).status is SnapshotStatus.VALIDATED
    with engine.connect() as conn:
        assert conn.execute(
            select(func.sum(validation_summary.c.finding_count)).where(validation_summary.c.snapshot_id == sid)
        ).scalar() == len(expected.findings)


def test_revalidation_replaces_the_result(engine):
    sid, first, _ = validated(engine, "consumer_nbfc_001")
    rules = load_rule_set(engine, "nbfc_001", "CONSUMER")
    again = validate_snapshot(engine, SnapshotRepository(engine), sid, rules)
    assert again == first
    with engine.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(record_outcome).where(record_outcome.c.snapshot_id == sid)
            ).scalar()
            == first.records
        )


def test_a_policy_changes_the_validation(engine):
    sid, before, _ = validated(engine, "consumer_nbfc_001")
    with engine.connect() as conn:
        rule_id, outcome = conn.execute(
            select(validation_finding.c.rule_id, validation_finding.c.outcome)
            .where(validation_finding.c.snapshot_id == sid, validation_finding.c.outcome == "REJECT_RECORD")
            .limit(1)
        ).one()
    set_policy(engine, "nbfc_001", rule_id, is_enabled=False)
    after = validate_snapshot(engine, SnapshotRepository(engine), sid, load_rule_set(engine, "nbfc_001", "CONSUMER"))
    with engine.connect() as conn:
        assert not conn.execute(
            select(validation_finding.c.rule_id).where(
                validation_finding.c.snapshot_id == sid, validation_finding.c.rule_id == rule_id
            )
        ).first()
    assert after.findings.get(outcome, 0) < before.findings[outcome]
    set_policy(engine, "nbfc_001", rule_id, is_enabled=True, outcome=Outcome.WARN)
    softened = validate_snapshot(engine, SnapshotRepository(engine), sid, load_rule_set(engine, "nbfc_001", "CONSUMER"))
    assert softened.findings["WARN"] > before.findings.get("WARN", 0)


def test_only_extracted_snapshots_can_be_validated_and_failures_fail_the_snapshot(engine, monkeypatch):
    snapshots = SnapshotRepository(engine)
    batch, _ = golden("consumer_nbfc_001")
    building = snapshots.create("nbfc_001", "CONSUMER", batch.as_of_date, "b-x", "v", "t")
    with pytest.raises(ValidationError, match="only extracted snapshots"):
        validate_snapshot(engine, snapshots, building, [])
    sid = load_snapshot(engine, batch, batch_id="b-y")

    def broken(*_a, **_k):
        raise RuntimeError("core down")

    monkeypatch.setattr("app.validation.run.core_validate", broken)
    with pytest.raises(RuntimeError, match="core down"):
        validate_snapshot(engine, snapshots, sid, [])
    snap = snapshots.get(sid)
    assert snap.status is SnapshotStatus.FAILED and "validation (core): core down" in snap.error_message


def test_capture_batch_round_trips(engine):
    batch, _ = golden("commercial_nbfc_001")
    sid = load_snapshot(engine, batch)
    with engine.connect() as conn:
        captured = capture_batch(conn, sid)
    assert captured.entities == batch.entities and captured.submission_type == batch.submission_type
    assert captured.source["snapshot_id"] == sid and captured.exceptions == {}


def test_create_all_can_run_again(engine):
    """The adjustment view is created once, however often create_all runs (e.g. two test modules, one database)."""
    from canonical.db import metadata

    metadata.create_all(engine)
    metadata.create_all(engine)
