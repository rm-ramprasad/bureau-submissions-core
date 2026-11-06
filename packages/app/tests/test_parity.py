"""The parity check: a request made by the app (standing in for the old app) re-run by the parity check on the same
inputs gives an identical report; a changed rule or a changed file shows up as a difference; the temporary snapshot
is removed."""

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_api import worker  # noqa: F401  (fixture: the real pipeline over golden-backed views)

from app.adjustments import AdjustmentRepository, AdjustmentScope
from app.enums import AdjustmentKind, RunMode
from app.parity import Part, compare_text_files, local_reader, run_parity
from app.parity import _old_load_id as REAL_OLD_LOAD_ID
from app.validation.settings import set_policy
from canonical.schema import extract_snapshot
from canonical.snapshot import SnapshotRepository

AS_OF = date(2026, 9, 30)


@pytest.fixture(autouse=True)
def one_upload(monkeypatch):
    """The stand-in views serve one upload (load_id 1), as the worker fixture does."""
    monkeypatch.setattr("app.parity._old_load_id", lambda *a: 1)


def old_request(worker, requests_repo, engine):  # noqa: F811
    """A validated consumer request with one exclusion, and CIBIL files generated from it."""
    batch = requests_repo.create_batch("nbfc_001", AS_OF, RunMode.VALIDATE, ["CONSUMER"], draft=True)
    scope = AdjustmentScope.of(requests_repo.list_batch(batch)[0])
    AdjustmentRepository(engine).add_exclusions(scope, AdjustmentKind.EXCLUDE_ACCOUNT, ["B122HL10788234"], "r", "u")
    requests_repo.submit_draft(batch)
    worker.run_once()
    (row,) = requests_repo.list_batch(batch)
    gen = requests_repo.create_batch(
        "nbfc_001", AS_OF, RunMode.GENERATE, ["CONSUMER"], ["CIBIL"], snapshot_id=row.snapshot_id
    )
    worker.run_once()
    (file_row,) = requests_repo.list_batch(gen)
    assert file_row.status.value == "SUCCEEDED", file_row.error_message
    return batch, Path(file_row.output_file_path).parent


def snapshot_count(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(extract_snapshot)).scalar()


def test_the_same_code_gives_an_identical_report(worker, requests_repo, engine, tmp_path):  # noqa: F811
    batch, old_files = old_request(worker, requests_repo, engine)
    before = snapshot_count(engine)
    report = run_parity(engine, batch, tmp_path / "new", local_reader(old_files))
    text = report.text()
    assert report.same, text
    assert "CONSUMER snapshot rows: account" in text and "CONSUMER CIBIL files: bureau file" in text
    assert "bureau Excel" in text and "RESULT: IDENTICAL" in text
    assert snapshot_count(engine) == before  # the temporary snapshot is gone


def test_a_changed_rule_shows_up(worker, requests_repo, engine, tmp_path):  # noqa: F811
    batch, old_files = old_request(worker, requests_repo, engine)
    set_policy(engine, "nbfc_001", "UCRF.ID.NUMBER_FORMAT", is_enabled=False)  # the "new code" validates differently
    report = run_parity(engine, batch, tmp_path / "new", local_reader(old_files), keep=True)
    assert not report.same and "only in OLD: rule_id=UCRF.ID.NUMBER_FORMAT" in report.text()
    assert "DIFFERENCES FOUND" in report.text()
    assert len(SnapshotRepository(engine).list_where(["VALIDATED"])) == 2  # kept on request


def test_missing_old_files_are_reported_not_hidden(worker, requests_repo, engine, tmp_path):  # noqa: F811
    batch, _ = old_request(worker, requests_repo, engine)
    report = run_parity(engine, batch, tmp_path / "new", local_reader(tmp_path / "empty"))
    assert not report.same and "NOT COMPARED: old file not readable" in report.text()


def test_file_comparison_rules():
    same = Part("f")
    compare_text_files(same, b"HDR1\nA\nB\n", b"HDR2\nA\nB\n")  # pipe file: the header is the first line
    assert same.same and "only the header differs" in same.note
    diff = Part("f")
    compare_text_files(diff, b"HDR\nA\nB\n", b"HDR\nA\nC\n")
    assert diff.differences == 1 and "byte 6" in diff.examples[0]
    # a TUDF has no line breaks: only a difference inside the fixed-length header is expected
    header_only = Part("t")
    compare_text_files(header_only, b"TUDF0001PNABCDEF", b"TUDF0002PNABCDEF", header_bytes=8)
    assert header_only.same
    body = Part("t")
    compare_text_files(body, b"TUDF0001PNABCDEF", b"TUDF0002PNABCXEF", header_bytes=8)
    assert not body.same and body.differences == 1 and "byte 13" in body.examples[0]
    longer = Part("t")
    compare_text_files(longer, b"TUDF0001PN", b"TUDF0001PNXX", header_bytes=8)
    assert not longer.same and "length" in longer.examples[0]


def test_an_unknown_request_is_refused(engine, tmp_path):
    with pytest.raises(ValueError, match="not a finished validation request"):
        run_parity(engine, "nope", tmp_path, local_reader(tmp_path))


def test_the_old_snapshots_own_upload_is_used_not_a_later_one(engine):
    """A workbook re-uploaded after the old validation must not be what parity rebuilds from."""
    from datetime import datetime

    from sqlalchemy import insert

    from canonical.staging.tables import input_load

    def load(at, status="LOADED"):
        with engine.begin() as conn:
            return conn.execute(
                insert(input_load).values(
                    tenant_id="nbfc_001", submission_type="CONSUMER", batch_id="b-old", file_name="c.xlsx",
                    sha256="x", status=status, loaded_by="u", loaded_at=at,
                )
            ).inserted_primary_key[0]  # fmt: skip

    first = load(datetime(2026, 11, 1, 9))
    load(datetime(2026, 11, 1, 10), status="FAILED")
    load(datetime(2026, 11, 3, 9))  # after the snapshot
    assert REAL_OLD_LOAD_ID(engine, "b-old", "CONSUMER", datetime(2026, 11, 2)) == first
    assert REAL_OLD_LOAD_ID(engine, "b-other", "CONSUMER", datetime(2026, 11, 2)) is None
