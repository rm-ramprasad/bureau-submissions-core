"""JSON API: request flow (draft -> exceptions -> submit -> validate -> confirm -> files), tracker and files, findings
actions, rules & policies, audit, dispatch, and SSO identity. The worker runs the real pipeline over stand-in source
views backed by the golden batches (captures of the reference's mock LMS), so the account numbers are the same."""

from datetime import date
from pathlib import Path

import pytest
from app_helpers import install_golden_views
from sqlalchemy import select

from app.adjustments import AdjustmentRepository, AdjustmentScope
from app.audit import audit_event
from app.bureaus.seed import seed_missing
from app.enums import BureauCode as B
from app.enums import ExtractStrategy, Outcome
from app.enums import RequestStatus as S
from app.enums import RunMode as M
from app.enums import SubmissionType as T
from app.pipeline import Services
from app.ports.files import LocalFileStore
from app.requests import RequestResult
from app.users import UserRepository
from app.validation import settings as rules
from app.worker.runner import Worker
from canonical.snapshot import SnapshotRepository
from canonical.tenants import TenantRepository

AS_OF = date(2026, 9, 30)
JOINT_LOAN, CLEAN_LOAN = "B122HL10788234", "B107PL79230310"


def actions(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(select(audit_event.c.action).order_by(audit_event.c.event_id)).scalars())


@pytest.fixture
def worker(engine, requests_repo, bureau_repo, tmp_path, monkeypatch):
    """The real pipeline over golden-backed stand-in views (one upload: load_id 1)."""
    install_golden_views(engine)
    monkeypatch.setattr("app.pipeline.views.loaded_workbook", lambda *a: 1)
    tenants = TenantRepository(engine)
    tenants.upsert(
        tenants.get("nbfc_001").__class__(
            **{**vars(tenants.get("nbfc_001")), "extract_strategy": ExtractStrategy.INSERT_SELECT}
        )
    )
    seed_missing(bureau_repo, "nbfc_001")
    services = Services(engine, engine, SnapshotRepository(engine), LocalFileStore(tmp_path))
    return Worker(services, requests_repo, tenants, bureau_repo, worker_id="w-api")


@pytest.fixture
def api(make_api, tmp_path, engine):
    install_golden_views(engine)  # both types are set up for nbfc_001 (their views exist)
    return make_api(store=LocalFileStore(tmp_path))


def new_request(api, submit=True, **fields) -> tuple[int, dict]:
    body = {"tenant_id": "nbfc_001", "as_of_date": "2026-09-30", "submission_types": ["CONSUMER"]} | fields
    status, data = api.post("/requests", **body)
    if submit and status == 201:
        api.post(f"/batches/{data['batch_id']}/submit")
    return status, data


# ---------------------------------------------------------------- meta, health
def test_meta_lists_choices(api):
    status, meta = api.get("/meta")
    assert status == 200 and {t["tenant_id"] for t in meta["tenants"]} == {"nbfc_001", "nbfc_002"}
    types = {t["value"]: t["enabled"] for t in meta["submission_types"]}
    assert types == {"CONSUMER": True, "COMMERCIAL": True}  # nbfc_001 has both mappings; NESL has no type folder
    assert {o["value"] for o in meta["override_fields"]} >= {"days_past_due", "settlement_amount"}
    dates = [d["value"] for d in meta["as_of_dates"]]
    assert meta["default_as_of"] == dates[0] and dates == sorted(dates, reverse=True)
    assert {d["cycle"] for d in meta["as_of_dates"]} == {"W1", "W2", "W3", "ME"}


def test_health_reports_waiting_requests(api, requests_repo, clock):
    assert api.get("/health")[1] == {"oldest_pending_at": None, "waiting_seconds": None, "slow": False}
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    health = api.get("/health")[1]
    assert health["oldest_pending_at"].startswith("2026-10-21T09:00:00") and health["slow"] is True


def test_unknown_tenant_and_routes(api):
    assert api.get("/batches", tenant="nbfc_999") == (400, {"error": "Unknown NBFC nbfc_999"})
    assert api.get("/batches/nope")[0] == 404
    assert api.get("/snapshots/999/findings")[0] == 404
    assert api.get("/requests/999/file")[0] == 404
    assert api.get("/nothing-here")[0] == 404


# ---------------------------------------------------------------- creating requests
def test_new_request_is_a_draft_until_submitted(api, requests_repo, worker, dispatcher):
    status, data = new_request(api, submit=False)
    assert status == 201 and "Add any exclusions" in data["message"]
    (row,) = requests_repo.list_batch(data["batch_id"])
    assert (row.status, row.run_mode, row.bureau_code, row.requested_by) == (S.DRAFT, M.VALIDATE, None, "internal-user")
    assert dispatcher.dispatched == [] and worker.run_once() == 0  # not validated until submitted
    assert [d["batch_id"] for d in api.get("/exceptions")[1]["drafts"]] == [data["batch_id"]]
    api.post(f"/batches/{data['batch_id']}/submit")
    assert dispatcher.dispatched == [row.request_id]  # started on submit
    assert requests_repo.get(row.request_id).status is S.PENDING


