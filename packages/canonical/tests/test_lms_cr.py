"""The LMS CR section from stored procedures (owner 2026-11-16): the date rule, the column check, the fetch."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import func, select, text

from canonical import lms_cr
from canonical.lms_cr import (
    SOURCES,
    CrFetchError,
    _match,
    fetch_cr,
    lms_cr_consumer,
    lms_cr_fetch,
    previous_cycle_date,
)
from canonical.testing import engine  # noqa: F401  (fixture)


@pytest.mark.parametrize(
    ("as_of", "previous"),
    [
        (date(2026, 10, 9), date(2026, 9, 30)),  # 9th -> last day of the previous month
        (date(2026, 10, 16), date(2026, 10, 9)),
        (date(2026, 10, 23), date(2026, 10, 16)),
        (date(2026, 10, 31), date(2026, 9, 30)),  # month-end -> last day of the previous month
        (date(2026, 3, 31), date(2026, 2, 28)),
        (date(2028, 3, 9), date(2028, 2, 29)),  # leap year
        (date(2028, 2, 29), date(2028, 1, 31)),
        (date(2026, 1, 9), date(2025, 12, 31)),  # across the year
        (date(2026, 1, 31), date(2025, 12, 31)),
    ],
)
def test_previous_cycle_date(as_of, previous):
    assert previous_cycle_date(as_of) == previous


@pytest.mark.parametrize("as_of", [date(2026, 10, 13), date(2026, 10, 26), date(2026, 10, 30), date(2026, 2, 27)])
def test_other_dates_are_not_reporting_dates(as_of):
    with pytest.raises(CrFetchError, match="not a reporting date"):
        previous_cycle_date(as_of)


def test_a_missing_column_stops_the_fetch_by_name():
    row = {c: None for c in SOURCES["CONSUMER"].columns if c not in ("TL_AmtOverdue", "SelectionReason")}
    with pytest.raises(CrFetchError, match="TL_AmtOverdue"):
        _match([row], SOURCES["CONSUMER"])


def test_columns_match_ignoring_case_and_selection_reason_is_optional():
    row = {c.upper(): i for i, c in enumerate(SOURCES["COMMERCIAL"].columns) if c != "SelectionReason"}
    (out,) = _match([{**row, "Extra": 1}], SOURCES["COMMERCIAL"])
    assert out["Major reasons for Restructuring"] == row["MAJOR REASONS FOR RESTRUCTURING"]
    assert out["SelectionReason"] is None and "Extra" not in out


def _wip(engine, *accounts, as_of=date(2026, 10, 31)):  # noqa: F811
    with engine.begin() as conn:
        for a in accounts:
            conn.execute(
                text("INSERT INTO wip.VwConsumerSubmissionAccountSegment VALUES (:a, :d)"), {"a": a, "d": as_of}
            )


def _saved(engine):  # noqa: F811
    with engine.connect() as conn:
        return sorted(conn.execute(select(lms_cr_consumer.c.AccountNumber)).scalars())


def test_fetch_saves_the_rows_and_a_new_fetch_replaces_them(engine):  # noqa: F811
    _wip(engine, "A1", "A2")
    result = fetch_cr(engine, engine, "CONSUMER", date(2026, 10, 31), by="maker")
    assert (result.rows, result.previous_date) == (2, date(2026, 9, 30))
    assert _saved(engine) == ["A1", "A2"]
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM wip.VwConsumerSubmissionAccountSegment WHERE AccountNumber = 'A2'"))
    fetch_cr(engine, engine, "CONSUMER", date(2026, 10, 31))
    assert _saved(engine) == ["A1"]
    with engine.connect() as conn:
        log = conn.execute(select(lms_cr_fetch).order_by(lms_cr_fetch.c.fetch_id)).mappings().all()
    assert [(r["outcome"], r["row_count"], r["fetched_by"]) for r in log] == [("SAVED", 2, "maker"), ("SAVED", 1, None)]


def test_other_dates_are_kept(engine):  # noqa: F811
    _wip(engine, "OLD", as_of=date(2026, 9, 30))
    fetch_cr(engine, engine, "CONSUMER", date(2026, 9, 30))
    _wip(engine, "NEW")
    fetch_cr(engine, engine, "CONSUMER", date(2026, 10, 31))
    assert _saved(engine) == ["NEW", "OLD"]


def test_a_failed_procedure_is_logged_and_reported_in_plain_words(engine, monkeypatch):  # noqa: F811
    def broken(*_):
        raise RuntimeError("The EXECUTE permission was denied")

    monkeypatch.setattr(lms_cr, "run_procedure", broken)
    with pytest.raises(CrFetchError, match="usp_GetConsumerSubmissionCR failed for 31-10-2026: The EXECUTE"):
        fetch_cr(engine, engine, "CONSUMER", date(2026, 10, 31), clock=lambda: datetime(2026, 11, 16))
    with engine.connect() as conn:
        assert conn.execute(select(lms_cr_fetch.c.outcome)).scalar_one() == "FAILED"


def test_a_non_reporting_date_is_logged_nowhere_and_refused(engine):  # noqa: F811
    with pytest.raises(CrFetchError):
        fetch_cr(engine, engine, "CONSUMER", date(2026, 10, 30))
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(lms_cr_fetch)).scalar_one() == 0


def test_types_without_a_procedure_fetch_nothing(engine):  # noqa: F811
    assert fetch_cr(engine, engine, "MFI", date(2026, 10, 31)) is None


def test_the_procedure_call_per_database():
    from types import SimpleNamespace

    def on(name):
        return SimpleNamespace(dialect=SimpleNamespace(name=name))

    with pytest.raises(ValueError, match="SQL Server only"):
        lms_cr._call_sql(on("postgresql"), "dbo.p")  # type: ignore[arg-type]
    assert lms_cr._call_sql(on("mssql"), "dbo.p") == (  # type: ignore[arg-type]
        "SET NOCOUNT ON; EXEC dbo.p @TransactionDate = :t, @PreviousTransactionDate = :p, @CloseDate = :c"
    )


def test_the_ci_stand_ins_have_the_procedure_columns():
    from canonical.testing_db import STUBS

    for kind, source in SOURCES.items():
        assert list(STUBS[lms_cr.STAND_INS[kind]]) == [c for c in source.columns if c != "SelectionReason"]


def test_compare_with_view_reports_each_difference(engine, monkeypatch):  # noqa: F811
    _wip(engine, "A1", "A2")
    proc = [
        {"AccountNumber": "A1", "TransactionDate": date(2026, 10, 31), "TL_CurrentBalance": 5},
        {"AccountNumber": "A3", "TransactionDate": date(2026, 10, 31)},
    ]
    monkeypatch.setattr(lms_cr, "run_procedure", lambda *_: proc)
    monkeypatch.setattr(
        lms_cr, "_match", lambda rows, source, strict=True: [{c: r.get(c) for c in source.columns} for r in rows]
    )
    out = lms_cr.compare_with_view(engine, "CONSUMER", date(2026, 10, 31))
    assert out["verdict"] == "DIFFERENT"
    assert (out["only_in_view"], out["only_in_procedure"]) == (["A2"], ["A3"])
    assert out["differing_columns"] == {"TL_CurrentBalance": 1}
    assert out["differing_examples"][0]["view_vs_procedure"] == {"TL_CurrentBalance": ["None", "5"]}


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        ("1234.5", 1234.50, True),
        (date(2026, 10, 31), "2026-10-31", True),
        (datetime(2026, 10, 31, 0, 0), date(2026, 10, 31), True),
        (" X ", "X", True),
        ("ABC", "ABD", False),
        (None, None, True),
        (None, "", False),
        (date(2026, 10, 31), "31102026", False),
    ],
)
def test_values_compare_as_reported(a, b, same):
    assert lms_cr._same(a, b) is same
