import itertools
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.enums import (
    BatchStatus,
    ReportingCycle,
)
from app.enums import (
    BureauCode as B,
)
from app.enums import (
    RequestStatus as S,
)
from app.enums import (
    RunMode as M,
)
from app.enums import (
    SubmissionType as T,
)
from app.requests import (
    AlreadyGeneratedError,
    DuplicateActiveRequestError,
    InvalidTransitionError,
    RequestNotFoundError,
    RequestResult,
    RequestValidationError,
)

AS_OF = date(2026, 9, 30)
NBFC1, NBFC2 = "nbfc_001", "nbfc_002"
SNAPSHOTS = itertools.count(1000)


def generate(repo, bureaus, tenant=NBFC1, as_of=AS_OF, types=(T.CONSUMER,), snapshot_id=None, **kw):
    """GENERATE rows always come from a validated snapshot (a fresh id each time unless given: files are generated
    once per snapshot)."""
    snapshot_id = next(SNAPSHOTS) if snapshot_id is None else snapshot_id
    return repo.create_batch(tenant, as_of, M.GENERATE, types, bureaus, snapshot_id=snapshot_id, **kw)


def validate(repo, tenant=NBFC1, as_of=AS_OF, types=(T.CONSUMER,), **kw):
    return repo.create_batch(tenant, as_of, M.VALIDATE, types, **kw)


# ---------------------------------------------------------------- create
def test_generate_creates_one_row_per_bureau_from_a_snapshot(requests_repo):
    batch_id = generate(
        requests_repo, [B.CIBIL, B.EXPERIAN, B.EQUIFAX], reporting_cycle="ME", requested_by="ops.user", snapshot_id=7
    )
    rows = requests_repo.list_batch(batch_id)
    assert {r.bureau_code for r in rows} == {B.CIBIL, B.EXPERIAN, B.EQUIFAX}
    assert all(
        r.status is S.PENDING and r.run_mode is M.GENERATE and r.tenant_id == NBFC1 and r.snapshot_id == 7 for r in rows
    )
    assert all(r.reporting_cycle is ReportingCycle.MONTH_END and r.requested_by == "ops.user" for r in rows)


def test_validate_rows_for_several_types(requests_repo):
    batch_id = validate(requests_repo, types=[T.CONSUMER, T.COMMERCIAL])
    assert {r.submission_type for r in requests_repo.list_batch(batch_id)} == {T.CONSUMER, T.COMMERCIAL}


def test_validate_creates_one_bureau_independent_row_per_type(requests_repo):
    batch_id = validate(requests_repo, types=[T.CONSUMER, T.COMMERCIAL])
    rows = requests_repo.list_batch(batch_id)
    assert [(r.submission_type, r.bureau_code, r.run_mode) for r in rows] == [
        (T.COMMERCIAL, None, M.VALIDATE),
        (T.CONSUMER, None, M.VALIDATE),
    ]


def test_generate_can_reuse_a_validated_snapshot(requests_repo):
    batch_id = generate(requests_repo, [B.CIBIL, B.EQUIFAX], snapshot_id=42)
    assert {r.snapshot_id for r in requests_repo.list_batch(batch_id)} == {42}


def test_repeated_selections_collapse(requests_repo):
    batch_id = generate(requests_repo, [B.CIBIL, "CIBIL"], types=["CONSUMER", T.CONSUMER])
    assert len(requests_repo.list_batch(batch_id)) == 1


@pytest.mark.parametrize(
    "kwargs,message",
    [
        (dict(tenant_id="", run_mode=M.VALIDATE, submission_types=[T.CONSUMER]), "NBFC"),
        (dict(run_mode=M.VALIDATE, submission_types=[]), "submission type"),
        (dict(run_mode=M.GENERATE, submission_types=[T.CONSUMER], bureau_codes=[B.CIBIL]), "validate first"),
        (dict(run_mode=M.GENERATE, submission_types=[T.CONSUMER], snapshot_id=1), "bureau"),
        (dict(run_mode=M.VALIDATE, submission_types=[T.CONSUMER], bureau_codes=[B.CIBIL]), "bureau-independent"),
        (dict(run_mode=M.VALIDATE, submission_types=[T.CONSUMER], snapshot_id=1), "generate"),
        (
            dict(
                run_mode=M.GENERATE, submission_types=[T.CONSUMER, T.COMMERCIAL], bureau_codes=[B.CIBIL], snapshot_id=1
            ),
            "exactly one",
        ),
        (dict(run_mode=M.VALIDATE, submission_types=[T.CONSUMER], as_of_date=date(2026, 10, 22)), "future"),
    ],
)
def test_create_batch_validation(requests_repo, kwargs, message):
    args = dict(tenant_id=NBFC1, as_of_date=AS_OF) | kwargs
    with pytest.raises(RequestValidationError, match=message):
        requests_repo.create_batch(**args)


