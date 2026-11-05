"""Cell text, reporting cycles and bureau profiles."""

from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bureau_core.bureau import BureauProfile, InvalidFileNamePatternError
from bureau_core.enums import BureauCode, ReportingCycle, SubmissionType
from bureau_core.text import as_date, cell_text, normalise_header


def test_normalise_header() -> None:
    assert normalise_header("Borrower‟s \n Name") == normalise_header("borrower's name") == "borrower's name"
    assert normalise_header(None) == ""


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (None, None),
        (True, "1"),
        (False, "0"),
        (42, "42"),
        (42.0, "42"),
        (1.5, "1.5"),
        (datetime(2026, 9, 30), "2026-09-30"),
        (datetime(2026, 9, 30, 8, 5, 1), "2026-09-30 08:05:01"),
        (date(2026, 9, 30), "2026-09-30"),
        (time(8, 5), "08:05:00"),
        ("\xa0 x \xa0", "x"),
        ("\xa0", None),
    ],
)
def test_cell_text(value: object, text: str | None) -> None:
    assert cell_text(value) == text


@given(st.dates(min_value=date(1900, 1, 1), max_value=date(2999, 12, 31)))
def test_as_date_round_trips_both_formats(d: date) -> None:
    assert as_date(d.strftime("%d%m%Y")) == d
    assert as_date(d.isoformat()) == d


@pytest.mark.parametrize("value", [None, "31022026", "2026-02-31", "30/09/2026", ""])
def test_as_date_rejects(value: str | None) -> None:
    assert as_date(value) is None


def test_reporting_cycle_for_date() -> None:
    assert ReportingCycle.for_date(date(2026, 9, 9)) is ReportingCycle.WEEKLY_1
    assert ReportingCycle.for_date(date(2026, 9, 16)) is ReportingCycle.WEEKLY_2
    assert ReportingCycle.for_date(date(2026, 9, 23)) is ReportingCycle.WEEKLY_3
    assert ReportingCycle.for_date(date(2026, 2, 28)) is ReportingCycle.MONTH_END
    assert ReportingCycle.for_date(date(2026, 9, 10)) is None
    assert ReportingCycle.DAILY.manual and not ReportingCycle.MONTH_END.manual


def test_reporting_dates_are_newest_first_and_valid() -> None:
    dates = ReportingCycle.dates(date(2026, 9, 20), months=2)
    assert dates == sorted(dates, reverse=True)
    assert dates[0] == date(2026, 9, 16) and date(2026, 8, 31) in dates
    assert all(ReportingCycle.for_date(d) for d in dates)


def _profile(pattern: str) -> BureauProfile:
    return BureauProfile("t", BureauCode.CIBIL, SubmissionType.CONSUMER, "CIBIL", "CIB0000001", "T", "TUDF", pattern)


def test_file_name() -> None:
    p = _profile("{bureau}-{cycle}-{as_of:%d%m%Y}-{member_id}-{created:%H%M%S}.txt")
    created = datetime(2026, 10, 5, 7, 8, 9)
    assert p.file_name(created, date(2026, 9, 30), "ME") == "CIBIL-ME-30092026-CIB0000001-070809.txt"
    assert p.file_name(created, date(2026, 9, 30)) == "CIBIL-AH-30092026-CIB0000001-070809.txt"


@pytest.mark.parametrize("pattern", ["{unknown}.txt", "{0}.txt", "{as_of:%Q}{member_id:>>>}"])
def test_bad_file_name_pattern(pattern: str) -> None:
    with pytest.raises(InvalidFileNamePatternError):
        _profile(pattern).file_name(datetime(2026, 10, 5), date(2026, 9, 30))


def test_from_row() -> None:
    row = SimpleNamespace(
        _mapping={
            **_profile("x").__dict__,
            "bureau_code": "CIBIL",
            "submission_type": "CONSUMER",
            "is_active": 1,
            "updated_at": "ignored",
        }
    )
    assert BureauProfile.from_row(row) == _profile("x")
