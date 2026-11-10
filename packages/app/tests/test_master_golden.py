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
from canonical.master.apply import apply_upload
from canonical.master.tables import dm_party
from canonical.position import POSITION_COLUMNS, VIEW
from canonical.schema import ENTITY_TABLES, MODEL
from canonical.snapshot import SnapshotRepository
from canonical.tenants import TenantRepository
from sources.extract import coerce, run_extract
from sources.master import compile_master
from sources.master_load import entities_to_master, typed

AS_OF = date(2026, 9, 30)
KINDS = {"date": Date, "amount": Numeric(20, 2), "number": Integer, "id": Integer}


def golden_json(name: str) -> tuple[dict, dict]:
    return (
        json.loads((GOLDEN / f"{name}.batch.json").read_text("utf-8")),
        json.loads((GOLDEN / f"{name}.result.json").read_text("utf-8")),
    )


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
def load_master(engine, batch: dict) -> None:
    """Split a golden batch into demographics (the product's loader: entities_to_master -> apply_upload) and positions
    (the stand-in view)."""
    parties, sanctions = entities_to_master(batch["entities"], batch["submission_type"])
    apply_upload(engine, "nbfc_001", 1, parties, sanctions)
    install_positions(engine, batch)


def install_positions(engine, batch: dict) -> None:
    """The stand-in account position view: each account on the as-of date, its history rows as earlier positions."""
    e = batch["entities"]
    # positions: the account's figures on the as-of date, and its history rows as earlier positions
    meta = MetaData(schema="automn")
    pos = Table(VIEW, meta, *(Column(c, KINDS.get(k, String(100))) for c, k in POSITION_COLUMNS.items()))
    meta.create_all(engine)
    number = {a["account_key"]: a["account_number"] for a in e["account"]}
    rows = [
        {c: v for c, v in typed("account", a).items() if c in POSITION_COLUMNS}
        | {
            "account_number": a["account_number"],
            "sanction_number": "",
            "as_of_date": AS_OF,
            "source": "VIEW",
            "lms_type": batch["submission_type"],
        }
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
                "lms_type": batch["submission_type"],
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


# ---------------------------------------------------------------------------------------------------- parity report
def install_keyed_views(engine, batch: dict) -> None:
    """Stand-ins for the type's source views over the golden batch keyed by account number (as rarfincare's views
    key accounts), returning load_id 1 and the as-of date."""
    from sqlalchemy import text

    t = batch["submission_type"].lower()
    meta = MetaData(schema="automn")
    for entity, rows in batch["entities"].items():
        if not rows:
            continue
        columns = [c.name for c in MODEL.entities[entity].columns]
        table = Table(f"src_{t}_{entity}", meta, *(Column(c, ENTITY_TABLES[entity].c[c].type) for c in columns))
        table.create(engine)
        with engine.begin() as conn:
            conn.execute(insert(table), [typed(entity, {c: r.get(c) for c in columns}) for r in rows])
            conn.execute(text(f"CREATE VIEW automn.vw_{t}_{entity} AS SELECT 1 AS load_id, '{AS_OF}' AS as_of_date, "
                              f"{', '.join(columns)} FROM automn.src_{t}_{entity}"))  # fmt: skip


@pytest.mark.parametrize("name", NAMES)
def test_parity_report_on_the_golden_data_is_identical(engine, name):
    from sources.master_parity import compare

    batch = keyed_by_number(golden_json(name)[0])
    install_keyed_views(engine, batch)
    install_positions(engine, batch)
    history = batch["submission_type"] == "CONSUMER"
    report = compare(engine, engine, "nbfc_001", batch["submission_type"], AS_OF, 1, history=history)
    assert report["same"], report["summary"]
    assert "IDENTICAL" in report["summary"][0]
    assert (
        report["entities"]["account"]["views"]
        == report["entities"]["account"]["master"]
        == len(batch["entities"]["account"])
    )


def test_parity_report_shows_what_differs(engine):
    """An account from an earlier upload, still in the master and in the LMS for the date but not in this upload:
    the new path reports it, today's path does not. The report names it."""
    from canonical.master.apply import PartyIn, SanctionIn, SanctionPartyIn
    from sources.master_parity import compare

    batch = keyed_by_number(golden_json("commercial_nbfc_001")[0])
    install_keyed_views(engine, batch)
    install_positions(engine, batch)
    apply_upload(engine, "nbfc_001", 0, [PartyIn("OLD-B", "ENTITY", {"full_name": "Old Borrower"})],
                 [SanctionIn("OLD-1", parties=[SanctionPartyIn("OLD-B", "BORROWER")])])  # fmt: skip
    with engine.begin() as conn:
        conn.execute(insert(Table(VIEW, MetaData(schema="automn"), autoload_with=conn)).values(
            account_number="OLD-1", sanction_number="", as_of_date=AS_OF, current_balance=1, source="VIEW",
            lms_type="COMMERCIAL"))  # fmt: skip
    report = compare(engine, engine, "nbfc_001", "COMMERCIAL", AS_OF, 1)
    assert not report["same"] and "part(s) differ" in report["summary"][0]
    acc = report["entities"]["account"]
    assert (acc["views"], acc["master"], acc["only_in_views"]) == (18, 19, [])
    assert [a["account_number"] for a in acc["only_in_master"]] == ["OLD-1"]
    assert any(
        line.startswith("account: 18 rows today, 19 new; 0 only today, 1 only new") for line in report["summary"]
    )


def test_parity_report_explains_and_compares_by_value(engine):
    """Rarfincare's first real report: the same account in both LMS views (each format must read its own), the
    guarantor listed before the borrower, and 480038 vs 480038.00 (equal numbers)."""
    from decimal import Decimal

    from sqlalchemy import text

    from sources.master_parity import compare

    batch = keyed_by_number(golden_json("consumer_nbfc_001")[0])
    e = batch["entities"]
    first = e["account"][0]["account_number"]
    holders = [h for h in e["account_holder"] if h["account_key"] == first]
    others = [h for h in e["account_holder"] if h["account_key"] != first]
    e["account_holder"] = [
        {**holders[0], "ownership_indicator": "GUARANTOR", "party_key": e["party"][-1]["party_key"]},
        *holders,
        *others,
    ]  # fmt: skip  (a guarantor row before the borrower)
    install_keyed_views(engine, batch)
    install_positions(engine, batch)
    meta = MetaData(schema="automn")
    pos = Table(VIEW, meta, autoload_with=engine)
    with engine.begin() as conn:  # the commercial LMS view has the same account, with other figures
        conn.execute(insert(pos).values(account_number=first, sanction_number="", as_of_date=AS_OF,
                                        current_balance=Decimal("1.00"), source="VIEW", lms_type="COMMERCIAL"))  # fmt: skip
        # the view gives 471875.18 as text with more decimals: equal by value
        conn.execute(text("UPDATE automn.src_consumer_account SET current_balance = '471875.1800' WHERE rowid = 1"))
    report = compare(engine, engine, "nbfc_001", "CONSUMER", AS_OF, 1, history=True)
    assert report["same"], report["summary"]

    with engine.begin() as conn:  # an account the LMS has no figures for on the date: the report says so
        conn.execute(pos.delete().where(pos.c.account_number == first, pos.c.lms_type == "CONSUMER"))
    report = compare(engine, engine, "nbfc_001", "CONSUMER", AS_OF, 1, history=True)
    assert report["accounts"]["only_today"] == [
        {"account_key": first, "why": "LMS figures for the date only in the COMMERCIAL LMS view"}
    ]
    assert any(
        line.startswith("accounts only today: 1 (1 LMS figures for the date only in the COMMERCIAL")
        for line in report["summary"]
    )


def test_the_workbook_payment_amount_fills_in_when_the_lms_has_none(engine):
    """rarfincare's second report: 58 consumer accounts whose LMS has no actual payment amount; today's view takes
    the workbook's, so the new path must too (the LMS still wins when it has one)."""
    from sources.master_parity import compare

    batch = keyed_by_number(golden_json("consumer_nbfc_001")[0])
    install_keyed_views(engine, batch)
    install_positions(engine, batch)
    pos = Table(VIEW, MetaData(schema="automn"), autoload_with=engine)
    with engine.begin() as conn:  # no payment amount in the LMS for any account; the views (workbook) still have it
        conn.execute(pos.update().where(pos.c.as_of_date == AS_OF).values(actual_payment_amount=None))
    report = compare(engine, engine, "nbfc_001", "CONSUMER", AS_OF, 1, history=True)
    assert report["same"], report["summary"]
