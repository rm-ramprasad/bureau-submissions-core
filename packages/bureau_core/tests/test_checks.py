"""ID-number and phone rules (spec §7.1, §8.1; commercial Annexure D part C)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from bureau_core.checks.identifiers import id_number_problem, normalise, verhoeff_valid
from bureau_core.checks.phones import number_problem, valid_numbers


def _with_check_digit(body: str) -> str:
    return next(body + d for d in "0123456789" if verhoeff_valid(body + d))


@given(st.text(alphabet="0123456789", min_size=11, max_size=11))
def test_verhoeff_finds_exactly_one_check_digit(body: str) -> None:
    assert sum(verhoeff_valid(body + d) for d in "0123456789") == 1


def test_aadhaar() -> None:
    good = _with_check_digit("23412341234")
    assert id_number_problem("AADHAAR", good) is None
    assert id_number_problem("AADHAAR", f"{good[:4]} {good[4:8]} {good[8:]}") is None  # spaces ignored
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    assert id_number_problem("AADHAAR", bad) == "Aadhaar check digit (Verhoeff) is wrong"
    assert id_number_problem("AADHAAR", "1234") == "Aadhaar must be 12 digits"


@pytest.mark.parametrize(
    ("id_type", "good", "bad"),
    [
        ("PAN", "ABCPE1234F", "ABCDE1234F"),
        ("PASSPORT", "A1234567", "1234567"),
        ("VOTER_ID", "ABC1234567", "AB12"),
        ("CKYC", "12345678901234", "1234567890123456"),
        ("CIN", "U12345MH2020PTC123456", "U123"),
        ("TIN", "27123456789", "2712"),
        ("UDYAM", "udyam-mh-01-1234567", "UDYAM-MH-1-1234567"),
        ("DIN", "01234567", "0123456"),
        ("G_RAM_G", "MH-01-001-002-003/004Patil", "MH01001002003004Patil"),
    ],
)
def test_patterns(id_type: str, good: str, bad: str) -> None:
    assert id_number_problem(id_type, good) is None
    assert id_number_problem(id_type, bad)


def test_overridden_pattern_is_named_in_the_message() -> None:
    assert id_number_problem("DIN", "1234", {"id_pattern_din": r"^[0-9]{4}$"}) is None
    assert id_number_problem("DIN", "12", {"id_pattern_din": r"^[0-9]{4}$"}) == "does not match the pattern ^[0-9]{4}$"


def test_unvalidated_and_empty() -> None:
    assert id_number_problem("DRIVING_LICENCE", "anything") is None
    assert id_number_problem("PAN", "  ") == "empty ID number"
    assert id_number_problem("PAN", None) == "empty ID number"
    assert normalise("ab-12/ 3") == "AB123"


@pytest.mark.parametrize(
    ("raw", "phone_type", "problem"),
    [
        ("1234", None, "fewer than 5 digits"),
        ("99999999", None, "single repeated digit"),
        ("1234567", None, "ascending or descending sequence"),
        ("98765", None, "ascending or descending sequence"),
        ("+44 2079", None, "ISD number shorter than 9 digits"),
        ("+44 20 7946 0958", None, None),
        ("1800123456", None, "starts with 1"),
        ("4123456789", "MOBILE", "Indian mobile must be 10 digits starting 5-9"),
        ("+91 9812345670", "MOBILE", None),
        ("009198123 45670", "MOBILE", None),
        ("09812345670", "MOBILE", None),
        ("0222345678", "LANDLINE", None),
    ],
)
def test_number_problem(raw: str, phone_type: str | None, problem: str | None) -> None:
    assert number_problem(raw, phone_type) == problem


def test_valid_numbers_splits_only_unclassified() -> None:
    assert valid_numbers("9812345670 / 9812345671", None) == ["9812345670", "9812345671"]
    assert valid_numbers("9812345670, 1234", "NOT_CLASSIFIED") == ["9812345670"]
    assert valid_numbers("   ", None) == []
    assert valid_numbers(None, None) == []


@pytest.mark.xfail(strict=True, reason="known gap kept from the reference: a mobile needs >= 10 digits, not exactly 10")
def test_mobile_with_two_numbers_is_rejected() -> None:
    assert valid_numbers("9812345670/9812345671", "MOBILE") == []
