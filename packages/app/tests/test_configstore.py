"""Client configuration store (step 4): versions on every change, the version on each request, export / import."""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.bureaus.seed import seed_missing
from app.configstore import store
from app.enums import RunMode as M
from app.enums import SubmissionType as T
from app.validation import custom as C
from app.validation import settings as P
from clientconfig.document import ConfigDocumentError, changes, schema, validate

RULE = "UCRF.ID.NUMBER_FORMAT"
FORM = {
    "type": "CONSUMER", "name": "MIN_SANCTION", "field": "account.high_credit_amount", "check": "range",
    "min": "10000000", "outcome": "WARN", "message": "Sanction below 1 crore",
}  # fmt: skip


@pytest.fixture
def api(make_api, bureau_repo):
    seed_missing(bureau_repo, "nbfc_001")
    seed_missing(bureau_repo, "nbfc_002")
    return make_api(tenant="nbfc_001")


def test_every_saved_change_is_a_new_version(api, engine):
    assert store.record_baseline(engine, "nbfc_001") == 1
    assert store.record_baseline(engine, "nbfc_001") is None  # once
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=True, outcome="WARN", reason="legacy IDs")
    api.post("/rules/parameters/min_address_length", type="CONSUMER", value="5", reason="policy")
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=True, outcome="WARN", reason="same again")  # nothing changed
    api.post("/rules/custom", reason="credit policy", **FORM)
    api.post("/bureaus/CONSUMER/CIBIL", member_id="NB01234567", member_short_name="NBFC ONE",
             header_segment_tag="TUDF", file_name_pattern="{member_id}_{as_of:%d%m%Y}.txt", reason="real ID")  # fmt: skip
    _, data = api.get("/config/versions")
    got = [(v["version_no"], v["action"], v["target"], v["changes"]) for v in data["versions"]]
    assert got == [
        (5, "BUREAU_PROFILE_CHANGED", "CIBIL/CONSUMER", ["bureau_profiles/CIBIL/CONSUMER"]),
        (4, "CUSTOM_RULE_ADDED", "CUSTOM.NBFC_001.MIN_SANCTION", ["custom_rules/CONSUMER"]),
        (3, "RULE_PARAMETER_CHANGED", "CONSUMER/min_address_length", ["rule_parameters/CONSUMER"]),
        (2, "RULE_POLICY_CHANGED", RULE, [f"rule_policies/{RULE}"]),
        (1, "BASELINE", None, []),
    ]
    assert data["versions"][1]["created_by"] == "internal-user" and data["versions"][3]["reason"] == "legacy IDs"
    assert store.get(engine, "nbfc_001", 1)["rule_policies"] == {}  # the settings of the day stay readable
    assert store.get(engine, "nbfc_001", 2)["rule_policies"] == {RULE: {"is_enabled": None, "outcome": "WARN"}}
    assert store.get(engine, "nbfc_001", 99) is None
    assert store.versions(engine, "nbfc_002") == []  # per NBFC


def test_a_request_records_the_version_its_worker_started_with(api, engine, requests_repo):
    store.record_baseline(engine, "nbfc_001")
    batch = requests_repo.create_batch("nbfc_001", date(2026, 9, 30), M.VALIDATE, [T.CONSUMER])
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=False, reason="off")  # changed before the worker started
    row = requests_repo.list_batch(batch)[0]
    assert row.config_version is None
    assert requests_repo.claim(row.request_id, "w-1").config_version == 2
    other = requests_repo.create_batch("nbfc_002", date(2026, 9, 30), M.VALIDATE, [T.CONSUMER])
    assert requests_repo.claim_next("w-1").config_version is None  # nbfc_002 has no versions yet
    assert requests_repo.list_batch(other)[0].config_version is None


