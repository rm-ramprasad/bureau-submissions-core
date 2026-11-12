"""Admin page part 1 (step 6): bureaus per type, reporting cycles for new requests; audited and versioned."""

from __future__ import annotations

import pytest
from app_helpers import install_golden_views
from sqlalchemy import select

from app.audit import audit_event
from app.bureaus.seed import seed_missing
from app.configstore import settings as S
from app.configstore import store
from app.enums import ReportingCycle


@pytest.fixture
def api(make_api, bureau_repo, engine):
    install_golden_views(engine)
    seed_missing(bureau_repo, "nbfc_001")
    return make_api(tenant="nbfc_001")


def actions(engine) -> list[str]:
    with engine.connect() as conn:
        return list(conn.execute(select(audit_event.c.action).order_by(audit_event.c.event_id)).scalars())


def test_the_settings_page_lists_switches_and_cycles(api):
    status, data = api.get("/admin/settings")
    assert status == 200 and [t["value"] for t in data["types"]] == ["CONSUMER", "COMMERCIAL", "MFI"]
    assert data["bureaus"] == ["CIBIL", "CRIF_HIGHMARK", "EQUIFAX", "EXPERIAN"] and len(data["switches"]) == 12
    assert all(s["is_active"] for s in data["switches"]) and all(c["enabled"] for c in data["cycles"])
    assert [c["value"] for c in data["cycles"]] == [c.value for c in ReportingCycle]


def test_switching_a_bureau_off_hides_it_for_new_files_only(api, engine, bureau_repo):
    path = "/admin/bureaus/COMMERCIAL/EQUIFAX/active"
    status, err = api.post(path, active=False)
    assert status == 400 and "reason" in err["error"]
    status, out = api.post(path, active=False, reason="not a member for commercial")
    assert status == 200 and "off" in out["message"]
    assert not bureau_repo.get("nbfc_001", "EQUIFAX", "COMMERCIAL").is_active
    assert "EQUIFAX" not in {str(p.bureau_code) for p in bureau_repo.list("nbfc_001", "COMMERCIAL")}  # not offered
    assert api.post(path, active=False, reason="again")[1]["message"] == "Nothing changed."
    assert api.post("/admin/bureaus/COMMERCIAL/NOPE/active", active=True, reason="r")[0] == 404
    assert actions(engine).count("BUREAU_SWITCHED") == 1
    assert store.versions(engine, "nbfc_001")[0].action == "BUREAU_SWITCHED"


def test_cycles_switched_off_are_not_offered_or_accepted_for_new_requests(api, engine):
    assert api.post("/admin/cycles", cycles=[], reason="r")[0] == 400
    assert api.post("/admin/cycles", cycles=["ME", "XX"], reason="r")[0] == 400
    assert api.post("/admin/cycles", cycles=["ME"])[0] == 400  # needs a reason
    status, _ = api.post("/admin/cycles", cycles=["ME", "AH"], reason="monthly only, plus ad hoc")
    assert status == 200 and S.get_all(engine, "nbfc_001") == {"reporting_cycles": ["ME", "AH"]}

    _, meta = api.get("/meta")
    assert [c["value"] for c in meta["cycles"]] == ["AH"]  # manual cycles offered
    assert {d["cycle"] for d in meta["as_of_dates"]} == {"ME"}  # only month ends
    status, err = api.post("/requests", tenant_id="nbfc_001", as_of_date="2026-09-16", submission_types=["CONSUMER"])
    assert status == 400 and "W2 cycle is switched off" in err["error"]
    status, _ = api.post("/requests", tenant_id="nbfc_001", as_of_date="2026-09-30", submission_types=["CONSUMER"])
    assert status == 201

    every = [c.value for c in ReportingCycle]
    api.post("/admin/cycles", cycles=every, reason="all again")
    assert S.get_all(engine, "nbfc_001") == {}  # back to the default: no row
    assert api.post("/admin/cycles", cycles=every, reason="r")[1]["message"] == "Nothing changed."
    assert actions(engine).count("CYCLES_CHANGED") == 2


def test_cycles_are_part_of_the_configuration_document(api, engine):
    api.post("/admin/cycles", cycles=["ME"], reason="monthly")
    doc = store.document(engine, "nbfc_001")
    assert doc["settings"] == {"reporting_cycles": ["ME"]}
    api.post("/admin/cycles", cycles=[c.value for c in ReportingCycle], reason="all")
    assert api.post("/config/import", document=doc, reason="back to monthly")[0] == 200
    assert S.enabled_cycles(engine, "nbfc_001") == [ReportingCycle.MONTH_END]
    bad = doc | {"settings": {"reporting_cycles": ["ZZ"]}}
    status, err = api.post("/config/import", document=bad, reason="r")
    assert status == 400 and "settings: Unknown reporting cycle ZZ" in err["error"]
    older = {k: v for k, v in doc.items() if k != "settings"}  # an export from before this step
    assert api.post("/config/import", document=older, reason="old file")[0] == 200
    assert S.enabled_cycles(engine, "nbfc_001") == [ReportingCycle.MONTH_END]  # kept
    with pytest.raises(ValueError, match="Unknown setting"):
        S.put(engine, "nbfc_001", "colour", "red", "me")


def test_the_data_source_is_switched_per_type_and_can_be_switched_back(api, engine):
    _, data = api.get("/admin/settings")
    assert [(d["submission_type"], d["source"]) for d in data["data_sources"]] == [
        ("CONSUMER", "views"),
        ("COMMERCIAL", "views"),
        ("MFI", "views"),
    ]
    path = "/admin/data-source/CONSUMER"
    assert api.post(path, source="master")[0] == 400  # needs a reason
    assert api.post(path, source="lake", reason="r")[0] == 400
    assert api.post("/admin/data-source/NOPE", source="master", reason="r")[0] == 404
    status, out = api.post("/admin/data-source/MFI", source="master", reason="r")
    assert status == 400 and "cannot use the customer tables" in out["error"]
    status, out = api.post(path, source="master", reason="parity identical on uploads 30 and 31")
    assert status == 200 and "customer tables" in out["message"]
    assert S.get_all(engine, "nbfc_001") == {"data_source": {"CONSUMER": "master"}}
    assert (S.data_source(engine, "nbfc_001", "CONSUMER"), S.data_source(engine, "nbfc_001", "COMMERCIAL")) == (
        "master",
        "views",
    )
    assert api.post(path, source="master", reason="again")[1]["message"] == "Nothing changed."
    doc = store.document(engine, "nbfc_001")
    assert doc["settings"] == {"data_source": {"CONSUMER": "master"}}
    api.post(path, source="views", reason="back")
    assert S.get_all(engine, "nbfc_001") == {}  # the default again: no row
    assert actions(engine).count("DATA_SOURCE_CHANGED") == 2
    assert store.versions(engine, "nbfc_001")[0].action == "DATA_SOURCE_CHANGED"
    status, out = api.post("/config/import", document=doc, reason="restore")
    assert status == 200, out
    assert S.data_source(engine, "nbfc_001", "CONSUMER") == "master"
    bad = doc | {"settings": {"data_source": {"CONSUMER": "lake"}}}
    status, err = api.post("/config/import", document=bad, reason="r")
    assert status == 400 and "Unknown data source" in err["error"]
