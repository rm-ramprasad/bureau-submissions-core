"""ADR 0002 PR 4: the snapshot built from the demographics master (L2a) and the account position (L2b) is the golden
snapshot, and its files are the golden files.

The golden batches (captures of the reference app's mock sources) are split the way a client's data would arrive:
demographics -> apply_upload -> dm_*, loan-system figures -> a stand-in for automn.vw_pos_account_position (account
history = earlier positions). compile_master + run_extract then build the snapshot.

Keys: the new path keys accounts by account number, as the source views do on rarfincare; the golden batches use the
mock's own keys (L00001). So the golden rows are compared with their account_key replaced by the account number.
Files: commercial files are byte for byte the golden files. Consumer records are sorted by account key, so with
account-number keys the same records come in another order: the consumer files are compared byte for byte with what
today's code writes from the golden batch keyed by account number.
"""

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from app_helpers import GOLDEN, load_snapshot
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, insert, select
from test_worker_golden import GOLDEN_FILES, worker  # noqa: F401  (fixture)

from app.enums import BureauCode, ReportingCycle, RunMode
from app.validation.run import store_result
from bureau_core.contract import CanonicalBatch, ValidationResult
from canonical.master.apply import PartyIn, SanctionIn, SanctionPartyIn, SecurityIn, apply_upload
from canonical.master.tables import dm_party, dm_sanction
from canonical.position import POSITION_COLUMNS, VIEW
from canonical.schema import ENTITY_TABLES, MODEL
from canonical.snapshot import SnapshotRepository
from canonical.tenants import TenantRepository
from sources.extract import coerce, run_extract
from sources.master import ID_COLUMNS, compile_master

AS_OF = date(2026, 9, 30)
KINDS = {"date": Date, "amount": Numeric(20, 2), "number": Integer, "id": Integer}


def golden_json(name: str) -> tuple[dict, dict]:
    return (
        json.loads((GOLDEN / f"{name}.batch.json").read_text("utf-8")),
        json.loads((GOLDEN / f"{name}.result.json").read_text("utf-8")),
    )


def typed(entity: str, row: dict) -> dict:
    cols = {c.name: c for c in MODEL.entities[entity].columns}
    return {k: coerce(cols[k], v) if k in cols else v for k, v in row.items()}


def keyed_by_number(batch: dict) -> dict:
    """The golden batch with account_key = the account number (as the new path keys accounts)."""
    number = {a["account_key"]: a["account_number"] for a in batch["entities"]["account"]}
    out = json.loads(json.dumps(batch))
    for rows in out["entities"].values():
        for r in rows:
            if r.get("account_key") in number:
                r["account_key"] = number[r["account_key"]]
    return out


# ---------------------------------------------------------------------------------------------------- golden -> L2a/L2b
def _related_party(row: dict) -> PartyIn:
    kind = "INDIVIDUAL" if "INDIVIDUAL" in (row["related_type"] or "") else "ENTITY"
    values = {k: row[k] for k in ("full_name", "name_prefix", "business_category", "industry_type", "gender",
                                  "birth_date", "incorporation_date")}  # fmt: skip
    values = typed("relationship", values) | {"party_type": row["related_type"]}
    address = {k: row[k] for k in ("address_line_1", "address_line_2", "address_line_3", "city", "district",
                                   "state_code", "pin_code", "country")}  # fmt: skip
    phones = [
        {"phone_number": row["mobile"], "phone_type": "MOBILE"} if row["mobile"] else None,
        {"phone_number": row["telephone"], "area_code": row["telephone_area_code"], "phone_type": "OFFICE"}
        if row["telephone"]
        else None,
        {"phone_number": row["fax"], "area_code": row["fax_area_code"], "phone_type": "FAX"} if row["fax"] else None,
    ]
    return PartyIn(
        row["related_key"],
        kind,
        values,
        identifiers=[{"id_type": t, "id_number": row[c]} for c, t in ID_COLUMNS.items() if row[c]],
        phones=[p for p in phones if p],
        addresses=[address] if any(address.values()) else [],
    )


