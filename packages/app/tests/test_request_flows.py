"""The request repository's worker, draft and retention queries, and the dashboard built from real rows (the API and
worker tests in 1.3c/1.3d exercise them again end to end)."""

from datetime import date, timedelta

import pytest

from app.enums import BureauCode as B
from app.enums import RequestStatus as S
from app.enums import RunMode as M
from app.enums import SubmissionType as T
from app.requests import InvalidTransitionError, RequestResult
from app.requests.stages import Period, Stage, build_dashboard, latest_per_tenant, load_requests

AS_OF = date(2026, 9, 30)


def validated(repo, clock, *, snapshot_id, records=10, accepted=8, types=(T.CONSUMER,), tenant="nbfc_001"):
    """A VALIDATE batch that succeeded on `snapshot_id`."""
    batch = repo.create_batch(tenant, AS_OF, M.VALIDATE, types)
    for _ in types:
        req = repo.claim_next("w1")
        repo.assign_snapshot(req.request_id, snapshot_id)
        clock.advance()
        repo.mark_succeeded(req.request_id, RequestResult(total_records=records, accepted_records=accepted))
    return batch


def test_drafts_are_found_submitted_once(requests_repo):
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)
    assert requests_repo.open_draft("nbfc_001", AS_OF, "CONSUMER") == batch
    assert requests_repo.open_draft("nbfc_001", AS_OF, "COMMERCIAL") is None
    assert requests_repo.claim_next("w") is None  # a draft is not work
    assert requests_repo.submit_draft(batch) == 1
    assert requests_repo.open_draft("nbfc_001", AS_OF, "CONSUMER") is None
    with pytest.raises(InvalidTransitionError, match="not a draft"):
        requests_repo.submit_draft(batch)


def test_queue_triggered_claim_and_queue_health(requests_repo, clock):
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    (row,) = requests_repo.list_batch(batch)
    assert requests_repo.oldest_pending() == clock()
    clock.advance(60)
    assert [r.request_id for r in requests_repo.pending_before(clock())] == [row.request_id]
    claimed = requests_repo.claim(row.request_id, "fn-1")
    assert claimed.status is S.RUNNING and claimed.attempt_count == 1 and claimed.worker_id == "fn-1"
    assert requests_repo.claim(row.request_id, "fn-2") is None  # duplicate delivery
    assert requests_repo.oldest_pending() is None
    clock.advance(60)
    assert [r.request_id for r in requests_repo.running_started_before(clock())] == [row.request_id]
    requests_repo.release(row.request_id)
    assert requests_repo.get(row.request_id).status is S.PENDING


def test_generations_files_and_retention(requests_repo, clock):
    batch = validated(requests_repo, clock, snapshot_id=11)
    assert requests_repo.validation_batch_of(11) == batch and requests_repo.generation_of(11) is None
    assert requests_repo.generation_of(None) is None and requests_repo.generations([None]) == []
    gen = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL, B.EXPERIAN], snapshot_id=11)
    assert requests_repo.generation_of(11) == gen
    cibil, experian = sorted(requests_repo.list_batch(gen), key=lambda r: r.bureau_code)
    for _ in range(2):
        requests_repo.claim_next("w")
    clock.advance()
    requests_repo.mark_succeeded(
        cibil.request_id,
        RequestResult(output_file_name="c.txt", output_file_path="/f/c.txt", accepted_records=8, total_records=10),
    )
    requests_repo.mark_failed(experian.request_id, "NOT MATCHED", review_file_path="/f/review.xlsx")
    assert [g.request_id for g in requests_repo.generations([11])] == [cibil.request_id, experian.request_id]
    assert requests_repo.get(experian.request_id).review_file_path == "/f/review.xlsx"

    files, total = requests_repo.list_files("nbfc_001", B.CIBIL, since=clock() - timedelta(days=1))
    assert total == 1 and [f.output_file_name for f in files] == ["c.txt"]
    assert requests_repo.list_files("nbfc_002")[1] == 0
    clock.advance(3600)
    old = requests_repo.finished_with_files_before(clock())
    assert {r.request_id for r in old} == {cibil.request_id, experian.request_id}
    requests_repo.clear_files(cibil.request_id)
    assert requests_repo.get(cibil.request_id).output_file_path is None
    assert requests_repo.list_files()[1] == 1  # still listed: the record of a generated file stays


def test_dashboard_from_real_rows(engine, requests_repo, clock):
    done = validated(requests_repo, clock, snapshot_id=21)
    gen = requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL], snapshot_id=21)
    (g,) = requests_repo.list_batch(gen)
    requests_repo.claim_next("w")
    requests_repo.mark_succeeded(g.request_id, RequestResult(output_file_name="c.txt", accepted_records=8))
    clock.advance()
    failing = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.COMMERCIAL])
    (f,) = requests_repo.list_batch(failing)
    requests_repo.claim_next("w")
    requests_repo.mark_failed(f.request_id, "views missing")
    clock.advance()
    draft = requests_repo.create_batch("nbfc_002", AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)

    later = clock() + timedelta(days=10)
    board = build_dashboard(engine, None, Period.ALL, later)
    assert board.raised == 3 and board.count(Stage.COMPLETED) == 1 and board.count(Stage.FAILED) == 1
    assert board.files_generated == 1 and board.records_submitted == 8
    assert board.acceptance_rate == pytest.approx(0.8) and board.completion_rate == pytest.approx(1 / 3)
    assert board.oldest_age(Stage.COMPLETED) is not None and board.oldest_age(Stage.CANCELLED) is None
    reasons = {a.request.batch_id: a.reason for a in board.attention}
    assert reasons[failing] == "Failed: views missing" and reasons[draft].startswith("Draft for 10 days")
    finished = requests_repo.get(g.request_id).finished_at
    assert board.bureaus == [{"bureau": B.CIBIL, "files": 1, "records": 8, "last": finished, "share": 100}]

    consumer = build_dashboard(engine, "nbfc_001", Period.ALL, later, submission_type="CONSUMER")
    assert [r.batch_id for r in consumer.requests] == [done]
    (req,) = load_requests(engine, "nbfc_001", submission_type=T.CONSUMER)
    assert (req.records, req.accepted, req.excluded, req.overridden) == (10, 8, 0, 0)
    assert latest_per_tenant(engine, ["nbfc_001", "nbfc_002"]) == {
        "nbfc_001": load_requests(engine, "nbfc_001")[0],
        "nbfc_002": load_requests(engine, "nbfc_002")[0],
    }
    assert load_requests(engine, since=later) == []


def test_stuck_and_stale_requests_need_attention(engine, requests_repo, clock):
    validated(requests_repo, clock, snapshot_id=31)
    running = requests_repo.create_batch("nbfc_001", date(2026, 9, 29), M.VALIDATE, [T.CONSUMER])
    requests_repo.claim_next("w")
    board = build_dashboard(engine, None, Period.ALL, clock() + timedelta(days=30))
    reasons = sorted(a.reason for a in board.attention)
    assert any(r.startswith("In progress for") for r in reasons)
    assert any(r.startswith("Validated 30 days ago") for r in reasons)
    assert running in {a.request.batch_id for a in board.attention}
