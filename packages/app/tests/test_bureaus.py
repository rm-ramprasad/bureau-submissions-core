from dataclasses import replace
from datetime import date, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.bureaus import BureauProfileNotFoundError, InvalidFileNamePatternError
from app.bureaus.seed import placeholder_profiles, seed, seed_missing
from app.enums import BureauCode as B
from app.enums import SubmissionType as T

NBFC1, NBFC2 = "nbfc_001", "nbfc_002"


def test_seed_loads_four_bureaus_per_tenant_and_type(bureau_repo):
    assert seed(bureau_repo, [NBFC1, NBFC2]) == 16
    assert {p.bureau_code for p in bureau_repo.list(NBFC1, T.CONSUMER)} == set(B)
    commercial = bureau_repo.list(NBFC1, T.COMMERCIAL)
    assert {p.bureau_code for p in commercial} == set(B)
    cibil = bureau_repo.get(NBFC1, "CIBIL", "COMMERCIAL")
    assert (cibil.member_id, cibil.header_segment_tag) == ("CIBC000001", "")
    assert (
        cibil.file_name(datetime(2026, 10, 5, 9, 30), date(2026, 9, 30), "ME")
        == "Commercial-ME-30092026-CIBC000001-093000.txt"
    )
    assert cibil.file_name(datetime(2026, 10, 5, 9, 30), date(2026, 9, 30)).startswith("Commercial-AH-")


def test_seed_missing_adds_new_types_and_keeps_real_values(bureau_repo):
    for p in placeholder_profiles(NBFC1, 1, "ONE")[:4]:  # consumer only, as before commercial existed
        bureau_repo.upsert(p)
    real = replace(bureau_repo.get(NBFC1, "CIBIL", "CONSUMER"), member_id="REAL123456")
    bureau_repo.upsert(real)
    assert seed_missing(bureau_repo, NBFC1) == 4  # the four commercial profiles
    assert bureau_repo.get(NBFC1, "CIBIL", "CONSUMER").member_id == "REAL123456"
    assert seed_missing(bureau_repo, NBFC1) == 0


def test_seed_is_rerunnable(bureau_repo):
    seed(bureau_repo, [NBFC1])
    seed(bureau_repo, [NBFC1])
    assert len(bureau_repo.list(NBFC1)) == 8


def test_member_ids_are_per_tenant(bureau_repo):
    seed(bureau_repo, [NBFC1, NBFC2])
    one, two = bureau_repo.get(NBFC1, "CIBIL", "CONSUMER"), bureau_repo.get(NBFC2, "CIBIL", "CONSUMER")
    assert (one.member_id, two.member_id) == ("CIB0000001", "CIB0000002")
    assert one.header_segment_tag == two.header_segment_tag == "TUDF"
    assert all(len(p.member_id) == 10 for p in bureau_repo.list(NBFC2))


def test_profile_for_unknown_tenant_rejected(bureau_repo):
    with pytest.raises(IntegrityError):
        bureau_repo.upsert(placeholder_profiles("nbfc_999", 9, "X")[0])


def test_get_missing_profile(bureau_repo):
    seed(bureau_repo, [NBFC1])
    with pytest.raises(ValueError):
        bureau_repo.get(NBFC1, B.CIBIL, "NESL")  # no such type
    with pytest.raises(BureauProfileNotFoundError):
        bureau_repo.get(NBFC2, B.CIBIL, T.CONSUMER)


def test_upsert_updates_and_inactive_hidden(bureau_repo):
    seed(bureau_repo, [NBFC1, NBFC2])
    exp = bureau_repo.get(NBFC1, B.EXPERIAN, T.CONSUMER)
    bureau_repo.upsert(replace(exp, member_id="NB99999999", is_active=False))
    assert bureau_repo.get(NBFC1, B.EXPERIAN, T.CONSUMER).member_id == "NB99999999"
    assert B.EXPERIAN not in {p.bureau_code for p in bureau_repo.list(NBFC1, T.CONSUMER)}
    assert B.EXPERIAN in {p.bureau_code for p in bureau_repo.list(NBFC1, T.CONSUMER, active_only=False)}
    assert bureau_repo.get(NBFC2, B.EXPERIAN, T.CONSUMER).member_id == "EXP0000002"  # other tenant untouched


def test_file_name_rendering():
    cibil = placeholder_profiles(NBFC1, 1, "NBFC 001")[1]
    assert cibil.file_name(datetime(2026, 10, 21, 14, 5, 9), date(2026, 9, 30)) == "CIB0000001-21102026-140509.txt"
    custom = replace(cibil, file_name_pattern="{bureau}_{member_id}_{as_of:%Y%m%d}.txt")
    assert custom.file_name(datetime(2026, 10, 21), date(2026, 9, 30)) == "CIBIL_CIB0000001_20260930.txt"


def test_bad_file_name_pattern_rejected_on_upsert(bureau_repo):
    with pytest.raises(InvalidFileNamePatternError):
        bureau_repo.upsert(replace(placeholder_profiles(NBFC1, 1, "X")[0], file_name_pattern="{member}.txt"))