def test_unknown_codes_rejected(requests_repo):
    with pytest.raises(ValueError):
        generate(requests_repo, [B.CIBIL], types=["RETAIL"])
    with pytest.raises(ValueError):
        generate(requests_repo, ["TRANSUNION"])
    with pytest.raises(ValueError):
        requests_repo.create_batch(NBFC1, AS_OF, "PREVIEW", [T.CONSUMER])


def test_unknown_tenant_rejected_by_foreign_key(requests_repo):
    with pytest.raises(IntegrityError):
        validate(requests_repo, tenant="nbfc_999")


# ---------------------------------------------------------------- duplicate guard
def test_duplicate_active_unit_is_blocked(requests_repo):
    generate(requests_repo, [B.CIBIL, B.EXPERIAN])
    with pytest.raises(DuplicateActiveRequestError) as exc:
        generate(requests_repo, [B.EXPERIAN, B.EQUIFAX])
    (conflict,) = exc.value.conflicts
    assert (conflict.tenant_id, conflict.bureau_code, conflict.run_mode) == (NBFC1, B.EXPERIAN, M.GENERATE)
    assert sum(len(requests_repo.list_batch(b.batch_id)) for b in requests_repo.list_batches()) == 2


def test_duplicate_guard_is_per_tenant_and_mode(requests_repo):
    generate(requests_repo, [B.CIBIL])
    generate(requests_repo, [B.CIBIL], tenant=NBFC2)  # other tenant: allowed
    validate(requests_repo)  # same tenant, validate mode: allowed
    with pytest.raises(DuplicateActiveRequestError):
        validate(requests_repo)


def test_same_unit_allowed_for_other_date_or_after_completion(requests_repo):
    batch_id = generate(requests_repo, [B.CIBIL])
    generate(requests_repo, [B.CIBIL], as_of=date(2026, 8, 31))
    req = requests_repo.claim_next("w1")
    assert req.batch_id == batch_id
    requests_repo.mark_succeeded(req.request_id, RequestResult())
    generate(requests_repo, [B.CIBIL])  # no longer active -> allowed


def test_files_are_generated_once_per_validation(requests_repo):
    first = generate(requests_repo, [B.CIBIL], snapshot_id=5)
    with pytest.raises(AlreadyGeneratedError, match=first):  # while pending
        generate(requests_repo, [B.EXPERIAN], snapshot_id=5)  # another bureau: still refused
    req = requests_repo.claim_next("w1")
    requests_repo.mark_succeeded(req.request_id, RequestResult())
    with pytest.raises(AlreadyGeneratedError, match="create a new request"):  # done
        generate(requests_repo, [B.CIBIL], snapshot_id=5)
    generate(requests_repo, [B.CIBIL], snapshot_id=6)  # another validation: allowed


def test_a_failed_generation_can_be_retried_but_not_generated_twice(requests_repo):
    first = generate(requests_repo, [B.CIBIL], snapshot_id=5)
    (row,) = requests_repo.list_batch(first)
    requests_repo.claim_next("w1")
    requests_repo.mark_failed(row.request_id, "boom")
    requests_repo.requeue(row.request_id)  # the retry goes ahead
    assert requests_repo.get(row.request_id).status is S.PENDING
    requests_repo.claim_next("w1")
    requests_repo.mark_failed(row.request_id, "boom again")
    second = generate(requests_repo, [B.CIBIL], snapshot_id=5)  # failed: a new generation is allowed
    (other,) = requests_repo.list_batch(second)
    requests_repo.claim_next("w2")
    requests_repo.mark_succeeded(other.request_id, RequestResult())
    with pytest.raises(AlreadyGeneratedError, match=second):
        requests_repo.requeue(row.request_id)  # ...but then the old one cannot run too


def test_the_database_refuses_a_second_generation(requests_repo):
    """Two clicks at once: both pass the read check; the unique generate_key stops the second insert."""
    from app.requests.tables import submission_request as t

    generate(requests_repo, [B.CIBIL], snapshot_id=5)
    with pytest.raises(IntegrityError), requests_repo.engine.begin() as conn:
        conn.execute(
            t.insert().values(
                batch_id="x",
                tenant_id=NBFC1,
                run_mode="GENERATE",
                as_of_date=AS_OF,
                submission_type="CONSUMER",
                bureau_code="CIBIL",
                snapshot_id=5,
                requested_at=date(2026, 10, 1),
                status="PENDING",
                generate_key="5|CIBIL",
                attempt_count=0,
            )
        )


