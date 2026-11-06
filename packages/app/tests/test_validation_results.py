"""Reading validation results (app.validation.results): the findings report, filters and the CSV export, on the
stored result of a golden snapshot."""

from dataclasses import replace

import pytest
from app_helpers import golden, load_snapshot

from app.validation import results
from app.validation.results import FindingFilter, report
from app.validation.run import validate_snapshot
from app.validation.settings import load_rule_set
from bureau_core.validation.rules import report_hints
from canonical.snapshot import SnapshotRepository

HINTS = report_hints("CONSUMER")


@pytest.fixture
def sid(engine):
    batch, _ = golden("consumer_nbfc_001")
    snapshot_id = load_snapshot(engine, batch, skip=("account_history",))
    validate_snapshot(engine, SnapshotRepository(engine), snapshot_id, load_rule_set(engine, "nbfc_001", "CONSUMER"))
    return snapshot_id


def test_report_counts_records_and_adds_up(engine, sid):
    rep = report(engine, sid)
    k = rep["kpis"]
    counts = results.record_counts(engine, sid)
    assert k["records"] == sum(counts.values()) and k["reported"] + k["not_reported"] == k["records"]
    assert k["not_reported"] == counts.get("REJECTED", 0) > 0
    assert sum(c["records"] for c in rep["not_reported_by_category"]) == k["not_reported"]  # one main reason each
    cats = {c["key"]: c for c in rep["categories"]}
    assert list(cats) == [c for c in HINTS.categories if c in cats]  # display order
    rules = [r for c in rep["categories"] for r in c["rules"]]
    assert all(r["records"] <= k["records"] and r["effect"] for r in rules)


def test_expected_rules_are_not_gaps(engine, sid):
    plain, hinted = report(engine, sid, replace(HINTS, expected=frozenset())), report(engine, sid)
    assert hinted["kpis"]["with_gaps"] <= plain["kpis"]["with_gaps"]
    assert hinted["kpis"]["not_reported"] == plain["kpis"]["not_reported"]  # file outcome unchanged
    for r in (r for c in hinted["categories"] for r in c["rules"] if r["expected"]):
        assert r["effect"] == results.EXPECTED_EFFECT


def test_findings_filters_and_export(engine, sid):
    summary = results.summary(engine, sid)
    rule_id = summary[0].rule_id
    rows, total = results.findings(engine, sid, FindingFilter(rule_id=rule_id), limit=2)
    assert total == summary[0].finding_count and len(rows) == min(2, total) and {r.rule_id for r in rows} == {rule_id}
    key = HINTS.category(rule_id)
    ids = results.category_rule_ids(engine, sid, key)
    assert rule_id in ids
    _, in_category = results.findings(engine, sid, FindingFilter(rule_ids=ids))
    assert in_category >= total
    lines = "".join(results.findings_csv(engine, sid, FindingFilter(rule_id=rule_id))).splitlines()
    assert lines[0] == ",".join(results.EXPORT_COLUMNS) and len(lines) == total + 1
    assert results.findings(engine, sid, FindingFilter(account_key="nobody"))[1] == 0
