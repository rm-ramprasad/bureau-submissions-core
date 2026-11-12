"""ADR 0002 PR 4 (sanction model since 2026-11-11): the snapshot built from the customer tables (L2a) and the account position (L2b) is the golden
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
from app_helpers import GOLDEN
from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, func, insert, select
from test_worker_golden import GOLDEN_FILES, worker  # noqa: F401  (fixture)

from app.enums import BureauCode, ReportingCycle, RunMode
from app.validation.run import store_result
from bureau_core.contract import ValidationResult
from canonical.master.apply import apply_upload
from canonical.master.tables import dm_sanction, dm_sanction_account
from canonical.position import POSITION_COLUMNS, VIEW
from canonical.schema import ENTITY_TABLES, MODEL
from canonical.snapshot import SnapshotRepository
from canonical.tenants import TenantRepository
from sources.extract import coerce, run_extract
from sources.master import compile_master
from sources.master_load import entities_to_master, typed
from sources.master_parity import rekeyed as per_account

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
    sanctions, accounts = entities_to_master(batch["entities"], batch["submission_type"], refs_of(batch))
    apply_upload(engine, "nbfc_001", 1, sanctions, accounts)
    install_positions(engine, batch)


def per_sanction(batch: dict) -> dict:
    """The golden batch as the sanction model reports it: commercial securities, guarantors and cheque dishonours
    belong to the sanction (the borrower), so each of its accounts reports all of them (B0004: two accounts, one
    security each -> both on each)."""
    if batch["submission_type"] != "COMMERCIAL":
        return batch
    out = json.loads(json.dumps(batch))
    e = out["entities"]
    ref = {h["account_key"]: h["party_key"] for h in e["account_holder"]}
    accounts: dict[str, list[str]] = {}
    for a in e["account"]:
        accounts.setdefault(ref.get(a["account_key"], a["account_key"]), []).append(a["account_key"])
    for entity in ("security", "guarantor", "cheque_dishonour"):
        rows: dict[str, list[dict]] = {}
        for r in e.get(entity, []):
            rest = {k: v for k, v in r.items() if k not in ("account_key", "row_id")}
            group = rows.setdefault(ref.get(r["account_key"], r["account_key"]), [])
            if rest not in group:
                group.append(rest)
        e[entity] = [{"account_key": a, **r} for g, rs in rows.items() for a in accounts.get(g, []) for r in rs]
    return out


def refs_of(batch: dict) -> dict[str, str]:
    """Commercial: each account's sanction = its borrower in the golden batch (the mock keys borrowers B0001...; B0004
    holds two accounts, as a sanction with two loan accounts)."""
    if batch["submission_type"] != "COMMERCIAL":
        return {}
    number = {a["account_key"]: a["account_number"] for a in batch["entities"]["account"]}
    refs = {h["account_key"]: h["party_key"] for h in batch["entities"]["account_holder"]}
    return refs | {number[k]: v for k, v in refs.items() if k in number}


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
    got = per_account(snapshot_rows(engine, sid))  # parties compared per account they hold (keys differ)
    want = per_account(keyed_by_number(per_sanction(batch))["entities"])
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
    """Validated with today's rules, the files hold the golden records. Consumer: the same records, in account-number
    order. Commercial: the golden lines, plus the second security of borrower B0004 on each of its two accounts
    (securities belong to the sanction since 2026-11-11, owner)."""
    from app.validation.run import validate_snapshot
    from app.validation.settings import load_rule_set

    sid, batch = extract_from_master(engine, name)
    kind = batch["submission_type"]
    validate_snapshot(engine, SnapshotRepository(engine), sid, load_rule_set(engine, "nbfc_001", kind))
    b = worker.requests.create_batch(
        "nbfc_001", AS_OF, RunMode.GENERATE, [kind], list(BureauCode),
        reporting_cycle=ReportingCycle.MONTH_END, snapshot_id=sid,
    )  # fmt: skip
    worker.run_once()
    for row in worker.requests.list_batch(b):
        assert row.status.value == "SUCCEEDED", row.error_message
        content = Path(row.output_file_path).read_bytes()
        bureau = row.bureau_code.value.lower()
        if kind == "CONSUMER":
            golden = (GOLDEN_FILES / f"consumer_nbfc_001_{bureau}_via_excel.txt").read_bytes()
            assert sorted(content) == sorted(golden) and len(content) == len(golden), bureau
            continue
        golden_lines = (GOLDEN_FILES / f"commercial_nbfc_001_{bureau}.txt").read_bytes().split(b"\r\n")
        lines = content.split(b"\r\n")
        extra = list((Counter(lines) - Counter(golden_lines)).elements())
        assert not Counter(golden_lines) - Counter(lines), bureau  # every golden line is still there
        assert [x[:2] for x in extra] == [b"SS"], (bureau, extra)  # one security added (B0004's other account)


def test_history_is_off_unless_asked(engine):
    batch, _ = golden_json("consumer_nbfc_001")
    load_master(engine, batch)
    assert "account_history" not in compile_master(engine, "CONSUMER").sql
    assert "account_history" in compile_master(engine, "CONSUMER", history=True).sql
    with engine.connect() as conn:
        assert conn.execute(select(dm_sanction.c.sanction_reference_number).limit(1)).scalar() is not None


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
    if batch["entities"].get("account"):  # the account's workbook fields, no date (owner 2026-11-12): the rest is LMS
        workbook = [c.name for c in MODEL.entities["account"].columns
                    if c.name in ("account_key", "account_number", "actual_payment_amount")  # the workbook fallback
                    or c.name not in POSITION_COLUMNS]  # fmt: skip
        with engine.begin() as conn:
            conn.execute(text(f"CREATE VIEW automn.vw_{t}_account_static AS SELECT 1 AS load_id, {', '.join(workbook)} "
                              f"FROM automn.src_{t}_account"))  # fmt: skip
    if t == "commercial":  # account -> sanction (the golden borrower)
        with engine.begin() as conn:
            conn.execute(text("CREATE VIEW automn.vw_commercial_account_ref AS SELECT 1 AS load_id, account_key AS "
                              "account_number, party_key AS sanction_reference_number "
                              "FROM automn.src_commercial_account_holder"))  # fmt: skip


@pytest.mark.parametrize("name", NAMES)
def test_parity_report_on_the_golden_data_is_identical(engine, name):
    from sources.master_parity import compare

    batch = keyed_by_number(per_sanction(golden_json(name)[0]))  # rarfincare's views repeat GS/SS per account
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
    from canonical.master.apply import AccountIn, PersonIn, SanctionIn
    from sources.master_parity import compare

    batch = keyed_by_number(per_sanction(golden_json("commercial_nbfc_001")[0]))  # the views repeat SS per account
    install_keyed_views(engine, batch)
    install_positions(engine, batch)
    apply_upload(engine, "nbfc_001", 0, [SanctionIn("OLD-B", PersonIn("ENTITY", {"full_name": "Old Borrower"}))],
                 [AccountIn("OLD-1", "OLD-B")])  # fmt: skip
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


def test_records_that_cannot_reach_a_file_are_set_aside(engine):
    """rarfincare's commercial report: 21 accounts with no borrower and 4 borrowers with no account (no LMS row for
    the date) in today's snapshot. A record needs both, so neither reaches a file in either path."""
    from sources.master_parity import compare

    batch = keyed_by_number(per_sanction(golden_json("commercial_nbfc_001")[0]))  # the views repeat SS per account
    e = batch["entities"]
    e["account"].append({**e["account"][0], "account_key": "NOHOLDER-1", "account_number": "NOHOLDER-1"})
    e["party"].append({**e["party"][0], "party_key": "NOACC-B"})
    e["account_holder"].append({"account_key": "NOACC-1", "party_key": "NOACC-B", "ownership_indicator": None})
    e["party_phone"].append(
        {"party_key": "NOACC-B", "phone_number": "9000000000", "phone_type": "MOBILE", "priority": 1}
    )
    install_keyed_views(engine, batch)
    install_positions(engine, batch)
    report = compare(engine, engine, "nbfc_001", "COMMERCIAL", AS_OF, 1)
    assert report["same"], report["summary"]
    assert report["left_out"] == {"accounts without a borrower": 1, "borrowers without an account": 1}
    assert report["summary"][1] == (
        "set aside (cannot reach a file in either path): 1 accounts without a borrower, 1 borrowers without an account"
    )