def test_new_request_is_always_a_validation(api, requests_repo):
    _, data = new_request(api, run_mode="GENERATE", bureau_codes=["CIBIL"])  # ignored if posted anyway
    assert [(r.run_mode, r.bureau_code) for r in requests_repo.list_batch(data["batch_id"])] == [(M.VALIDATE, None)]


def test_user_header_is_recorded_without_sign_in(api, requests_repo):
    _status, data = api.post(
        "/requests",
        headers={"X-Forwarded-User": "ops.priya"},
        tenant_id="nbfc_002",
        as_of_date="2026-09-30",
        submission_types=["CONSUMER"],
    )
    assert requests_repo.list_batch(data["batch_id"])[0].requested_by == "ops.priya"


@pytest.mark.parametrize(
    "fields,message",
    [
        (dict(tenant_id="nbfc_999"), "Select an active NBFC"),
        (dict(as_of_date="2099-01-01"), "future"),
        (dict(as_of_date="2026-09-10"), "reporting date"),
        (dict(as_of_date="not-a-date"), "YYYY-MM-DD"),
        (dict(submission_types=["NESL"]), "Not set up for this NBFC"),
        (dict(submission_types=[]), "at least one submission type"),
    ],
)
def test_invalid_requests_are_refused(api, fields, message):
    status, data = new_request(api, **fields)
    assert status == 400 and message in data["error"]


def test_the_cycle_comes_from_the_as_of_date(api, requests_repo):
    for day, cycle in (("2026-09-09", "W1"), ("2026-09-16", "W2"), ("2026-09-23", "W3"), ("2026-02-28", "ME")):
        status, data = new_request(api, submit=False, as_of_date=day, reporting_cycle="")
        assert status == 201, data
        assert requests_repo.list_batch(data["batch_id"])[0].reporting_cycle == cycle


def test_a_manual_cycle_allows_any_date(api, requests_repo):
    status, data = new_request(api, submit=False, as_of_date="2026-09-10", reporting_cycle="DC")
    assert status == 201 and requests_repo.list_batch(data["batch_id"])[0].reporting_cycle == "DC"
    status, err = new_request(api, submit=False, as_of_date="2026-09-16", reporting_cycle="ME")
    assert status == 400 and "follows from the as-of date" in err["error"]


def test_duplicate_request_rejected(api):
    new_request(api)
    status, data = new_request(api)
    assert status == 400 and "Already open (draft, pending or running)" in data["error"]


# ---------------------------------------------------------------- after the worker ran
def test_batch_findings_and_csv_after_validation(api, worker):
    _, data = new_request(api)
    worker.run_once()
    _, batch = api.get(f"/batches/{data['batch_id']}")
    (row,) = batch["rows"]
    assert row["status"] == "SUCCEEDED" and batch["active"] is False
    (snap,) = batch["snapshots"]
    assert snap["records"] == {"ACCEPTED": 48, "REJECTED": 8} and snap["snapshot"]["status"] == "VALIDATED"
    assert batch["generate_from"][0]["request_id"] == row["request_id"]
    assert set(batch["generate_from"][0]["bureaus"]) == {"CIBIL", "CRIF_HIGHMARK", "EQUIFAX", "EXPERIAN"}
    sid = row["snapshot_id"]

    _, findings = api.get(f"/snapshots/{sid}/findings", rule_id="UCRF.TL.CLOSED_WITH_BALANCE")
    assert findings["total"] == 1 and findings["findings"][0]["account_key"].endswith("L00047")
    csv_resp = api.raw(f"/snapshots/{sid}/findings.csv", outcome="REJECT_RECORD")
    lines = csv_resp.get_data(as_text=True).strip().splitlines()
    assert csv_resp.mimetype == "text/csv" and lines[0].startswith("rule_id,outcome")
    assert len(lines) - 1 == 9 and all(",REJECT_RECORD," in line for line in lines[1:])


def test_findings_csv_export_is_a_blob_link_on_azure(make_api, worker):
    class FakeExports:
        def __init__(self):
            self.published = []

        def publish(self, file_name, data, content_type):
            self.published.append((file_name, data, content_type))
            return f"https://st.blob.core.windows.net/exports/abc/{file_name}?sig=x"

    exports = FakeExports()

    plain = make_api()
    _, data = new_request(plain)
    worker.run_once()
    sid = plain.get(f"/batches/{data['batch_id']}")[1]["rows"][0]["snapshot_id"]
    link = make_api(exports=exports).raw(f"/snapshots/{sid}/findings.csv").get_json()
    assert link["url"].startswith("https://st.blob.core.windows.net/exports/") and link["file_name"].startswith(
        "findings-nbfc_001"
    )
    ((name, body, content_type),) = exports.published
    assert name == link["file_name"] and body.startswith(b"rule_id,outcome") and content_type == "text/csv"