# ---------------------------------------------------------------- worker
def test_claim_next_takes_oldest_pending_first(requests_repo, clock):
    first = generate(requests_repo, [B.CIBIL])
    clock.advance()
    second = validate(requests_repo, tenant=NBFC2)
    a, b = requests_repo.claim_next("w1"), requests_repo.claim_next("w2")
    assert (a.batch_id, b.batch_id) == (first, second)
    assert a.status is S.RUNNING and a.worker_id == "w1" and a.attempt_count == 1 and a.started_at == clock.now
    assert requests_repo.claim_next("w3") is None


def test_assign_snapshot_only_while_active(requests_repo):
    validate(requests_repo)
    req = requests_repo.claim_next("w1")
    requests_repo.assign_snapshot(req.request_id, 7)
    assert requests_repo.get(req.request_id).snapshot_id == 7
    requests_repo.mark_succeeded(req.request_id, RequestResult())
    with pytest.raises(InvalidTransitionError):
        requests_repo.assign_snapshot(req.request_id, 8)


def test_succeeded_row_records_result(requests_repo, clock):
    generate(requests_repo, [B.CIBIL])
    req = requests_repo.claim_next("w1")
    clock.advance(30)
    requests_repo.mark_succeeded(
        req.request_id,
        RequestResult(
            output_file_name="CIB0000001-21102026-090030.txt",
            output_file_path="out/1.txt",
            exceptions_file_path="out/1.csv",
            total_records=56,
            accepted_records=42,
            rejected_records=14,
            duplicate_records=0,
        ),
    )
    done = requests_repo.get(req.request_id)
    assert done.status is S.SUCCEEDED and done.finished_at == clock.now
    assert (done.output_file_name, done.total_records, done.accepted_records) == (
        "CIB0000001-21102026-090030.txt",
        56,
        42,
    )


def test_failed_row_can_be_requeued_and_retried(requests_repo):
    validate(requests_repo)
    req = requests_repo.claim_next("w1")
    requests_repo.mark_failed(req.request_id, "source DB unreachable")
    assert requests_repo.get(req.request_id).error_message == "source DB unreachable"
    requests_repo.requeue(req.request_id)
    retried = requests_repo.claim_next("w2")
    assert retried.request_id == req.request_id
    assert retried.attempt_count == 2 and retried.error_message is None


def test_requeue_blocked_when_unit_active_again(requests_repo):
    generate(requests_repo, [B.CIBIL])
    req = requests_repo.claim_next("w1")
    requests_repo.mark_failed(req.request_id, "boom")
    generate(requests_repo, [B.CIBIL])
    with pytest.raises(DuplicateActiveRequestError):
        requests_repo.requeue(req.request_id)


def test_cancel_only_pending(requests_repo):
    batch_id = generate(requests_repo, [B.CIBIL, B.EQUIFAX])
    pending, _ = requests_repo.list_batch(batch_id)
    requests_repo.cancel(pending.request_id)
    assert requests_repo.get(pending.request_id).status is S.CANCELLED
    running = requests_repo.claim_next("w1")
    with pytest.raises(InvalidTransitionError):
        requests_repo.cancel(running.request_id)


def test_invalid_transitions_rejected(requests_repo):
    batch_id = validate(requests_repo)
    row = requests_repo.list_batch(batch_id)[0]
    with pytest.raises(InvalidTransitionError):
        requests_repo.mark_succeeded(row.request_id, RequestResult())  # still PENDING


def test_get_missing(requests_repo):
    with pytest.raises(RequestNotFoundError):
        requests_repo.get(999)


# ---------------------------------------------------------------- listing
def test_list_batches_newest_first_with_derived_status(requests_repo, clock):
    old = generate(requests_repo, [B.CIBIL, B.EXPERIAN])
    clock.advance()
    new = validate(requests_repo, as_of=date(2026, 8, 31))
    a = requests_repo.claim_next("w1")
    b = requests_repo.claim_next("w1")
    requests_repo.mark_succeeded(a.request_id, RequestResult())
    requests_repo.mark_failed(b.request_id, "boom")

    batches = requests_repo.list_batches()
    assert [x.batch_id for x in batches] == [new, old]
    assert (batches[0].status, batches[0].run_mode, batches[0].bureau_codes) == (BatchStatus.PENDING, M.VALIDATE, ())
    assert batches[1].status is BatchStatus.PARTIAL
    assert batches[1].status_counts == {S.SUCCEEDED: 1, S.FAILED: 1}
    assert set(batches[1].bureau_codes) == {B.CIBIL, B.EXPERIAN} and batches[1].tenant_id == NBFC1


def test_list_batches_by_tenant_and_limit(requests_repo, clock):
    for bureau in B:
        generate(requests_repo, [bureau])
        clock.advance()
    generate(requests_repo, [B.CIBIL], tenant=NBFC2)
    assert len(requests_repo.list_batches(limit=2)) == 2
    assert [b.tenant_id for b in requests_repo.list_batches(tenant_id=NBFC2)] == [NBFC2]
    assert len(requests_repo.list_batches(tenant_id=NBFC1)) == 4
