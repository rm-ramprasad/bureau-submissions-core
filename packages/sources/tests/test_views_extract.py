"""Source views and the snapshot builder (sources.views, sources.extract). Simple SQLite stand-ins over the staging
table test what the app does with any view; the real T-SQL views run in the Database CI job (test_db_extract.py)."""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from src_helpers import workbook

from bureau_core.canonical import ColumnDef
from canonical.enums import ExtractStrategy, SnapshotStatus
from canonical.schema import ENTITY_TABLES
from canonical.snapshot import SnapshotRepository
from canonical.testing import TENANT
from sources.excel import load_workbook
from sources.extract import ExtractError, coerce, run_extract
from sources.views import ViewError, bind_params, check_views, compile_views, has_views, view_name

AS_OF = date(2026, 10, 26)
VIEWS = {
    "party": """CREATE VIEW automn.vw_consumer_party AS
        SELECT s.load_id, '2026-10-26' AS as_of_date, s.curr_new_account_no || '-' || s.source_row AS party_key,
               s.consumer_name AS full_name, UPPER(s.gender_label) AS gender
        FROM automn.stg_consumer s""",
    "account": """CREATE VIEW automn.vw_consumer_account AS
        SELECT s.load_id, '2026-10-26' AS as_of_date, s.curr_new_account_no AS account_key,
               s.curr_new_account_no AS account_number, 'HOUSING_LOAN' AS account_type, '1500.50' AS current_balance
        FROM automn.stg_consumer s""",
    "account_holder": """CREATE VIEW automn.vw_consumer_account_holder AS
        SELECT s.load_id, '2026-10-26' AS as_of_date, s.curr_new_account_no AS account_key,
               s.curr_new_account_no || '-' || s.source_row AS party_key, 'INDIVIDUAL' AS ownership_indicator
        FROM automn.stg_consumer s""",
}


def create(engine, *names, replace=None):
    with engine.begin() as conn:
        for n in names:
            conn.execute(text(f"DROP VIEW IF EXISTS automn.vw_consumer_{n}"))
            conn.execute(text((replace or {}).get(n, VIEWS[n])))


def upload(engine, rows):
    data = workbook("CONSUMER", {"Consumer": rows})
    return load_workbook(
        engine,
        "CONSUMER",
        data,
        tenant_id=TENANT.tenant_id,
        file_name="c.xlsx",
        loaded_by="t",
        batch_id="b-1",
        clock=lambda: datetime(2026, 10, 28),
    ).load_id


def extract(engine, load_id, *, strategy=ExtractStrategy.INSERT_SELECT, batch_id="b-1", compiled=None):
    return run_extract(
        tenant=replace(TENANT, extract_strategy=strategy),
        compiled=compiled or compile_views(engine, "CONSUMER"),
        submission_type="CONSUMER",
        as_of_date=AS_OF,
        app_engine=engine,
        source_engine=engine,
        snapshots=SnapshotRepository(engine),
        owner="t",
        batch_id=batch_id,
        load_id=load_id,
    )


def party_rows(engine, snapshot_id):
    party = ENTITY_TABLES["party"]
    with engine.connect() as conn:
        q = select(party.c.party_key, party.c.full_name, party.c.gender).where(party.c.snapshot_id == snapshot_id)
        return [tuple(r) for r in conn.execute(q)]


@pytest.mark.parametrize("strategy", [ExtractStrategy.INSERT_SELECT, ExtractStrategy.STREAM])
def test_a_snapshot_is_built_from_the_views(engine, strategy):
    create(engine, *VIEWS)
    load_id = upload(engine, [{"curr_new_account_no": "A1", "consumer_name": "ASHA K", "gender_label": "Female"}])
    other = upload(engine, [{"curr_new_account_no": "Z9", "consumer_name": "OTHER UPLOAD"}])
    compiled = compile_views(engine, "CONSUMER")
    assert set(compiled.sql) == {"party", "account", "account_holder"} and compiled.version.startswith("views-")
    assert "CAST(NULL AS DATE) AS birth_date" in compiled.sql["party"]  # a column the view lacks is NULL
    result = extract(engine, load_id, strategy=strategy)
    assert party_rows(engine, result.snapshot_id) == [("A1-2", "ASHA K", "FEMALE")] and other != load_id
    assert result.row_counts["party"] == 1 and result.row_counts["party_phone"] == 0
    snapshot = SnapshotRepository(engine).get(result.snapshot_id)
    assert snapshot.mapping_version == compiled.version and snapshot.status is SnapshotStatus.EXTRACTED
    account = ENTITY_TABLES["account"]
    with engine.connect() as conn:
        balance = conn.execute(select(account.c.current_balance)).scalar_one()
    assert balance == Decimal("1500.50")


def test_the_fingerprint_follows_the_view_definitions(engine):
    create(engine, *VIEWS)
    before = compile_views(engine, "CONSUMER").version
    create(engine, "party", replace={"party": VIEWS["party"].replace("UPPER(s.gender_label)", "'MALE'")})
    assert compile_views(engine, "CONSUMER").version != before


