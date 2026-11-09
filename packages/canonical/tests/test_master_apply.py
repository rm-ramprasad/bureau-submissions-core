"""Applying an upload to the L2a demographics master: the owner's rules (ADR 0002), one test each."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from canonical.db import make_engine, metadata
from canonical.master.apply import (
    LIST_FIELD,
    PartyIn,
    SanctionIn,
    SanctionPartyIn,
    SecurityIn,
    UploadConflict,
    apply_upload,
)
from canonical.master.tables import (
    MASTER_TABLES,
    dm_change_log,
    dm_party,
    dm_party_phone,
    dm_sanction,
    dm_sanction_party,
    dm_security,
)

T = "nbfc_001"
NOW = datetime(2026, 11, 8, 10)


@pytest.fixture
def eng(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    metadata.create_all(engine, tables=list(MASTER_TABLES))
    return engine


def _ravi(**values) -> PartyIn:
    base = dict(full_name="Ravi Kumar", birth_date=date(1990, 1, 2), gender="MALE", income=Decimal("50000.00"))
    return PartyIn("C1", "INDIVIDUAL", {**base, **values}, phones=[{"phone_number": "9000000001"}])


def _loan(**values) -> SanctionIn:
    return SanctionIn(
        "L1",
        values={"account_type": "PERSONAL_LOAN", "sanctioned_amount": Decimal("100000.00"), **values},
        parties=[SanctionPartyIn("C1", "borrower")],
        securities=[SecurityIn("S1", {"security_value": Decimal("5000.00")})],
    )


def _one(eng, table):
    with eng.connect() as conn:
        return conn.execute(select(table)).one()


def _count(eng, table) -> int:
    with eng.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _log(eng) -> list[tuple]:
    t = dm_change_log
    with eng.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                select(t.c.entity, t.c.entity_key, t.c.field, t.c.old_value, t.c.new_value, t.c.load_id).order_by(
                    t.c.change_id
                )
            )
        ]


def test_first_upload_adds_everything_without_change_log(eng) -> None:
    r = apply_upload(eng, T, 1, [_ravi()], [_loan()], now=NOW)
    assert (r.parties_added, r.sanctions_added, r.changes, r.warnings) == (1, 1, 0, [])
    p, s = _one(eng, dm_party), _one(eng, dm_sanction)
    assert (p.customer_id, p.full_name, p.first_load_id, p.last_load_id, p.is_active) == (
        "C1",
        "Ravi Kumar",
        1,
        1,
        True,
    )
    assert (s.account_number, s.sanction_number, s.sanctioned_amount) == ("L1", "", Decimal("100000.00"))
    link = _one(eng, dm_sanction_party)
    assert (link.party_id, link.role, link.load_id) == (p.party_id, "BORROWER", 1)
    assert _one(eng, dm_security).security_value == Decimal("5000.00")
    assert _one(eng, dm_party_phone).seq == 1


def test_same_upload_again_changes_nothing(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [_loan()], now=NOW)
    r = apply_upload(eng, T, 2, [_ravi()], [_loan()], now=NOW)
    assert (r.parties_unchanged, r.sanctions_unchanged, r.changes) == (1, 1, 0)
    assert _one(eng, dm_party).last_load_id == 1


def test_newest_wins_and_a_blank_clears_with_change_log(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [_loan()], now=NOW)
    r = apply_upload(eng, T, 2, [_ravi(full_name="Ravi K", gender="  ", income=None)], [], now=NOW)
    p = _one(eng, dm_party)
    assert (p.full_name, p.gender, p.income, p.birth_date, p.first_load_id, p.last_load_id) == (
        "Ravi K",
        None,
        None,
        date(1990, 1, 2),
        1,
        2,
    )
    assert (r.parties_updated, r.cleared) == (1, 2)
    assert _log(eng) == [
        ("dm_party", "C1", "full_name", "Ravi Kumar", "Ravi K", 2),
        ("dm_party", "C1", "gender", "MALE", None, 2),
        ("dm_party", "C1", "income", "50000.00", None, 2),
    ]


def test_a_field_missing_from_the_upload_is_a_blank(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [], now=NOW)
    apply_upload(eng, T, 2, [PartyIn("C1", "INDIVIDUAL", {"full_name": "Ravi Kumar"})], [], now=NOW)
    assert _one(eng, dm_party).birth_date is None


def test_lists_are_replaced_per_customer(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [], now=NOW)
    two = PartyIn(
        "C1",
        "INDIVIDUAL",
        _ravi().values,
        phones=[{"phone_number": "9000000002"}, {"phone_number": "9000000003", "phone_type": "HOME"}],
    )
    apply_upload(eng, T, 2, [two], [], now=NOW)
    with eng.connect() as conn:
        rows = conn.execute(
            select(dm_party_phone.c.seq, dm_party_phone.c.phone_number, dm_party_phone.c.load_id).order_by(
                dm_party_phone.c.seq
            )
        ).all()
    assert [tuple(r) for r in rows] == [(1, "9000000002", 2), (2, "9000000003", 2)]
    assert _log(eng) == [
        (
            "dm_party_phone",
            "C1",
            LIST_FIELD,
            "9000000001 |  |  |  | ",
            "9000000002 |  |  |  | ; 9000000003 |  | HOME |  | ",
            2,
        )
    ]


def test_customers_not_in_the_upload_are_untouched(eng) -> None:
    apply_upload(eng, T, 1, [_ravi(), PartyIn("C2", "ENTITY", {"full_name": "Acme"})], [], now=NOW)
    apply_upload(eng, T, 2, [_ravi(full_name="Ravi K")], [], now=NOW)
    with eng.connect() as conn:
        acme = conn.execute(select(dm_party).where(dm_party.c.customer_id == "C2")).one()
    assert (acme.full_name, acme.last_load_id) == ("Acme", 1)
    assert _count(eng, dm_party_phone) == 1


def test_sanction_parties_and_securities_are_replaced(eng) -> None:
    apply_upload(eng, T, 1, [_ravi(), PartyIn("C2", "INDIVIDUAL")], [_loan()], now=NOW)
    loan = SanctionIn(
        "L1",
        values=_loan().values,
        parties=[SanctionPartyIn("C1", "BORROWER"), SanctionPartyIn("C2", "GUARANTOR")],
    )
    apply_upload(eng, T, 2, [], [loan], now=NOW)
    with eng.connect() as conn:
        roles = sorted(conn.execute(select(dm_sanction_party.c.role)).scalars())
    assert roles == ["BORROWER", "GUARANTOR"]
    assert _count(eng, dm_security) == 0
    assert [e[:3] for e in _log(eng)] == [("dm_sanction_party", "L1", LIST_FIELD), ("dm_security", "L1", LIST_FIELD)]


def test_a_sanction_may_link_a_customer_from_an_earlier_upload(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [], now=NOW)
    r = apply_upload(eng, T, 2, [], [_loan()], now=NOW)
    assert r.sanctions_added == 1


def test_an_unknown_customer_blocks_the_whole_upload(eng) -> None:
    loan = SanctionIn("L1", parties=[SanctionPartyIn("C9", "BORROWER")])
    with pytest.raises(UploadConflict, match=r"unknown customer.*C9"):
        apply_upload(eng, T, 1, [_ravi()], [loan], now=NOW)
    assert _count(eng, dm_party) == 0  # nothing applied


def test_an_exact_repeat_counts_once(eng) -> None:
    r = apply_upload(eng, T, 1, [_ravi(), _ravi()], [_loan(), _loan()], now=NOW)
    assert (r.parties_added, r.sanctions_added) == (1, 1)


@pytest.mark.parametrize(
    "parties, sanctions, message",
    [
        ([_ravi(), _ravi(full_name="Other")], [], "Customer C1"),
        ([_ravi()], [_loan(), _loan(sanctioned_amount=Decimal(1))], "Account"),
        (
            [_ravi()],
            [
                SanctionIn(
                    "L1",
                    parties=[
                        SanctionPartyIn("C1", "GUARANTOR", {"control_percentage": 1}),
                        SanctionPartyIn("C1", "guarantor"),
                    ],
                )
            ],
            "C1 as GUARANTOR",
        ),
        ([], [SanctionIn("L1", securities=[SecurityIn("S1"), SecurityIn("S1", {"currency": "INR"})])], "security S1"),
        ([PartyIn(" ", "INDIVIDUAL")], [], "without a key"),
    ],
)
def test_conflicts_inside_one_upload_block_it(eng, parties, sanctions, message) -> None:
    with pytest.raises(UploadConflict, match=message):
        apply_upload(eng, T, 1, parties, sanctions, now=NOW)


def test_unknown_fields_are_refused(eng) -> None:
    with pytest.raises(ValueError, match=r"unknown field.*shoe_size"):
        apply_upload(eng, T, 1, [PartyIn("C1", "INDIVIDUAL", {"shoe_size": 9})], now=NOW)


def test_sanction_numbers_keep_several_sanctions_of_one_account_apart(eng) -> None:
    r = apply_upload(eng, T, 1, [], [SanctionIn("L1", "S-1"), SanctionIn("L1", "S-2"), SanctionIn("L2")], now=NOW)
    assert r.sanctions_added == 3
    apply_upload(eng, T, 2, [], [SanctionIn("L1", "S-1", {"remark": "x"})], now=NOW)
    assert _log(eng) == [("dm_sanction", "L1/S-1", "remark", None, "x", 2)]


def test_tenants_are_separate(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [], now=NOW)
    r = apply_upload(eng, "other", 2, [_ravi(full_name="X")], [], now=NOW)
    assert (r.parties_added, r.changes) == (1, 0)


def test_mass_clearing_warns_but_applies(eng) -> None:
    people = [_ravi() for _ in range(1)] + [
        PartyIn(f"C{i}", "INDIVIDUAL", {"full_name": f"P{i}", "gender": "FEMALE"}) for i in range(2, 30)
    ]
    apply_upload(eng, T, 1, people, [], now=NOW)
    blanks = [PartyIn(f"C{i}", "INDIVIDUAL") for i in range(2, 30)]
    r = apply_upload(eng, T, 2, blanks, [], now=NOW)
    assert r.cleared == 56 and len(r.warnings) == 1 and "clears 56 of 56" in r.warnings[0]
    with eng.connect() as conn:
        assert conn.execute(select(func.count()).where(dm_party.c.full_name.is_(None))).scalar_one() == 28


def test_a_few_clears_do_not_warn(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [], now=NOW)
    assert apply_upload(eng, T, 2, [_ravi(gender=None)], [], now=NOW).warnings == []


def test_clearing_a_list_counts_as_a_clear(eng) -> None:
    apply_upload(eng, T, 1, [_ravi()], [], now=NOW)
    r = apply_upload(eng, T, 2, [PartyIn("C1", "INDIVIDUAL", _ravi().values)], [], now=NOW)
    assert r.cleared == 1 and _count(eng, dm_party_phone) == 0
