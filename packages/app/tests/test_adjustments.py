"""User adjustments: input rules, capture on a request, and their effect when the snapshot is built from the views
(simple SQLite stand-in views over the staging table; the real views run in the Database job)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from app.adjustments import (
    AdjustmentError,
    AdjustmentNotFoundError,
    AdjustmentRepository,
    AdjustmentScope,
    ApplyResult,
    applied_summary,
    parse_keys,
    parse_override_value,
)
from app.adjustments.extract import SqlAdjustments
from app.adjustments.summary import adjustments_csv
from app.adjustments.tables import snapshot_adjustment
from app.enums import AdjustmentKind as K
from app.enums import ExtractStrategy, SnapshotStatus
from app.enums import OverrideField as F
from app.enums import RunMode as M
from app.enums import SubmissionType as T
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository
from canonical.testing import TENANT
from sources.extract import ExtractError, run_extract
from sources.views import compile_views

AS_OF = date(2026, 9, 30)

# A loan per staging row; borrower = the row's customer (consumer_name holds the customer id here). A2 is joint:
# held by C1 and C2. C1 also holds A1, so excluding customer C1 removes A1 and A2 (and borrower C1); C2 then holds
# nothing else and is dropped too.
ROWS = [("A1", "C1"), ("A2", "C1"), ("A2", "C2"), ("A3", "C3")]
VIEWS = [
    """CREATE VIEW automn.vw_consumer_account AS
       SELECT DISTINCT 1 AS load_id, '2026-09-30' AS as_of_date, s.acc AS account_key, s.acc AS account_number,
              45 AS days_past_due, 1000.50 AS current_balance, 10.00 AS amount_overdue
       FROM automn.t_rows s""",
    """CREATE VIEW automn.vw_consumer_party AS
       SELECT DISTINCT 1 AS load_id, s.cust AS party_key, 'NAME ' || s.cust AS full_name FROM automn.t_rows s""",
    """CREATE VIEW automn.vw_consumer_account_holder AS
       SELECT 1 AS load_id, s.acc AS account_key, s.cust AS party_key, 'INDIVIDUAL' AS ownership_indicator
       FROM automn.t_rows s""",
]


@pytest.fixture
def views(engine):
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE automn.t_rows (acc TEXT, cust TEXT)"))
        conn.execute(text("INSERT INTO automn.t_rows VALUES (:a, :c)"), [{"a": a, "c": c} for a, c in ROWS])
        for v in VIEWS:
            conn.execute(text(v))
    return engine


@pytest.fixture
def adjustments(engine, clock):
    return AdjustmentRepository(engine, clock=clock)


def draft(requests_repo, tenant="nbfc_001") -> AdjustmentScope:
    batch = requests_repo.create_batch(tenant, AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)
    return AdjustmentScope.of(requests_repo.list_batch(batch)[0])


def extract(engine, scope, *, source=None, strategy=ExtractStrategy.INSERT_SELECT):
    return run_extract(
        tenant=replace(TENANT, extract_strategy=strategy),
        compiled=compile_views(engine, "CONSUMER"),
        submission_type="CONSUMER",
        as_of_date=AS_OF,
        app_engine=engine,
        source_engine=source or engine,
        snapshots=SnapshotRepository(engine),
        owner="t",
        batch_id=scope.batch_id,
        load_id=1,
        adjustments=SqlAdjustments(engine),
    )


def keys(engine, entity, sid, column):
    t = ENTITY_TABLES[entity]
    with engine.connect() as conn:
        return sorted(conn.execute(select(t.c[column]).where(t.c.snapshot_id == sid)).scalars())


# ---------------------------------------------------------------- input rules
def test_parse_keys_splits_dedupes_and_limits_length():
    assert parse_keys(" A1\nA2, A3;A1\n\n  A4 ") == ["A1", "A2", "A3", "A4"]
    with pytest.raises(AdjustmentError, match="Longer than 50"):
        parse_keys("X" * 51)


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        (F.DAYS_PAST_DUE, "45", Decimal(45)),
        (F.AMOUNT_OVERDUE, "1,250.50", Decimal("1250.50")),
        (F.CURRENT_BALANCE, "-100", Decimal(-100)),  # a ledger balance can be a credit
    ],
)
def test_override_values_accepted(field, raw, expected):
    assert parse_override_value(field, raw) == expected


@pytest.mark.parametrize(
    ("field", "raw", "message"),
    [
        (F.DAYS_PAST_DUE, "4.5", "whole number"),
        (F.DAYS_PAST_DUE, "10000", "whole number"),
        (F.AMOUNT_OVERDUE, "-1", "cannot be negative"),
        (F.SETTLEMENT_AMOUNT, "1.234", "2 decimals"),
        (F.AMOUNT_OVERDUE, "abc", "not a number"),
        (F.AMOUNT_OVERDUE, "NaN", "not a number"),
        (F.AMOUNT_OVERDUE, "1e20", "too large"),
    ],
)
def test_override_values_rejected(field, raw, message):
    with pytest.raises(AdjustmentError, match=message):
        parse_override_value(field, raw)


def test_duplicates_skipped_and_override_replaces_previous(adjustments, requests_repo):
    scope = draft(requests_repo)
    first = adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A1", "A2"], " r ", "u")
    again = adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A2", "A3"], None, "u")
    assert (first.added, again.added, again.already_present) == (["A1", "A2"], ["A3"], ["A2"])
    one, replaced = adjustments.add_override(scope, "A9", F.DAYS_PAST_DUE, Decimal(30), None, "u")
    _, replaced_two = adjustments.add_override(scope, "A9", F.DAYS_PAST_DUE, Decimal(60), None, "u")
    assert replaced is None and replaced_two == one
    active = adjustments.list(scope)
    assert [a.override_value for a in active if a.kind is K.OVERRIDE] == [Decimal(60)]
    assert {a.reason for a in active if a.key == "A1"} == {"r"}  # trimmed
    assert {a.batch_id for a in active} == {scope.batch_id}  # tagged with the request


def test_invalid_captures_are_refused(adjustments, requests_repo):
    scope = draft(requests_repo)
    with pytest.raises(AdjustmentError, match="Not an exclusion"):
        adjustments.add_exclusions(scope, K.OVERRIDE, ["A1"], None, "u")
    with pytest.raises(AdjustmentError, match="at least one"):
        adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, [" "], None, "u")
    with pytest.raises(AdjustmentError, match="one loan account number"):
        adjustments.add_override(scope, "", F.DAYS_PAST_DUE, Decimal(1), None, "u")
    with pytest.raises(AdjustmentError, match="at most 500"):
        adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A1"], "x" * 501, "u")
    with pytest.raises(AdjustmentNotFoundError):
        adjustments.get(999)


def test_remove_is_soft_and_scoped_to_the_request(adjustments, requests_repo):
    scope, other = draft(requests_repo), draft(requests_repo, "nbfc_002")
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A1"], None, "u")
    adjustments.add_exclusions(other, K.EXCLUDE_ACCOUNT, ["A1"], None, "u")
    (adj,) = adjustments.list(scope)
    adjustments.remove(adj.adjustment_id, "boss")
    assert adjustments.list(scope) == []
    (gone,) = adjustments.list(scope, include_removed=True)
    assert gone.removed_by == "boss" and not gone.is_active and len(adjustments.list(other)) == 1
    with pytest.raises(AdjustmentError, match="already removed"):
        adjustments.remove(adj.adjustment_id, "boss")


# ---------------------------------------------------------------- in the snapshot
def test_no_adjustments_changes_nothing(views, requests_repo):
    result = extract(views, draft(requests_repo))
    assert keys(views, "account", result.snapshot_id, "account_key") == ["A1", "A2", "A3"]
    assert applied_summary(views, result.snapshot_id) == ApplyResult(0, 0, 0)
    assert adjustments_csv(views, result.snapshot_id) == ""


def test_excluding_a_joint_loan_removes_it_whole(views, requests_repo, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A2"], "written off", "u")
    sid = extract(views, scope).snapshot_id
    assert keys(views, "account", sid, "account_key") == ["A1", "A3"]
    assert keys(views, "account_holder", sid, "account_key") == ["A1", "A3"]  # both holders of A2 gone
    assert keys(views, "party", sid, "party_key") == ["C1", "C3"]  # C2 held only A2
    assert applied_summary(views, sid).excluded_accounts == 1


def test_excluding_a_customer_excludes_every_loan_they_hold(views, requests_repo, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_CUSTOMER, ["C1"], None, "u")
    sid = extract(views, scope).snapshot_id
    assert keys(views, "account", sid, "account_key") == ["A3"]
    assert keys(views, "party", sid, "party_key") == ["C3"]
    assert {(a.action, a.account_key or a.party_key) for a in AdjustmentRepository(views).applied(sid)} == {
        ("EXCLUDE", "A1"),
        ("EXCLUDE", "A2"),
        ("EXCLUDE_PARTY", "C1"),
        ("EXCLUDE_PARTY", "C2"),
    }


def test_overrides_replace_source_values_and_unmatched_are_reported(views, requests_repo, adjustments):
    scope = draft(requests_repo)
    adjustments.add_override(scope, "A1", F.DAYS_PAST_DUE, Decimal(0), "paid", "u")
    adjustments.add_override(scope, "A1", F.CURRENT_BALANCE, Decimal("-5.25"), None, "u")
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["NOPE"], None, "u")
    sid = extract(views, scope).snapshot_id
    t = ENTITY_TABLES["account"]
    with views.connect() as conn:
        rows = dict(
            (r.account_key, (r.days_past_due, r.current_balance))
            for r in conn.execute(select(t).where(t.c.snapshot_id == sid))
        )
    assert rows["A1"] == (0, Decimal("-5.25")) and rows["A3"] == (45, Decimal("1000.50"))
    assert applied_summary(views, sid) == ApplyResult(0, 1, 1)  # one loan overridden; NOPE matched nothing
    (override,) = [a for a in AdjustmentRepository(views).applied(sid) if a.field is F.DAYS_PAST_DUE]
    assert (override.old_value, override.new_value) == (Decimal(45), Decimal(0))
    csv = adjustments_csv(views, sid)
    assert "NOT_FOUND" in csv and "45.00" in csv and "paid" in csv
    assert AdjustmentRepository(views).frozen(sid)  # adjustment -> loans affected (the view)


def test_only_this_requests_active_adjustments_apply(views, requests_repo, adjustments):
    scope, other = draft(requests_repo), draft(requests_repo, "nbfc_002")
    adjustments.add_exclusions(other, K.EXCLUDE_ACCOUNT, ["A1"], None, "u")  # another request
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A3"], None, "u")
    (removed,) = adjustments.list(scope)
    adjustments.remove(removed.adjustment_id, "u")  # removed before submit
    sid = extract(views, scope).snapshot_id
    assert keys(views, "account", sid, "account_key") == ["A1", "A2", "A3"]
    with views.connect() as conn:
        assert conn.execute(select(snapshot_adjustment).where(snapshot_adjustment.c.snapshot_id == sid)).all() == []


def test_adjustments_need_the_views_in_the_app_database(views, requests_repo, adjustments):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A1"], None, "u")
    elsewhere = views.execution_options()  # a different Engine object: another server as far as the extract knows
    with pytest.raises(ExtractError, match="on another server"):
        extract(views, scope, source=elsewhere, strategy=ExtractStrategy.STREAM)
    snap = SnapshotRepository(views).find_for_batch(scope.batch_id, "nbfc_001", "CONSUMER")
    assert snap.status is SnapshotStatus.FAILED
    with views.connect() as conn:  # the frozen copy was purged with the rows
        assert conn.execute(select(snapshot_adjustment)).all() == []


def test_frozen_copy_is_the_audit_trail(views, requests_repo, adjustments, clock):
    scope = draft(requests_repo)
    adjustments.add_exclusions(scope, K.EXCLUDE_ACCOUNT, ["A3"], "dispute", "ops.priya")
    sid = extract(views, scope).snapshot_id
    (adj,) = adjustments.list(scope)
    adjustments.remove(adj.adjustment_id, "later")  # removing it afterwards does not change the snapshot's record
    with views.connect() as conn:
        (frozen,) = conn.execute(select(snapshot_adjustment).where(snapshot_adjustment.c.snapshot_id == sid)).all()
    assert (frozen.account_number, frozen.reason, frozen.created_by) == ("A3", "dispute", "ops.priya")
    assert frozen.created_at == datetime(2026, 10, 21, 9, 0, 0)


def test_every_entity_can_carry_adjustments():
    """Every canonical entity (commercial ones included) has a key the exclusions filter on."""
    from app.adjustments.sql import final_select
    from canonical.schema import MODEL

    for entity, definition in MODEL.entities.items():
        sql = final_select(entity, "SELECT 1", [c.name for c in definition.columns])
        assert "NOT EXISTS" in sql or "NOT ((" in sql or entity == "borrower_group", entity