def test_the_pipeline_follows_the_data_source_setting(engine):
    """Default: the source views. Switched to master on the Admin page: the upload goes into the customer tables
    first, then the snapshot SQL reads the master + position."""
    from types import SimpleNamespace

    from app.configstore import settings as S
    from app.pipeline.views import ViewsPipeline

    batch = keyed_by_number(per_sanction(golden_json("commercial_nbfc_001")[0]))  # the views repeat SS per account
    install_keyed_views(engine, batch)
    install_positions(engine, batch)
    services = SimpleNamespace(app_engine=engine, source_engine=engine)
    tenant = TenantRepository(engine).get("nbfc_001")
    request = SimpleNamespace(submission_type="COMMERCIAL", as_of_date=AS_OF)
    assert ViewsPipeline._compiled(services, tenant, request, 1).version.startswith("views-")
    S.put(engine, "nbfc_001", "data_source", {"COMMERCIAL": "master"}, "me")
    # no workbook of its own (Uploads page): the customer tables as they are; nothing applied yet, so none selected
    assert ViewsPipeline._compiled(services, tenant, request, None).version.startswith("master-")
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(dm_sanction_account)).scalar_one() == 0
    compiled = ViewsPipeline._compiled(services, tenant, request, 1)
    assert compiled.version.startswith("master-") and "account_history" not in compiled.sql
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(dm_sanction_account)).scalar_one() == len(
            batch["entities"]["account"]
        )
        assert conn.execute(select(func.count()).select_from(dm_sanction)).scalar_one() == 17  # B0004: 2 accounts
    result = run_extract(tenant=tenant, compiled=compiled, submission_type="COMMERCIAL", as_of_date=AS_OF,
                         app_engine=engine, source_engine=engine, snapshots=SnapshotRepository(engine), owner="t")  # fmt: skip
    assert result.row_counts["account"] == len(batch["entities"]["account"])