def test_generate_from_validation_reuses_snapshot_and_dispatches(api, worker, requests_repo, dispatcher):
    _, data = new_request(api)
    worker.run_once()
    (validated,) = requests_repo.list_batch(data["batch_id"])
    status, gen = api.post(
        f"/batches/{data['batch_id']}/generate", request_id=validated.request_id, bureau_codes=["CIBIL", "EXPERIAN"]
    )
    rows = requests_repo.list_batch(gen["batch_id"])
    assert status == 201 and len(rows) == 2
    assert all(r.run_mode is M.GENERATE and r.snapshot_id == validated.snapshot_id for r in rows)
    assert dispatcher.dispatched[-2:] == [r.request_id for r in rows]


def test_files_are_generated_once_and_shown_on_the_validation_request(api, worker, requests_repo):
    _, data = new_request(api)
    worker.run_once()
    (validated,) = requests_repo.list_batch(data["batch_id"])
    path = f"/batches/{data['batch_id']}/generate"
    assert api.get(f"/batches/{data['batch_id']}")[1]["generate_from"]  # offered before
    status, gen = api.post(path, request_id=validated.request_id, bureau_codes=["CIBIL"])
    assert status == 201
    status, err = api.post(path, request_id=validated.request_id, bureau_codes=["EXPERIAN"])
    assert status == 409 and gen["batch_id"] in err["error"] and "new request" in err["error"]
    _, page = api.get(f"/batches/{data['batch_id']}")
    assert page["generate_from"] == [] and [g["bureau_code"] for g in page["generations"]] == ["CIBIL"]
    assert page["active"] is True and page["parent_batch_id"] is None
    assert api.get(f"/batches/{gen['batch_id']}")[1]["parent_batch_id"] == data["batch_id"]  # old links lead back


def test_generate_needs_bureaus_and_profiles(api, engine, worker, requests_repo):
    from app.bureaus.tables import bureau_profile

    _, data = new_request(api)
    worker.run_once()
    (validated,) = requests_repo.list_batch(data["batch_id"])
    path = f"/batches/{data['batch_id']}/generate"
    assert "at least one bureau" in api.post(path, request_id=validated.request_id, bureau_codes=[])[1]["error"]
    with engine.begin() as conn:
        conn.execute(bureau_profile.delete().where(bureau_profile.c.bureau_code == "EXPERIAN"))
    status, err = api.post(path, request_id=validated.request_id, bureau_codes=["EXPERIAN"])
    assert status == 400 and "No active bureau profile" in err["error"]
    assert api.post(path, request_id="x", bureau_codes=["CIBIL"])[0] == 400


def test_cancel_and_retry_dispatches_again(api, requests_repo, dispatcher):
    _, data = new_request(api)
    pending = requests_repo.list_batch(data["batch_id"])[0]
    assert api.post(f"/requests/{pending.request_id}/cancel")[0] == 200
    assert requests_repo.get(pending.request_id).status is S.CANCELLED
    assert api.post(f"/requests/{pending.request_id}/cancel")[0] == 409  # not cancellable twice
    api.post(f"/requests/{pending.request_id}/retry")
    assert requests_repo.get(pending.request_id).status is S.PENDING
    assert dispatcher.dispatched == [pending.request_id, pending.request_id]


def test_dispatch_failure_does_not_lose_the_request(make_api, requests_repo, engine):
    install_golden_views(engine)

    class Broken:
        def dispatch(self, requests):
            list(requests)
            raise RuntimeError("queue unavailable")

    client = make_api(app_dispatcher=Broken()).client
    batch_id = client.post(
        "/api/requests", json={"tenant_id": "nbfc_001", "as_of_date": "2026-09-30", "submission_types": ["CONSUMER"]}
    ).get_json()["batch_id"]
    assert client.post(f"/api/batches/{batch_id}/submit").status_code == 200  # saved; recovery re-dispatches
    assert requests_repo.list_batch(batch_id)[0].status is S.PENDING


def test_download_streams_local_files_only_inside_the_store(api, requests_repo, tmp_path):
    requests_repo.create_batch("nbfc_001", AS_OF, M.GENERATE, ["CONSUMER"], ["CIBIL", "EQUIFAX"], snapshot_id=1)
    outside, inside = tmp_path.parent / "secret.txt", tmp_path / "CIB0000001-01102026-090000.txt"
    outside.write_text("nope")
    inside.write_text("HDR")
    bad = requests_repo.claim_next("w")
    requests_repo.mark_succeeded(bad.request_id, RequestResult(output_file_path=str(outside)))
    assert api.get(f"/requests/{bad.request_id}/file")[0] == 404
    good = requests_repo.claim_next("w")
    requests_repo.mark_succeeded(
        good.request_id, RequestResult(output_file_name=inside.name, output_file_path=str(inside))
    )
    resp = api.raw(f"/requests/{good.request_id}/file")
    assert resp.status_code == 200 and resp.get_data() == b"HDR"
    assert inside.name in resp.headers["Content-Disposition"]


