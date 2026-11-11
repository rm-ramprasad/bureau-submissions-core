"""API pages beyond the request flow: dashboard and tracker per type, the attention bell, custom rules and rule
parameters on the Rules page, findings by category (ported from the reference's page tests)."""

from datetime import date

from sqlalchemy import select
from test_api import worker  # noqa: F401  (fixture: the real pipeline over golden-backed views)

from app.audit import audit_event
from app.enums import RunMode as M
from app.enums import SubmissionType as T
from app.validation import custom as C
from app.validation import settings as P

AS_OF = date(2026, 9, 30)
FORM = {
    "type": "CONSUMER", "name": "MIN_SANCTION", "field": "account.high_credit_amount", "check": "range",
    "min": "10000000", "outcome": "WARN", "message": "Sanction below 1 crore",
}  # fmt: skip
RULE_ID = "CUSTOM.NBFC_001.MIN_SANCTION"


def actions(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(select(audit_event.c.action).order_by(audit_event.c.event_id)).scalars())


def validate(worker, requests_repo, submission_type):  # noqa: F811
    batch = requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [submission_type])
    worker.run_once()
    return requests_repo.list_batch(batch)[0]


def test_dashboard_api_switches_type(make_api, worker, requests_repo):  # noqa: F811
    api = make_api()
    validate(worker, requests_repo, T.COMMERCIAL)
    requests_repo.create_batch("nbfc_001", AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)
    _, consumer = api.get("/dashboard", period="all", tenant="nbfc_001")
    assert consumer["type"] == "CONSUMER" and consumer["raised"] == 1  # default: the first type
    assert [t["value"] for t in consumer["types"]] == ["CONSUMER", "COMMERCIAL", "MFI"]
    _, commercial = api.get("/dashboard", period="all", tenant="nbfc_001", type="COMMERCIAL")
    assert commercial["type"] == "COMMERCIAL" and commercial["records"] > 0
    assert {s["value"]: s["count"] for s in commercial["stages"]}["VALIDATED"] == 1
    _, tracker = api.get("/batches", stage="VALIDATED", period="all", type="COMMERCIAL")
    assert len(tracker["requests"]) == 1
    assert api.get("/batches", stage="VALIDATED", period="all", type="CONSUMER")[1]["requests"] == []
    assert api.get("/dashboard", type="NOPE")[0] == 400


def test_dashboard_api_and_click_through(make_api, worker, requests_repo):  # noqa: F811
    api = make_api()
    v = validate(worker, requests_repo, T.CONSUMER).batch_id
    requests_repo.create_batch("nbfc_002", AS_OF, M.VALIDATE, [T.CONSUMER], draft=True)
    status, board = api.get("/dashboard", period="all")
    assert status == 200 and board["raised"] == 2 and board["period"] == "all"
    counts = {s["value"]: s["count"] for s in board["stages"]}
    assert counts["AWAITING_EXCEPTIONS"] == 1 and counts["VALIDATED"] == 1
    assert {n["tenant_id"] for n in board["nbfc_status"]} == {"nbfc_001", "nbfc_002"}
    _, tracker = api.get("/batches", stage="VALIDATED", period="all")
    assert [r["batch_id"] for r in tracker["requests"]] == [v]
    assert api.get("/batches", stage="COMPLETED", period="all")[1]["requests"] == []
    assert api.get("/batches", stage="NOPE")[0] == 400
    assert api.get("/dashboard", period="bogus")[1]["period"] == "month"  # falls back to this month
    _, one = api.get("/dashboard", period="all", tenant="nbfc_002")
    assert one["raised"] == 1 and one["nbfc_status"] is None


def test_the_bell_lists_what_needs_attention_for_every_type(make_api, engine):
    from app.requests import RequestRepository

    api, requests_repo = make_api(), RequestRepository(engine)  # real clock: the bell looks back 30 days
    assert api.get("/attention")[1] == {"count": 0, "items": []}
    batch = requests_repo.create_batch("nbfc_001", date(2026, 9, 30), "VALIDATE", ["CONSUMER"])
    (row,) = requests_repo.list_batch(batch)
    requests_repo.claim_next("w")
    requests_repo.mark_failed(row.request_id, "boom")
    _, bell = api.get("/attention")
    assert bell["count"] == 1 and bell["items"][0]["batch_id"] == batch and bell["items"][0]["type"] == "CONSUMER"
    assert bell["items"][0]["reason"].startswith("Failed")


