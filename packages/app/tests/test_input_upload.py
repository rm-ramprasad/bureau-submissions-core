"""Uploading a request's input workbooks through the API (app.api.inputs)."""

import io
import sys
from datetime import date
from pathlib import Path

import pytest
from app_helpers import install_golden_views
from sqlalchemy import select
from test_api import new_request

from app.audit import audit_event
from app.ports.files import LocalFileStore
from canonical.staging.tables import STAGING_TABLES, input_load
from canonical.testing import add_view_accounts

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "sources" / "tests"))
from src_helpers import workbook

ACCOUNT = "000110710000006"
VALID = {
    "curr_new_account_no": ACCOUNT,
    "consumer_name": "ARIVUMANI CK",
    "date_of_birth": "23031981",
    "gender_label": "Male",
    "income_tax_id_number": "BIVPA9062Q",
    "address_line_1": "4, THULUKANATHAMMAN KOIL ST",
    "state_code_1_label": "Tamil Nadu",
    "pin_code_1": "600005",
    "account_type": "02",
    "account_type_label": "Housing Loan",
    "ownership_indicator_label": "Individual",
}
GOOD = workbook("CONSUMER", {"Consumer": [VALID, {"consumer_name": "no account number"}]})
BAD = workbook("CONSUMER", headers={"Consumer": ["Consumer Name"]})
FAILS_CHECKS = workbook("CONSUMER", {"Consumer": [{**VALID, "date_of_birth": "2404199"}]})


@pytest.fixture
def api(make_api, tmp_path, engine):
    install_golden_views(engine)  # the types are set up (their views exist)
    add_view_accounts(engine, date(2026, 9, 30), ACCOUNT)  # new_request's as-of date
    return make_api(store=LocalFileStore(tmp_path / "files"), require_inputs=True)