# ---------------------------------------------------------------- tracker, files, findings actions, audit
@pytest.fixture
def validated(worker, requests_repo):
    """A validated nbfc_001 request with CIBIL and Experian files."""
    v = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])
    worker.run_once()
    row = requests_repo.list_batch(v)[0]
    g = requests_repo.create_batch(
        "nbfc_001", AS_OF, M.GENERATE, [T.CONSUMER], [B.CIBIL, B.EXPERIAN], snapshot_id=row.snapshot_id
    )
    worker.run_once()
    return row, g


def test_files_lists_generated_files(api, validated):
    _, g = validated
    _, files = api.get("/files")
    assert files["total"] == 2 and {f["batch_id"] for f in files["files"]} == {g}
    assert all(f["has_file"] and f["output_file_name"] for f in files["files"])
    assert api.get("/files", bureau="CIBIL")[1]["total"] == 1
    assert api.get("/files", bureau="NOPE")[0] == 400


def test_tracker_lists_requests_with_their_files(api, validated):
    row, _g = validated

    def ids(**q):
        return [b["batch_id"] for b in api.get("/batches", **q)[1]["requests"]]

    assert ids() == [row.batch_id]  # the generation is part of the request
    (req,) = api.get("/batches")[1]["requests"]
    assert sorted(req["files"]) == ["CIBIL", "EXPERIAN"] and req["stage"] == "COMPLETED"
    assert ids(bureau="EXPERIAN", stage="COMPLETED", type="CONSUMER") == [row.batch_id]
    assert ids(stage="FAILED") == [] and ids(**{"from": "2030-01-01"}) == [] and ids(type="COMMERCIAL") == []
    assert api.get("/batches", **{"from": "not-a-date"})[0] == 400
    assert api.get("/batches", tenant="nbfc_002")[1]["total"] == 0


def test_exclude_from_findings_goes_into_one_draft(api, validated, engine, requests_repo):
    row, _ = validated
    _, findings = api.get(f"/snapshots/{row.snapshot_id}/findings")
    keys = list(dict.fromkeys(f["account_key"] for f in findings["findings"] if f["account_key"]))
    for key in keys[:2] + keys[:1]:  # the third is a duplicate
        status, data = api.post(f"/snapshots/{row.snapshot_id}/exclude", account_key=key)
        assert status == 200
    drafts = [b for b in requests_repo.list_batches() if b.status.value == "DRAFT"]
    assert len(drafts) == 1 and data["batch_id"] == drafts[0].batch_id  # one draft collects them
    (draft_row,) = requests_repo.list_batch(drafts[0].batch_id)
    adjustments = AdjustmentRepository(engine).list(AdjustmentScope.of(draft_row))
    assert len(adjustments) == 2 and all(a.reason.startswith("From findings") for a in adjustments)


def loan_key(api, snapshot_id) -> str:
    return next(
        f["account_key"] for f in api.get(f"/snapshots/{snapshot_id}/findings")[1]["findings"] if f["account_key"]
    )


def test_override_from_findings_returns_the_draft_to_prefill(api, validated):
    row, _ = validated
    key = loan_key(api, row.snapshot_id)
    status, data = api.post(f"/snapshots/{row.snapshot_id}/override", account_key=key, field="amount_overdue")
    assert status == 200 and data["type"] == "CONSUMER" and data["field"] == "amount_overdue" and data["account"]
    assert api.get(f"/batches/{data['batch_id']}/exceptions")[1]["is_draft"] is True


def test_findings_actions_blocked_while_a_run_is_open(api, validated, requests_repo):
    row, _ = validated
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER])  # pending, not a draft
    key = loan_key(api, row.snapshot_id)
    status, err = api.post(f"/snapshots/{row.snapshot_id}/exclude", account_key=key)
    assert status == 409 and "Cannot open a new request" in err["error"]
    assert api.post(f"/snapshots/{row.snapshot_id}/exclude", account_key="NOPE")[0] == 404


def test_audit_filters(api, engine):
    from app import audit

    audit.record(engine, "alice", "ADJUSTMENT_ADDED", tenant_id="nbfc_001", target="b-1")
    audit.record(engine, "bob", "FILE_DOWNLOADED", tenant_id="nbfc_001", target="7")

    def who(**q):
        return [e["actor"] for e in api.get("/audit", **q)[1]["events"]]

    assert who(actor="alice") == ["alice"] and who(action="FILE_DOWNLOADED") == ["bob"]
    assert [e["target"] for e in api.get("/audit", target="b-1")[1]["events"]] == ["b-1"]
    assert set(api.get("/audit")[1]["actors"]) == {"alice", "bob"}


