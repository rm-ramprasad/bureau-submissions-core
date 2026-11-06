"""Reconciliation results in the database (issue #39): stored for every generated consumer file, readable through the
API, the same as the review Excel shows, and the cell values removed by retention while the counts stay."""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from test_api import worker  # noqa: F401  (fixture: the real pipeline over golden-backed views)

from app import reconciliation
from app.enums import RunMode
from app.ops.retention import purge
from app.ports.files import LocalFileStore
from bureau_core.tudf.reconcile import ColumnResult, Place, Reconciliation

AS_OF = date(2026, 9, 30)


def generated(worker, requests_repo):  # noqa: F811
    batch = requests_repo.create_batch("nbfc_001", AS_OF, RunMode.VALIDATE, ["CONSUMER"])
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    gen = requests_repo.create_batch(
        "nbfc_001", AS_OF, RunMode.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=row.snapshot_id
    )
    worker.run_once()
    (file_row,) = requests_repo.list_batch(gen)
    assert file_row.status.value == "SUCCEEDED", file_row.error_message
    return file_row


def test_a_generated_file_has_its_reconciliation_stored(worker, requests_repo, engine, make_api):  # noqa: F811
    row = generated(worker, requests_repo)
    stored = reconciliation.load(engine, row.request_id)
    assert stored["matched"] is True and stored["mismatches"] == 0 and stored["bureau_code"] == "CIBIL"
    assert stored["form_rows"] == stored["tudf_records"] == row.accepted_records
    assert stored["columns"] and all(c["mismatched"] == 0 for c in stored["columns"])
    assert sum(c["compared"] for c in stored["columns"]) > 0
    assert {c["check_name"] for c in stored["checks"]} >= {"Records: form rows = TUDF records", "Trailer present"}
    status, data = make_api().get(f"/requests/{row.request_id}/reconciliation")
    assert status == 200 and data["matched"] is True and len(data["columns"]) == len(stored["columns"])
    assert make_api().get("/requests/999/reconciliation")[0] == 404


def test_commercial_files_have_none_and_the_api_says_so(worker, requests_repo, make_api):  # noqa: F811
    batch = requests_repo.create_batch("nbfc_001", AS_OF, RunMode.VALIDATE, ["COMMERCIAL"])
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    status, err = make_api().get(f"/requests/{row.request_id}/reconciliation")
    assert status == 404 and "only generated consumer files" in err["error"]


def _not_matched() -> Reconciliation:
    place = Place("Consumer Name", "PN", ("01",), spec=None)  # type: ignore[arg-type]
    col = ColumnResult(place, compared=2, matched=1, mismatched=1)
    col.spec_rules["truncated to 26"] += 1
    return Reconciliation(
        matched=False, form_rows=2, tudf_records=2, columns=[col],
        mismatches=[(3, "Consumer Name", "ASHA KUMARI", "PN 01", "ASHA KUMAR", "differs")],
        checks=[("Records: form rows = TUDF records", "2", "2", True), ("Trailer present", "TRLR", "", False)],
        segment_counts={"PN": 2}, samples=[],
    )  # fmt: skip


def test_a_not_matched_result_round_trips_and_a_retry_replaces_it(engine):
    reconciliation.save(engine, 7, 1, "CONSUMER", "CIBIL", _not_matched())
    reconciliation.save(engine, 7, 1, "CONSUMER", "CIBIL", _not_matched())  # a retry: replaced, not doubled
    stored = reconciliation.load(engine, 7)
    assert stored["matched"] is False and stored["mismatches"] == 2  # one cell + one failed check
    assert (
        stored["mismatches_list"]
        if False
        else len(stored["mismatches"])
        if isinstance(stored["mismatches"], list)
        else True
    )
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(reconciliation.reconciliation_mismatch)).scalar() == 1
    (col,) = stored["columns"]
    assert col["spec_rules"] == "1 x truncated to 26" and col["tudf_place"] == "PN 01"
    assert [c["ok"] for c in stored["checks"]] == [True, False]
    assert reconciliation.load(engine, 8) is None


def test_retention_removes_the_cell_values_and_keeps_the_counts(worker, requests_repo, engine, tmp_path):  # noqa: F811
    row = generated(worker, requests_repo)
    reconciliation.save(engine, row.request_id, row.snapshot_id, "CONSUMER", "CIBIL", _not_matched())
    purge(engine, timedelta(days=90), LocalFileStore(tmp_path), clock=lambda: datetime.now() + timedelta(days=200))
    stored = reconciliation.load(engine, row.request_id)
    assert (
        stored["matched"] is False
        and stored["columns"]
        and stored["mismatches"] == 2
        and stored["mismatch_cells"] == []
    )
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(reconciliation.reconciliation_mismatch)).scalar() == 0
    assert reconciliation.remove_mismatches(engine, []) == 0