def test_custom_rules_preview_save_edit_delete(make_api, engine, worker, requests_repo):  # noqa: F811
    api = make_api(tenant="nbfc_001")
    status, preview = api.post("/rules/custom/preview", **FORM)
    assert status == 200 and preview["snapshot"] is None and "high_credit_amount" in preview["sql"]
    validate(worker, requests_repo, T.CONSUMER)
    _, preview = api.post("/rules/custom/preview", **FORM)
    assert isinstance(preview["count"], int) and preview["snapshot"]["snapshot_id"]
    assert C.definitions(engine, "nbfc_001", "CONSUMER") == {}  # nothing saved

    status, err = api.post("/rules/custom", **FORM)
    assert status == 400 and "Give a reason" in err["error"]
    status, saved = api.post("/rules/custom", reason="credit policy", **FORM)
    assert status == 200 and saved["rule_id"] == RULE_ID
    _, page = api.get("/rules", type="CONSUMER")
    assert page["custom"][RULE_ID]["describe"] == "account.high_credit_amount is between 10000000 and …"
    assert next(r for r in page["rules"] if r["rule_id"] == RULE_ID)["kind_label"] == "Custom (form)"

    api.post("/rules/custom", reason="lowered", editing=RULE_ID, **FORM | {"min": "500000"})
    assert C.definitions(engine, "nbfc_001", "CONSUMER")[RULE_ID].min == "500000"
    assert api.post("/rules/custom", reason="r", editing=RULE_ID, **FORM | {"name": "OTHER"})[0] == 400

    api.post(f"/rules/custom/{RULE_ID}/delete", type="CONSUMER")
    assert C.definitions(engine, "nbfc_001", "CONSUMER") == {}
    assert api.post(f"/rules/custom/{RULE_ID}/delete", type="CONSUMER")[0] == 404
    assert [a for a in actions(engine) if a.startswith("CUSTOM_RULE")] == [
        "CUSTOM_RULE_ADDED",
        "CUSTOM_RULE_CHANGED",
        "CUSTOM_RULE_DELETED",
    ]


def messages(api) -> str:
    return " ".join(r["message"] for r in api.get("/rules", type="CONSUMER")[1]["rules"])


def test_parameters_through_the_api(make_api, engine):
    api = make_api(tenant="nbfc_001")
    _, page = api.get("/rules", type="CONSUMER")
    labels = {p["name"]: p["label"] for p in page["parameters"]}
    assert labels["phone_or_id_required_from"] == "Phone or ID required from"
    assert "on or after 01-05-2005" in messages(api)  # rule messages show the values
    status, err = api.post("/rules/parameters/min_address_length", type="CONSUMER", value="5")
    assert status == 400 and "give a reason" in err["error"]
    status, err = api.post("/rules/parameters/id_pattern_pan", type="CONSUMER", value="^[A-Z", reason="r")
    assert status == 400 and "not a valid regular expression" in err["error"]
    api.post("/rules/parameters/dob_gender_required_from", type="CONSUMER", value="2010-01-01", reason="NBFC policy")
    assert "on or after 01-01-2010" in messages(api)
    param = next(
        p for p in api.get("/rules", type="CONSUMER")[1]["parameters"] if p["name"] == "dob_gender_required_from"
    )
    assert param["value"] == "2010-01-01" and param["override"]["updated_by"] == "internal-user"
    assert P.load_parameters(engine, "nbfc_001", "CONSUMER")["dob_gender_required_from"] == date(2010, 1, 1)
    api.post("/rules/parameters/dob_gender_required_from/reset", type="CONSUMER")
    assert P.overrides(engine, "nbfc_001", "CONSUMER") == {}
    assert actions(engine) == ["RULE_PARAMETER_CHANGED", "RULE_PARAMETER_RESET"]
    assert api.post("/rules/parameters/nope", type="CONSUMER", value="1")[0] == 404
    assert make_api().post("/rules/parameters/min_address_length", value="5", reason="r")[0] == 400  # no NBFC


def test_findings_by_category(make_api, worker, requests_repo):  # noqa: F811
    row = validate(worker, requests_repo, T.CONSUMER)
    api = make_api()
    status, data = api.get(f"/snapshots/{row.snapshot_id}/findings", category="CONTACT")
    assert status == 200 and data["total"] > 0
    assert data["report"]["kpis"]["records"] > 0
    assert api.get(f"/snapshots/{row.snapshot_id}/findings", category="NOPE")[0] == 400