# ---------------------------------------------------------------- exceptions step (LMS schema on the app server)
@pytest.fixture
def same_server(worker):
    """The source views in the app database (adjustments are joined in SQL): the same worker as above."""
    return worker


def statuses(api, batch_id, **q) -> list[str]:
    return [a["status"] for a in api.get(f"/batches/{batch_id}/exceptions", **q)[1]["adjustments"]]


def test_capture_and_remove(api, same_server, engine):
    _, data = new_request(api, submit=False)
    base = f"/batches/{data['batch_id']}/exceptions"
    api.post(f"{base}/exclude", kind="account", keys=f"{JOINT_LOAN}\nX-1, X-2", reason="disputed")
    assert "Already excluded: X-1" in api.post(f"{base}/exclude", kind="account", keys="X-1")[1]["message"]
    api.post(f"{base}/exclude", kind="customer", keys="C00001")
    status, err = api.post(f"{base}/override", account_number=CLEAN_LOAN, field="days_past_due", value="4.5")
    assert status == 400 and "whole number" in err["error"]
    api.post(f"{base}/override", account_number=CLEAN_LOAN, field="days_past_due", value="45", reason="late")
    _, page = api.get(base)
    assert [a["status"] for a in page["adjustments"]] == ["WILL_APPLY"] * 5
    assert {a["kind"] for a in page["adjustments"]} == {"EXCLUDE_ACCOUNT", "EXCLUDE_CUSTOMER", "OVERRIDE"}
    removed = page["adjustments"][0]["adjustment_id"]
    api.post(f"{base}/{removed}/remove")
    assert statuses(api, data["batch_id"]) == ["WILL_APPLY"] * 4
    assert "REMOVED" in statuses(api, data["batch_id"], history=1)
    assert actions(engine) == ["RUN_CREATED"] + ["ADJUSTMENT_ADDED"] * 3 + ["ADJUSTMENT_REMOVED"]


def test_submit_validate_confirm_generate(api, same_server, engine, requests_repo, tmp_path):
    _, data = new_request(api, submit=False)
    batch_id, base = data["batch_id"], f"/batches/{data['batch_id']}/exceptions"
    api.post(f"{base}/exclude", kind="account", keys=f"{JOINT_LOAN}\nNO-SUCH-LOAN")
    api.post(f"{base}/override", account_number=CLEAN_LOAN, field="amount_overdue", value="1500")
    api.post(f"/batches/{batch_id}/submit")
    same_server.run_once()
    (row,) = requests_repo.list_batch(batch_id)
    assert row.status is S.SUCCEEDED, row.error_message
    assert (row.total_records, row.excluded_accounts, row.overridden_accounts) == (54, 1, 1)  # validated the rest

    _, page = api.get(base)
    by_key = {a["key"]: a for a in page["adjustments"]}
    assert page["is_draft"] is False and by_key["NO-SUCH-LOAN"]["status"] == "NOT_FOUND"
    assert by_key[JOINT_LOAN]["status"] == "APPLIED" and by_key[CLEAN_LOAN]["status"] == "APPLIED"
    assert (by_key[CLEAN_LOAN]["old_value"], by_key[CLEAN_LOAN]["override_value"]) == ("0.00", "1500.00")
    status, err = api.post(f"{base}/exclude", kind="account", keys="X-9")
    assert status == 409 and "locked" in err["error"]

    adjusted = api.get(f"/batches/{batch_id}")[1]["snapshots"][0]["adjusted"]
    assert (adjusted["excluded_accounts"], adjusted["overridden_accounts"], adjusted["not_found"]) == (1, 1, 1)
    gen = dict(request_id=row.request_id, bureau_codes=["CIBIL"])
    status, err = api.post(f"/batches/{batch_id}/generate", **gen)
    assert status == 400 and "Confirm the excluded loans" in err["error"]
    _, ok = api.post(f"/batches/{batch_id}/generate", confirm_adjustments=True, **gen)
    same_server.run_once()
    (file_row,) = requests_repo.list_batch(ok["batch_id"])
    assert file_row.status is S.SUCCEEDED and file_row.excluded_accounts == 1
    assert file_row.exceptions_file_path is None  # consumer: three outputs, no CSV
    assert all(
        Path(p).is_file() for p in (file_row.output_file_path, file_row.excel_file_path, file_row.review_file_path)
    )
    assert {"RUN_SUBMITTED", "ADJUSTMENTS_CONFIRMED"} <= set(actions(engine))


def test_submit_without_exceptions_and_discard(api, same_server, requests_repo):
    _, data = new_request(api)
    same_server.run_once()
    (row,) = requests_repo.list_batch(data["batch_id"])
    assert row.status is S.SUCCEEDED and (row.total_records, row.excluded_accounts) == (56, 0)
    assert api.post(f"/batches/{data['batch_id']}/submit")[0] == 409  # not a draft any more

    _, other = new_request(api, submit=False)
    api.post(f"/batches/{other['batch_id']}/discard")
    assert requests_repo.list_batch(other["batch_id"])[0].status is S.CANCELLED
    assert new_request(api, submit=False)[0] == 201  # the date is free again