def test_export_and_import_round_trip(api, engine):
    api.post(f"/rules/{RULE}", type="CONSUMER", enabled=False, reason="off")
    api.post("/rules/parameters/min_address_length", type="CONSUMER", value="5", reason="policy")
    api.post("/rules/custom", reason="credit policy", **FORM)
    api.post("/rules/CUSTOM.NBFC_001.MIN_SANCTION", type="CONSUMER", enabled=True, outcome="REJECT_RECORD",
             reason="stricter")  # fmt: skip
    resp = api.client.get("/api/config/export?tenant=nbfc_001")
    assert resp.status_code == 200 and 'filename="config-nbfc_001-v4.json"' in resp.headers["Content-Disposition"]
    doc = json.loads(resp.data)
    validate(doc)

    # into another NBFC: its custom rule ids become that NBFC's
    other = api.__class__(api.client, "nbfc_002")
    status, out = other.post("/config/import", document=doc, reason="copy set-up from nbfc_001")
    assert status == 200 and out["version_no"] == 1, out
    assert C.definitions(engine, "nbfc_002", "CONSUMER").keys() == {"CUSTOM.NBFC_002.MIN_SANCTION"}
    assert P.policies(engine, "nbfc_002")["CUSTOM.NBFC_002.MIN_SANCTION"]["outcome"] == "REJECT_RECORD"
    assert P.load_parameters(engine, "nbfc_002", "CONSUMER")["min_address_length"] == 5
    assert other.post("/config/import", document=doc, reason="again")[1]["version_no"] == 1  # nothing changed

    # an older version back: what was added since is removed
    v2 = json.loads(api.client.get("/api/config/export?tenant=nbfc_001&version=2").data)
    assert v2["version_no"] == 2 and v2["custom_rules"] == {}
    status, out = api.post("/config/import", document=v2, reason="undo")
    assert status == 200 and out["version_no"] == 5
    assert C.definitions(engine, "nbfc_001", "CONSUMER") == {}
    assert set(P.policies(engine, "nbfc_001")) == {RULE}
    assert api.client.get("/api/config/export?tenant=nbfc_001&version=x").status_code == 400
    assert api.client.get("/api/config/export?tenant=nbfc_001&version=77").status_code == 404


def test_import_checks_everything_before_applying(api, engine):
    doc = store.document(engine, "nbfc_001")
    assert api.post("/config/import", document=doc)[0] == 400  # needs a reason
    status, err = api.post("/config/import", document={"schema_version": 1}, reason="r")
    assert status == 400 and "not valid" in err["error"] and "bureau_profiles" in err["error"]
    bad = json.loads(json.dumps(doc))
    bad["rule_parameters"] = {"CONSUMER": {"min_address_length": "lots", "no_such": "1"}}
    bad["rule_policies"] = {"NOPE": {"is_enabled": False, "outcome": None}, RULE: {"is_enabled": None, "outcome": "X"}}
    bad["bureau_profiles"][0]["file_name_pattern"] = "{nope}.txt"
    bad["bureau_profiles"].append(bad["bureau_profiles"][0] | {"bureau_code": "OTHER"})
    rule = {"name": "MIN_SANCTION", "entity": "account", "field": "nope", "check": "range", "min": "1",
            "outcome": "WARN", "message": "m"}  # fmt: skip
    bad["custom_rules"] = {"CONSUMER": [rule], "NESL_X": []}
    status, err = api.post("/config/import", document=bad, reason="r")
    assert status == 400
    for problem in ("min_address_length", "no_such", "NOPE: no such rule", "unknown outcome X", "OTHER/",
                    "custom rule MIN_SANCTION", "unknown submission type NESL_X"):  # fmt: skip
        assert problem in err["error"], problem
    assert store.versions(engine, "nbfc_001") == [] and P.policies(engine, "nbfc_001") == {}  # nothing applied


def test_only_administrators_import(make_api, monkeypatch):
    from app.api import auth

    monkeypatch.setattr(auth, "identify", lambda config, groups=None: auth.Identity("ravi", "R", None, is_admin=False))
    api = make_api(tenant="nbfc_001")
    assert api.post("/config/import", document={}, reason="r")[0] == 403
    assert api.get("/config/versions")[0] == 200 and api.get("/config/schema")[0] == 200  # reading is for everyone


def test_the_document_schema():
    assert schema()["properties"]["schema_version"] == {"const": 1}
    with pytest.raises(ConfigDocumentError, match="tenant_id"):
        validate({"schema_version": 1})
    many = {"schema_version": 1, "tenant_id": "t", "bureau_profiles": [{}] * 11, "rule_policies": {},
            "rule_parameters": {}, "custom_rules": {}}  # fmt: skip
    with pytest.raises(ConfigDocumentError, match="more"):
        validate(many)
    assert changes(None, {"tenant_id": "a", "x": [1]}) == ["tenant_id", "x"]