def test_missing_views_and_filters_are_explained(engine):
    assert not has_views(engine, "CONSUMER")
    create(engine, "party", "account")
    with pytest.raises(ViewError, match="vw_consumer_account_holder"):
        compile_views(engine, "CONSUMER")
    holder = VIEWS["account_holder"].replace("s.load_id, '2026-10-26' AS as_of_date,", "s.load_id,")
    create(engine, "account_holder", replace={"account_holder": holder})
    assert has_views(engine, "CONSUMER")
    sql = compile_views(engine, "CONSUMER").sql  # upload-only views need no date
    assert "as_of_date" not in sql["account_holder"] and ":as_of_date" in sql["account"]
    create(engine, "account", replace={"account": VIEWS["account"].replace("'2026-10-26' AS as_of_date,", "")})
    with pytest.raises(ViewError, match="vw_consumer_account must return as_of_date"):
        compile_views(engine, "CONSUMER")
    create(engine, "account", replace={"account": VIEWS["account"].replace("s.load_id,", "")})
    with pytest.raises(ViewError, match="vw_consumer_account must return load_id"):
        compile_views(engine, "CONSUMER")


def test_view_names_match_literally(engine):
    """'_' in the prefix is not a LIKE wildcard: a view vwXconsumerXparty is not a consumer view."""
    with engine.begin() as conn:
        conn.execute(text("CREATE VIEW automn.vwXconsumerXparty AS SELECT 1 AS load_id"))
    with engine.connect() as conn:
        from sources.views import _views

        assert _views(conn, "vw_consumer_") == {}
    assert view_name("CONSUMER", "party") == "vw_consumer_party"


def test_the_check_reports_values_a_view_did_not_convert(engine):
    create(engine, *VIEWS)
    load_id = upload(engine, [{"curr_new_account_no": "A1", "consumer_name": "ASHA K", "gender_label": "Gents"}])
    rows, errors, warnings = check_views(engine, "CONSUMER", AS_OF, load_id)
    assert rows == {"party": 1, "account": 1, "account_holder": 1} and errors == []
    assert any("vw_consumer_party.gender" in w and "GENTS" in w for w in warnings)


def test_the_check_reports_missing_and_failing_views(engine, monkeypatch):
    assert check_views(engine, "CONSUMER", AS_OF, 1)[1] == [
        "Required views missing in schema automn: vw_consumer_party, vw_consumer_account, "
        "vw_consumer_account_holder (scripts: canonical/migrations/)"
    ]
    create(engine, *VIEWS)
    compiled = compile_views(engine, "CONSUMER")
    broken = replace(compiled, sql={**compiled.sql, "party": "SELECT no_such_column FROM automn.stg_consumer"})
    monkeypatch.setattr("sources.views.compile_views", lambda *_: broken)
    rows, errors, _ = check_views(engine, "CONSUMER", AS_OF, 1)
    assert set(rows) == {"account", "account_holder"} and len(errors) == 1 and "vw_consumer_party:" in errors[0]


def test_no_loaded_workbook_stops_the_extract(engine):
    create(engine, *VIEWS)
    with pytest.raises(ExtractError, match="no successfully loaded workbook"):
        extract(engine, None)


def test_a_failed_extract_removes_its_rows_and_fails_the_snapshot(engine):
    create(engine, *VIEWS)
    create(engine, "account", replace={"account": VIEWS["account"].replace("'1500.50'", "'lots'")})
    load_id = upload(engine, [{"curr_new_account_no": "A1", "consumer_name": "ASHA K"}])
    with pytest.raises(ExtractError, match="account: cannot convert 'lots' to decimal"):
        extract(engine, load_id, strategy=ExtractStrategy.STREAM)
    snapshot = SnapshotRepository(engine).latest(TENANT.tenant_id, "CONSUMER", AS_OF)
    assert snapshot is None or snapshot.status is SnapshotStatus.FAILED
    with engine.connect() as conn:
        assert all(conn.execute(select(func.count()).select_from(t)).scalar() == 0 for t in ENTITY_TABLES.values())


def test_a_database_error_names_the_entity(engine):
    create(engine, *VIEWS)
    compiled = compile_views(engine, "CONSUMER")
    broken = replace(compiled, sql={**compiled.sql, "account": "SELECT no_such_column FROM automn.c_account"})
    load_id = upload(engine, [{"curr_new_account_no": "A1"}])
    with pytest.raises(ExtractError, match=r"^account: "):
        extract(engine, load_id, compiled=broken)


@pytest.mark.parametrize(
    ("type_", "value", "expected"),
    [
        ("string", 12, "12"),
        ("code", "MALE", "MALE"),
        ("date", "2026-10-26 00:00:00", date(2026, 10, 26)),
        ("date", datetime(2026, 10, 26, 9, 30), date(2026, 10, 26)),
        ("date", date(2026, 10, 26), date(2026, 10, 26)),
        ("decimal", 1.5, Decimal("1.5")),
        ("decimal", Decimal("2.00"), Decimal("2.00")),
        ("integer", "7", 7),
        ("integer", None, None),
    ],
)
def test_coerce(type_, value, expected):
    assert coerce(ColumnDef(name="x", type=type_), value) == expected


def test_bind_params_only_what_the_sql_uses():
    assert bind_params("WHERE v.load_id = :load_id", {"load_id": 1, "as_of_date": AS_OF}) == {"load_id": 1}
    assert bind_params("WHERE a = :load_idx", {"load_id": 1}) == {}