# ---------------------------------------------------------------- rules & policies
RULE = "UCRF.ID.NUMBER_FORMAT"  # default: on, REJECT_SEGMENT


def effective(engine, tenant="nbfc_001", rule_id=RULE):
    return next(r for r in rules.load_rule_set(engine, tenant, "CONSUMER") if r.rule_id == rule_id)


def test_rule_catalogue_without_an_nbfc(make_api):
    _, page = make_api().get("/rules", type="CONSUMER")
    rule = next(r for r in page["rules"] if r["rule_id"] == RULE)
    assert page["tenant_id"] is None and rule["message"].startswith("ID number is not valid")
    assert rule["sql"] or rule["python"]  # the logic is visible
    assert any(r["python"] for r in page["rules"]) and any(r["sql"] for r in page["rules"])
    assert make_api().post(f"/rules/{RULE}", type="CONSUMER", enabled=True, reason="r")[0] == 400  # needs an NBFC


def test_rule_change_needs_a_reason_and_is_per_nbfc(make_api, engine):
    api = make_api(tenant="nbfc_001")
    status, err = api.post(f"/rules/{RULE}", type="CONSUMER", enabled=True, outcome="WARN")
    assert status == 400 and "give a reason" in err["error"]
    assert effective(engine).outcome is Outcome.REJECT_SEGMENT
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=True, outcome="WARN", reason="NBFC holds legacy IDs")
    assert effective(engine).outcome is Outcome.WARN and effective(engine).enabled
    assert effective(engine, "nbfc_002").outcome is Outcome.REJECT_SEGMENT  # other NBFC untouched
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=False, outcome="WARN", reason="not used by this NBFC")
    assert not effective(engine).enabled
    changed = [r for r in api.get("/rules", type="CONSUMER")[1]["rules"] if r["changed"]]
    assert [r["rule_id"] for r in changed] == [RULE]
    with engine.connect() as conn:
        events = conn.execute(
            select(audit_event).where(audit_event.c.action == "RULE_POLICY_CHANGED").order_by(audit_event.c.event_id)
        ).all()
    assert [e.target for e in events] == [RULE, RULE] and "legacy IDs" in events[0].details


def test_rule_back_to_default_and_reset(make_api, engine):
    api = make_api(tenant="nbfc_001")
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=True, outcome="WARN", reason="r")
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=True, outcome="REJECT_SEGMENT", reason="back")
    assert rules.policies(engine, "nbfc_001") == {}
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=False, outcome="REJECT_SEGMENT", reason="off")
    api.post(f"/rules/{RULE}/reset", type="CONSUMER")
    assert rules.policies(engine, "nbfc_001") == {} and effective(engine).enabled
    assert api.post("/rules/NOPE", type="CONSUMER", enabled=True, reason="r")[0] == 404
    assert api.post(f"/rules/{RULE}", type="CONSUMER", enabled=True, outcome="BOGUS", reason="r")[0] == 400


# ---------------------------------------------------------------- SSO identity (auth = entra)
TENANT_ID, CLIENT_ID = "11111111-2222-3333-4444-555555555555", "client-1"


def _rsa_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING_KEY, OTHER_KEY = _rsa_key(), _rsa_key()


def entra_token(key=None, **claims) -> str:
    import time

    import jwt

    now = int(time.time())
    claims = {
        "aud": CLIENT_ID,
        "iss": f"https://login.microsoftonline.com/{TENANT_ID}/v2.0",
        "tid": TENANT_ID,
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
        "oid": "o-1",
        "preferred_username": "Asha.K@Company.in",
    } | claims
    return jwt.encode(
        {k: v for k, v in claims.items() if v is not None}, key or SIGNING_KEY, algorithm="RS256", headers={"kid": "k1"}
    )


def bearer(key=None, **claims) -> dict:
    return {"Authorization": f"Bearer {entra_token(key, **claims)}"}


@pytest.fixture
def sso(make_api, engine, monkeypatch):
    from app.api import auth

    signing = type("K", (), {"key": SIGNING_KEY.public_key()})()
    monkeypatch.setattr(
        auth,
        "_signing_keys",
        lambda jwks_uri: type("C", (), {"get_signing_key_from_jwt": staticmethod(lambda token: signing)})(),
    )
    UserRepository(engine).add("asha.k", "Asha K", "asha.k@company.in", is_admin=True)
    return make_api(auth="entra", entra_tenant_id=TENANT_ID, entra_client_id=CLIENT_ID)