def upload(api, batch_id, data, name="Consumer.xlsx", submission_type="CONSUMER"):
    resp = api.client.post(
        f"/api/batches/{batch_id}/inputs/{submission_type}",
        data={"file": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )
    return resp.status_code, resp.get_json(silent=True)


def draft(api, types=("CONSUMER",)) -> str:
    status, data = new_request(api, submit=False, submission_types=list(types))
    assert status == 201, data
    return data["batch_id"]


def test_upload_loads_the_workbook_and_shows_the_result(api, engine, tmp_path):
    batch_id = draft(api)
    status, inputs = api.get(f"/batches/{batch_id}/inputs")
    assert status == 200 and inputs["is_draft"] is True
    assert inputs["inputs"] == [
        {"submission_type": "CONSUMER", "needs_workbook": True, "on_customer_tables": False, "latest": None}
    ]
    status, data = upload(api, batch_id, GOOD)
    assert status == 201 and "loaded and checked: 1 rows from 1 sheet(s)" in data["message"]
    assert "1 warning(s)" in data["message"]
    latest = data["latest"]
    assert latest["status"] == "LOADED" and latest["row_counts"] == {"Consumer": 1}
    assert latest["file_name"] == "Consumer.xlsx" and latest["loaded_by"] == "internal-user"
    assert [i["code"] for i in latest["issues"]] == ["KEY_MISSING"]
    with engine.connect() as conn:
        load = conn.execute(select(input_load)).one()
        assert load.batch_id == batch_id and load.file_uri.endswith(".xlsx")
        (row,) = conn.execute(select(STAGING_TABLES["CONSUMER"]["Consumer"])).all()
        assert row.consumer_name == "ARIVUMANI CK" and row.load_id == load.load_id
        assert conn.execute(select(audit_event.c.action).where(audit_event.c.action == "INPUT_UPLOADED")).scalar()
    stored = list((tmp_path / "files").rglob("*.xlsx"))
    assert len(stored) == 1 and stored[0].read_bytes() == GOOD and f"{batch_id}" in str(stored[0])
    assert api.get(f"/batches/{batch_id}/inputs")[1]["inputs"][0]["latest"]["load_id"] == load.load_id


def test_a_bad_workbook_is_refused_and_shown(api):
    batch_id = draft(api)
    status, data = upload(api, batch_id, BAD)
    assert status == 422 and "missing column" in data["message"]
    assert data["latest"]["status"] == "FAILED" and data["latest"]["issues"][0]["code"] == "COLUMN_MISSING"


@pytest.mark.parametrize(
    "name, data, error",
    [
        ("Consumer.csv", GOOD, ".xlsx"),
        ("Consumer.xlsx", b"", "empty"),
        ("", GOOD, "Choose the workbook"),
    ],
)
def test_upload_checks_the_file(api, name, data, error):
    status, body = upload(api, draft(api), data, name=name)
    assert status == 400 and error in body["error"]


def test_upload_size_limit_is_only_for_this_route(api):
    batch_id = draft(api)
    assert upload(api, batch_id, b"x" * (26 * 1024 * 1024))[0] == 413
    big = api.client.post(
        f"/api/batches/{batch_id}/exceptions/exclude",
        data=b"{" + b" " * (2 * 1024 * 1024) + b"}",
        content_type="application/json",
    )
    assert big.status_code == 413  # the rest of the API keeps 1 MB


def test_submit_needs_a_loaded_workbook_for_every_type(api, dispatcher):
    batch_id = draft(api, ("CONSUMER", "COMMERCIAL"))
    status, err = api.post(f"/batches/{batch_id}/submit")
    assert status == 409 and "Consumer" in err["error"] and "Commercial" in err["error"]
    assert dispatcher.dispatched == []
    upload(api, batch_id, GOOD)
    upload(api, batch_id, BAD)  # the latest upload counts: failed
    assert "Consumer" in api.post(f"/batches/{batch_id}/submit")[1]["error"]
    upload(api, batch_id, GOOD)
    assert upload(api, batch_id, workbook("COMMERCIAL"), "Commercial.xlsx", "COMMERCIAL")[0] == 201
    status, _ = api.post(f"/batches/{batch_id}/submit")
    assert status == 200 and len(dispatcher.dispatched) == 2


def test_uploads_are_locked_after_submit(api):
    batch_id = draft(api)
    upload(api, batch_id, GOOD)
    assert api.post(f"/batches/{batch_id}/submit")[0] == 200
    status, err = upload(api, batch_id, GOOD)
    assert status == 409 and "locked" in err["error"]
    assert api.get(f"/batches/{batch_id}/inputs")[1]["is_draft"] is False


def test_unknown_request_or_type(api):
    assert upload(api, "nope", GOOD)[0] == 404
    batch_id = draft(api)
    assert upload(api, batch_id, GOOD, submission_type="COMMERCIAL")[0] == 404
    assert api.get("/batches/nope/inputs")[0] == 404


def test_the_requirement_can_be_switched_off(make_api, engine):
    install_golden_views(engine)
    api = make_api(require_inputs=False)
    batch_id = draft(api)
    assert api.post(f"/batches/{batch_id}/submit")[0] == 200


def test_blocking_data_quality_problems_stop_the_request_until_fixed(api, dispatcher):
    batch_id = draft(api)
    status, data = upload(api, batch_id, FAILS_CHECKS)
    assert status == 422 and "fail blocking data-quality checks" in data["message"]
    latest = data["latest"]
    assert latest["status"] == "DQ_FAILED" and latest["row_counts"] == {"Consumer": 1}
    dq = [i for i in latest["issues"] if i["code"] == "DQ.DATE_FORMAT"]
    assert dq and dq[0]["severity"] == "ERROR" and dq[0]["source_row"] == 2 and dq[0]["column_name"] == "date_of_birth"
    group = next(g for g in latest["summary"] if g["code"] == "DQ.DATE_FORMAT")  # the per-check summary
    assert (group["severity"], group["sheet"], group["column"], group["count"]) == (
        "ERROR",
        "Consumer",
        "date_of_birth",
        1,
    )
    assert group["title"] == "Date is not valid" and group["rows"][0]["row"] == 2
    assert latest["summary"][0]["severity"] == "ERROR"  # blocking checks first
    status, err = api.post(f"/batches/{batch_id}/submit")
    assert status == 409 and "data-quality checks" in err["error"] and dispatcher.dispatched == []
    assert upload(api, batch_id, GOOD)[0] == 201  # corrected workbook
    assert api.post(f"/batches/{batch_id}/submit")[0] == 200


# ---------------------------------------------------------------- Uploads page (ADR 0002 PR 7)
def upload_page(api, data, as_of="2026-09-30", name="Consumer.xlsx", submission_type="CONSUMER"):
    resp = api.client.post(
        f"/api/uploads/{submission_type}",
        data={"file": (io.BytesIO(data), name), "as_of_date": as_of, "tenant_id": "nbfc_001"},
        content_type="multipart/form-data",
    )
    return resp.status_code, resp.get_json(silent=True)


def test_an_upload_on_the_uploads_page_is_checked_and_kept_for_its_date(api, engine):
    status, data = upload_page(api, GOOD)
    assert status == 201 and "source views" in data["message"]  # consumer is still on the source views
    u = data["upload"]
    assert (u["status"], u["as_of_date"], u["applied_at"], u["source"]) == ("LOADED", "2026-09-30", None, "views")
    with engine.connect() as conn:
        load = conn.execute(select(input_load)).one()
    assert load.batch_id is None and load.as_of_date == date(2026, 9, 30)
    _, listed = api.get("/uploads", tenant_id="nbfc_001")
    assert [x["load_id"] for x in listed["uploads"]] == [u["load_id"]]
    assert {t["submission_type"] for t in listed["types"]} == {"CONSUMER", "COMMERCIAL", "MFI"}
    with engine.connect() as conn:
        assert conn.execute(select(audit_event.c.action).where(audit_event.c.action == "DATA_UPLOADED")).scalar()


def test_a_type_on_the_customer_tables_saves_the_upload_and_its_requests_need_no_workbook(api, engine):
    from app.configstore import settings as S
    from canonical.master.tables import dm_sanction

    S.put(engine, "nbfc_001", "data_source", {"CONSUMER": "master"}, "admin")
    status, data = upload_page(api, GOOD)
    assert status == 201 and "saved in the customer tables" in data["message"], data
    assert data["upload"]["applied_at"] and "accounts" in data["upload"]["apply_summary"]
    with engine.connect() as conn:
        assert conn.execute(select(dm_sanction)).first() is not None
    batch_id = draft(api)
    _, inputs = api.get(f"/batches/{batch_id}/inputs")
    assert inputs["inputs"][0]["needs_workbook"] is False and inputs["inputs"][0]["on_customer_tables"] is True
    assert api.post(f"/batches/{batch_id}/submit")[0] == 200  # no workbook of its own needed


def test_blocking_checks_or_a_bad_file_save_nothing(api, engine):
    from app.configstore import settings as S
    from canonical.master.tables import dm_sanction

    S.put(engine, "nbfc_001", "data_source", {"CONSUMER": "master"}, "admin")
    status, data = upload_page(api, FAILS_CHECKS)
    assert status == 422 and "blocking data-quality checks" in data["message"] and not data["upload"]["applied_at"]
    assert upload_page(api, BAD)[0] == 422
    with engine.connect() as conn:
        assert conn.execute(select(dm_sanction)).first() is None


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"as_of": "2999-01-01"}, "cannot be in the future"),
        ({"as_of": "31-10-2026"}, "as-of date"),
        ({"name": "Consumer.csv"}, ".xlsx"),
        ({"submission_type": "NOPE"}, "has no input workbook"),
    ],
)
def test_upload_page_input_is_checked(api, kwargs, message):
    status, data = upload_page(api, GOOD, **kwargs)
    assert status in (400, 404) and message in data["error"]