def load_master(engine, batch: dict) -> None:
    """Split a golden batch into demographics (apply_upload) and positions (the stand-in view)."""
    e, consumer = batch["entities"], batch["submission_type"] == "CONSUMER"

    def lists(entity: str, key: str) -> list[dict]:
        return [
            {k: v for k, v in typed(entity, r).items() if k not in ("row_id", "party_key")}
            for r in e.get(entity, [])
            if r["party_key"] == key
        ]

    parties = [
        PartyIn(
            p["party_key"],
            "INDIVIDUAL" if consumer else "ENTITY",
            {k: v for k, v in typed("party", p).items() if k not in ("row_id", "party_key")},
            identifiers=lists("party_identifier", p["party_key"]),
            phones=lists("party_phone", p["party_key"]),
            emails=lists("party_email", p["party_key"]),
            addresses=lists("party_address", p["party_key"]),
        )
        for p in e["party"]
    ]
    related = {r["related_key"]: _related_party(r) for r in e.get("relationship", []) + e.get("guarantor", [])}

    sanction_fields = set(dm_sanction.c.keys()) - set(POSITION_COLUMNS) - {"sanction_id", "tenant_id"}
    borrowers: dict[str, list[str]] = {}  # party -> its accounts (where it is the borrower)
    sanctions = []
    for a in e["account"]:
        key = a["account_key"]
        holders = [h for h in e["account_holder"] if h["account_key"] == key]
        links = []
        for i, h in enumerate(holders):
            own = h["ownership_indicator"]
            role = "GUARANTOR" if own == "GUARANTOR" else ("BORROWER" if i == 0 else "CO_BORROWER")
            links.append(SanctionPartyIn(h["party_key"], role, {"ownership_indicator": own}))
            if role == "BORROWER":
                borrowers.setdefault(h["party_key"], []).append(key)
        for g in (g for g in e.get("guarantor", []) if g["account_key"] == key):
            links.append(
                SanctionPartyIn(
                    g["related_key"],
                    "GUARANTOR",
                    typed("guarantor", {"guarantee_invocation_date": g["guarantee_invocation_date"]}),
                )
            )
        sanctions.append(
            {
                "key": key,
                "number": a["account_number"],
                "values": {k: v for k, v in typed("account", a).items() if k in sanction_fields},
                "links": links,
                "securities": [
                    SecurityIn(
                        f"S{i:04d}",
                        {k: v for k, v in typed("security", s).items() if k not in ("row_id", "account_key")},
                    )
                    for i, s in enumerate(x for x in e.get("security", []) if x["account_key"] == key)
                ],
                "cheques": [
                    {k: v for k, v in typed("cheque_dishonour", c).items() if k not in ("row_id", "account_key")}
                    for c in e.get("cheque_dishonour", [])
                    if c["account_key"] == key
                ],
            }
        )
    for r in e.get("relationship", []):  # a related party is linked on each of the borrower's sanctions
        for s in sanctions:
            if s["key"] in borrowers.get(r["party_key"], []):
                v = typed("relationship", {k: r[k] for k in ("relationship_type", "control_percentage")})
                s["links"].append(SanctionPartyIn(r["related_key"], "RELATED", v))
    apply_upload(
        engine,
        "nbfc_001",
        1,
        parties + list(related.values()),
        [SanctionIn(s["number"], "", s["values"], s["links"], s["securities"], s["cheques"]) for s in sanctions],
    )

    # positions: the account's figures on the as-of date, and its history rows as earlier positions
    meta = MetaData(schema="automn")
    pos = Table(VIEW, meta, *(Column(c, KINDS.get(k, String(100))) for c, k in POSITION_COLUMNS.items()))
    meta.create_all(engine)
    number = {a["account_key"]: a["account_number"] for a in e["account"]}
    rows = [
        {c: v for c, v in typed("account", a).items() if c in POSITION_COLUMNS}
        | {"account_number": a["account_number"], "sanction_number": "", "as_of_date": AS_OF, "source": "VIEW"}
        for a in e["account"]
    ]
    for h in e.get("account_history", []):
        t = typed("account_history", h)
        rows.append(
            {c: v for c, v in t.items() if c in POSITION_COLUMNS and c != "account_key"}
            | {
                "account_number": number[h["account_key"]],
                "sanction_number": "",
                "as_of_date": t["history_month"],
                "source": "VIEW",
            }
        )
    with engine.begin() as conn:
        conn.execute(insert(pos), [{c: r.get(c) for c in POSITION_COLUMNS} for r in rows])


def extract_from_master(engine, name: str) -> tuple[int, dict]:
    batch, _ = golden_json(name)
    load_master(engine, batch)
    compiled = compile_master(engine, batch["submission_type"], history=batch["submission_type"] == "CONSUMER")
    result = run_extract(
        tenant=TenantRepository(engine).get("nbfc_001"),
        compiled=compiled,
        submission_type=batch["submission_type"],
        as_of_date=AS_OF,
        app_engine=engine,
        source_engine=engine,
        snapshots=SnapshotRepository(engine),
        owner="test",
    )
    return result.snapshot_id, batch


def snapshot_rows(engine, sid: int) -> dict[str, list[dict]]:
    out = {}
    with engine.connect() as conn:
        for entity, table in ENTITY_TABLES.items():
            rows = conn.execute(select(table).where(table.c.snapshot_id == sid).order_by(table.c.row_id)).mappings()
            out[entity] = [dict(r) for r in rows]
    return out