def test_sso_verifies_the_entra_token(sso):
    assert sso.get("/me")[0] == 401  # no token
    assert sso.get("/me", headers=bearer(aud="other-client"))[0] == 401
    assert sso.get("/me", headers=bearer(aud=f"api://{CLIENT_ID}"))[0] == 200  # access token for the API
    assert sso.get("/me", headers=bearer(iss="https://login.microsoftonline.com/other/v2.0"))[0] == 401
    assert sso.get("/me", headers=bearer(iss=f"https://sts.windows.net/{TENANT_ID}/"))[0] == 200  # v1 access token
    assert sso.get("/me", headers=bearer(tid="other-tenant"))[0] == 401
    assert sso.get("/me", headers=bearer(exp=1))[0] == 401  # expired
    assert sso.get("/me", headers=bearer(key=OTHER_KEY))[0] == 401  # not signed by the tenant's key
    header, _payload, sig = entra_token().split(".")
    forged = entra_token(preferred_username="boss@company.in").split(".")[1]
    assert sso.get("/me", headers={"Authorization": f"Bearer {header}.{forged}.{sig}"})[0] == 401
    assert sso.get("/me", headers={"Authorization": "Bearer not-a-jwt"})[0] == 401
    status, me = sso.get("/me", headers=bearer(preferred_username=None, upn="ASHA.K@company.in"))
    assert status == 200 and me["username"] == "asha.k"  # v1 tokens carry upn
    assert sso.get("/me", headers=bearer(email="asha.k@company.in", preferred_username="x@y.in"))[0] == 200


def test_sso_needs_its_settings(make_api):
    assert make_api(auth="entra").get("/me")[0] == 500


def test_sso_requires_a_registered_active_user(sso, engine):
    status, err = sso.get("/me", headers=bearer(preferred_username="someone@company.in"))
    assert status == 403 and "not registered" in err["error"]
    status, me = sso.get("/me", headers=bearer())
    assert status == 200 and me == {
        "username": "asha.k",
        "display_name": "Asha K",
        "email": "asha.k@company.in",
        "is_admin": True,
        "last_login_at": None,
    }  # never signed in before
    UserRepository(engine).set_active("asha.k", False)
    assert sso.get("/me", headers=bearer())[0] == 403  # leaver: disabled in the app
    assert actions(engine).count("LOGIN_DENIED") == 2


def test_sso_user_is_recorded_on_actions_and_logins(sso, engine, requests_repo):
    install_golden_views(engine)
    sso.post("/session", headers=bearer())
    status, data = sso.post(
        "/requests", headers=bearer(), tenant_id="nbfc_001", as_of_date="2026-09-30", submission_types=["CONSUMER"]
    )
    assert status == 201 and requests_repo.list_batch(data["batch_id"])[0].requested_by == "asha.k"
    sso.post("/session/end", headers=bearer())
    assert actions(engine) == ["LOGIN", "RUN_CREATED", "LOGOUT"]
    assert UserRepository(engine).find("asha.k").last_login_at is not None


USERS_GROUP, ADMINS_GROUP = "9684650a-3216-4662-a715-7ba0e38ac7e3", "ca4204eb-9105-45c3-9096-4a150c31e5b0"


@pytest.fixture
def grouped(make_api, monkeypatch):
    """Access from Entra security groups (the groups claim), as configured for rarfincare."""
    from app.api import auth

    signing = type("K", (), {"key": SIGNING_KEY.public_key()})()
    monkeypatch.setattr(
        auth,
        "_signing_keys",
        lambda jwks_uri: type("C", (), {"get_signing_key_from_jwt": staticmethod(lambda token: signing)})(),
    )

    def make(**kw):
        return make_api(
            auth="entra",
            entra_tenant_id=TENANT_ID,
            entra_client_id=CLIENT_ID,
            entra_user_group_id=USERS_GROUP,
            entra_admin_group_id=ADMINS_GROUP,
            **kw,
        )

    return make


def test_group_members_get_in_and_are_added_at_first_sign_in(grouped, engine):
    api = grouped()
    status, me = api.get(
        "/me",
        headers=bearer(groups=[USERS_GROUP, "other"], name="Ravi Shankar", preferred_username="Ravi.S@company.in"),
    )
    assert status == 200 and me["is_admin"] is False and me["email"] == "ravi.s@company.in"
    user = UserRepository(engine).find_by_email("ravi.s@company.in")
    assert user.username == "ravi.s" and user.display_name == "Ravi Shankar"
    assert "USER_ADDED" in actions(engine)
    status, me = api.get("/me", headers=bearer(groups=[ADMINS_GROUP.upper()], preferred_username="boss@company.in"))
    assert status == 200 and me["is_admin"] is True  # the Admins group makes an administrator
    assert api.get("/me", headers=bearer(groups=[USERS_GROUP], preferred_username="Ravi.S@company.in"))[0] == 200


