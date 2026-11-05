"""Reference data: the database is the master (the seed only adds missing rows); each snapshot freezes the
translations it was validated with, and files are written from that copy."""

from datetime import date, datetime

import pytest
from sqlalchemy import delete, select, update

from canonical.reference import TranslationsNotFrozen, freeze_translations, seed, snapshot_translations
from canonical.schema import extract_snapshot, ref_code_translation

T = ref_code_translation.c


def _gender(conn) -> dict[str, str]:
    return dict(
        conn.execute(
            select(T.code, T.output_code).where(T.submission_type == "CONSUMER", T.code_list == "gender")
        ).all()
    )


def _snapshot(conn) -> int:
    now = datetime(2026, 10, 24)
    return conn.execute(
        extract_snapshot.insert().values(
            tenant_id="nbfc_001",
            submission_type="CONSUMER",
            as_of_date=date(2026, 9, 30),
            canonical_version="1.0",
            status="EXTRACTED",
            created_at=now,
            updated_at=now,
        )
    ).inserted_primary_key[0]


def _set_male(conn, code: str) -> None:
    conn.execute(
        update(ref_code_translation)
        .where(T.submission_type == "CONSUMER", T.code_list == "gender", T.code == "MALE")
        .values(output_code=code)
    )


def test_seed_adds_missing_rows_and_keeps_edits(engine):
    with engine.begin() as conn:  # conftest seeded; edit one row, remove another
        _set_male(conn, "M")
        conn.execute(
            delete(ref_code_translation).where(
                T.submission_type == "CONSUMER", T.code_list == "gender", T.code == "FEMALE"
            )
        )
    counts = seed(engine)
    assert counts["ref_code_translation"] == 1 and counts["ref_code"] == 0
    with engine.connect() as conn:
        gender = _gender(conn)
    assert gender["MALE"] == "M" and gender["FEMALE"] == "1"  # edit kept, missing row back


def test_freeze_copies_active_rows_and_later_edits_do_not_change_the_copy(engine):
    with engine.begin() as conn:
        sid = _snapshot(conn)
        assert freeze_translations(conn, sid, "CONSUMER") > 100
        _set_male(conn, "M")  # edited after validation
        frozen = snapshot_translations(conn, sid)
    assert frozen["gender"]["MALE"] == "2" and frozen["id_type"]["PAN"] == "01"
    assert not {"ADDITIONAL_ID_1", "ADDITIONAL_ID_2"} & set(frozen["id_type"])  # inactive: not reportable
    with engine.begin() as conn:  # re-validation takes the current rows
        freeze_translations(conn, sid, "CONSUMER")
        assert snapshot_translations(conn, sid)["gender"]["MALE"] == "M"


def test_snapshot_without_a_frozen_copy_cannot_be_written(engine):
    with engine.begin() as conn:
        sid = _snapshot(conn)
        with pytest.raises(TranslationsNotFrozen, match="validate again"):
            snapshot_translations(conn, sid)