def _comparable(entity: str, row: dict) -> tuple:
    cols = [c.name for c in MODEL.entities[entity].columns]
    return tuple(str(coerce(next(c for c in MODEL.entities[entity].columns if c.name == n), row.get(n))) for n in cols)


NAMES = ["consumer_nbfc_001", "commercial_nbfc_001"]


@pytest.mark.parametrize("name", NAMES)
def test_the_master_snapshot_is_the_golden_snapshot(engine, name):
    sid, batch = extract_from_master(engine, name)
    got = snapshot_rows(engine, sid)
    want = keyed_by_number(batch)["entities"]
    for entity in MODEL.entities:
        expected = Counter(_comparable(entity, r) for r in want.get(entity, []))
        actual = Counter(_comparable(entity, r) for r in got[entity])
        assert actual == expected, (
            f"{entity}: {sorted((actual - expected).elements())[:2]} vs {sorted((expected - actual).elements())[:2]}"
        )


def _remapped_result(result: dict, golden_rows: dict, snapshot: dict) -> ValidationResult:
    """The recorded result with each finding's row_id moved to the matching row of the new snapshot."""
    moved = json.loads(json.dumps(result))
    for f in moved["findings"]:
        entity = f["entity"]
        if f.get("row_id") is None or entity not in golden_rows:
            continue
        g = next(r for r in golden_rows[entity] if r["row_id"] == f["row_id"])
        f["row_id"] = next(r["row_id"] for r in snapshot[entity] if _comparable(entity, r) == _comparable(entity, g))
    return ValidationResult.from_json(json.dumps(moved))


def _keyed_result(result: dict, number: dict) -> dict:
    out = json.loads(json.dumps(result))
    for o in out["outcomes"]:
        o[0] = number.get(o[0], o[0])
    for f in out["findings"]:
        if f.get("account_key") in number:
            f["account_key"] = number[f["account_key"]]
    for k in ("drops", "translations"):
        for x in out.get(k, []):
            if isinstance(x, dict) and x.get("account_key") in number:
                x["account_key"] = number[x["account_key"]]
            elif isinstance(x, list) and x and x[0] in number:
                x[0] = number[x[0]]
    return out


def _generate(w, engine, sid: int, submission_type: str, result: ValidationResult) -> dict[str, bytes]:
    with engine.begin() as conn:
        store_result(conn, sid, submission_type, result)
    SnapshotRepository(engine).mark_validated(sid)
    b = w.requests.create_batch(
        "nbfc_001", AS_OF, RunMode.GENERATE, [submission_type], list(BureauCode),
        reporting_cycle=ReportingCycle.MONTH_END, snapshot_id=sid,
    )  # fmt: skip
    w.run_once()
    files = {}
    for row in w.requests.list_batch(b):
        assert row.status.value == "SUCCEEDED", row.error_message
        files[row.bureau_code.value.lower()] = Path(row.output_file_path).read_bytes()
    return files


@pytest.mark.parametrize("name", NAMES)
def test_the_master_snapshot_writes_the_golden_files(engine, worker, name):  # noqa: F811  (fixture)
    sid, batch = extract_from_master(engine, name)
    _, result = golden_json(name)
    number = {a["account_key"]: a["account_number"] for a in batch["entities"]["account"]}
    keyed = keyed_by_number(batch)
    keyed_result = _keyed_result(result, number)
    files = _generate(worker, engine, sid, batch["submission_type"],
                      _remapped_result(keyed_result, keyed["entities"], snapshot_rows(engine, sid)))  # fmt: skip
    if batch["submission_type"] == "COMMERCIAL":
        for bureau, content in files.items():
            assert content == (GOLDEN_FILES / f"commercial_nbfc_001_{bureau}.txt").read_bytes(), bureau
        return
    # consumer: today's code on the golden batch keyed by account number
    ref = load_snapshot(engine, CanonicalBatch.from_json(json.dumps(keyed)), batch_id="today", keep_row_ids=False)
    ref_rows = snapshot_rows(engine, ref)
    expected = _generate(worker, engine, ref, "CONSUMER", _remapped_result(keyed_result, keyed["entities"], ref_rows))
    for bureau, content in files.items():
        assert content == expected[bureau], bureau
        golden_file = (GOLDEN_FILES / f"consumer_nbfc_001_{bureau}_via_excel.txt").read_bytes()
        assert sorted(content) == sorted(golden_file) and len(content) == len(golden_file)  # same records, reordered


def test_history_is_off_unless_asked(engine):
    batch, _ = golden_json("consumer_nbfc_001")
    load_master(engine, batch)
    assert "account_history" not in compile_master(engine, "CONSUMER").sql
    assert "account_history" in compile_master(engine, "CONSUMER", history=True).sql
    with engine.connect() as conn:
        assert conn.execute(select(dm_party.c.customer_id).limit(1)).scalar() is not None