def test_outside_the_groups_is_refused_unless_the_switch_over_fallback_lets_an_app_user_in(grouped, engine):
    UserRepository(engine).add("asha.k", "Asha K", "asha.k@company.in", is_admin=True)
    api = grouped()
    assert api.get("/me", headers=bearer(groups=["other"], preferred_username="someone@company.in"))[0] == 403
    status, me = api.get("/me", headers=bearer(groups=["other"]))  # listed app user, fallback on (default)
    assert status == 200 and me["is_admin"] is True
    strict = grouped(app_user_fallback=False)
    status, err = strict.get("/me", headers=bearer(groups=[]))
    assert status == 403 and "Bureau Submission group" in err["error"]
    assert actions(engine).count("LOGIN_DENIED") == 2


def test_a_user_disabled_in_the_app_stays_out_even_in_the_group(grouped, engine):
    repo = UserRepository(engine)
    repo.add("asha.k", "Asha K", "asha.k@company.in")
    repo.set_active("asha.k", False)
    assert grouped().get("/me", headers=bearer(groups=[USERS_GROUP]))[0] == 403


def test_usernames_made_at_sign_in_do_not_collide(grouped, engine):
    UserRepository(engine).add("ravi.s", "Ravi S", "ravi.s@other.in")
    api = grouped()
    assert api.get("/me", headers=bearer(groups=[USERS_GROUP], preferred_username="ravi.s@company.in"))[0] == 200
    assert UserRepository(engine).find_by_email("ravi.s@company.in").username == "ravi.s-2"


def test_only_administrators_change_rules(grouped, engine):
    api = grouped()
    user, admin = bearer(groups=[USERS_GROUP], preferred_username="ravi.s@company.in"), bearer(groups=[ADMINS_GROUP])
    assert api.get("/rules", headers=user)[0] == 200  # everyone may read the rules
    rule = api.get("/rules", headers=user)[1]["rules"][0]["rule_id"]
    status, err = api.post(f"/rules/{rule}", headers=user, enabled=False, reason="test")
    assert status == 403 and "administrators" in err["error"]
    for path in ("/rules/custom", "/rules/custom/preview", "/rules/parameters/x", f"/rules/{rule}/reset"):
        assert api.post(path, headers=user)[0] == 403
    assert "ADMIN_DENIED" in actions(engine)
    status, body = api.post(f"/rules/{rule}", headers=admin, enabled=False, reason="test")
    assert status != 403 and "administrators" not in str(body)  # an admin passes the check
    assert api.get("/audit", headers=user)[0] == 200  # the audit page is for everyone


def test_bureau_profiles_are_listed_for_all_and_changed_by_admins(grouped, engine, bureau_repo):
    seed_missing(bureau_repo, "nbfc_001")
    api = grouped(tenant="nbfc_001")
    user, admin = bearer(groups=[USERS_GROUP], preferred_username="ravi.s@company.in"), bearer(groups=[ADMINS_GROUP])
    status, data = api.get("/bureaus", headers=user)
    assert status == 200 and len(data["profiles"]) == 8  # 4 bureaus x consumer, commercial
    cibil = next(p for p in data["profiles"] if p["bureau_code"] == "CIBIL" and p["submission_type"] == "CONSUMER")
    assert cibil["placeholder"] is True and cibil["example_file_name"].startswith(cibil["member_id"])
    change = {
        "member_id": "NB12345678",
        "member_short_name": "RAR FINCARE",
        "header_segment_tag": "TUDF",
        "file_name_pattern": "{member_id}_{cycle}_{as_of:%d%m%Y}.txt",
        "is_active": True,
        "reason": "issued by CIBIL",
    }
    assert api.post("/bureaus/CONSUMER/CIBIL", headers=user, **change)[0] == 403  # users read only
    status, saved = api.post("/bureaus/CONSUMER/CIBIL", headers=admin, **change)
    assert status == 200 and saved["profile"]["placeholder"] is False
    assert saved["profile"]["example_file_name"].startswith("NB12345678_ME_")
    assert bureau_repo.get("nbfc_001", "CIBIL", "CONSUMER").member_short_name == "RAR FINCARE"
    assert "BUREAU_PROFILE_CHANGED" in actions(engine)


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("member_id", "AB 12", "Member ID"),
        ("member_short_name", "X" * 17, "16 characters"),
        ("header_segment_tag", "TOOLONG", "Header tag"),
        ("header_segment_tag", "", "required for consumer"),
        ("file_name_pattern", "a/b-{member_id}.txt", "File name pattern"),
        ("file_name_pattern", "{nope}.txt", "Allowed"),
        ("reason", "", "reason"),
    ],
)
def test_bureau_profile_changes_are_checked(grouped, bureau_repo, field, value, error):
    seed_missing(bureau_repo, "nbfc_001")
    change = {
        "member_id": "NB12345678",
        "member_short_name": "RAR",
        "header_segment_tag": "TUDF",
        "file_name_pattern": "{member_id}.txt",
        "reason": "r",
    } | {field: value}
    status, err = grouped(tenant="nbfc_001").post(
        "/bureaus/CONSUMER/CIBIL", headers=bearer(groups=[ADMINS_GROUP]), **change
    )
    assert status == 400 and error in err["error"]
