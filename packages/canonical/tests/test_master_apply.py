"""Applying an upload to the L2a customer tables (sanction model, owner 2026-11-11): one test per rule."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from canonical.db import make_engine, metadata
from canonical.master.apply import LIST_FIELD, AccountIn, PersonIn, SanctionIn, UploadConflict, apply_upload
from canonical.master.tables import (
    MASTER_TABLES,
    dm_change_log,
    dm_sanction,
    dm_sanction_account,
    dm_sanction_address,
    dm_sanction_guarantor,
    dm_sanction_holder,
    dm_sanction_phone,
    dm_sanction_relationship,
    dm_sanction_security,
)

T = "nbfc_001"
NOW = datetime(2026, 11, 11, 10)


@pytest.fixture
def eng(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    metadata.create_all(engine, tables=list(MASTER_TABLES))
    return engine


def _acme(ref="SR/1", **values) -> SanctionIn:
    base = dict(full_name="Acme Traders", legal_constitution="PROPRIETORSHIP")
    return SanctionIn(
        ref,
        PersonIn("ENTITY", {**base, **values}, phones=[{"phone_number": "9000000001", "phone_type": "MOBILE"}],
                 addresses=[{"address_line_1": "1 Main Rd", "state_code": "TAMIL_NADU", "pin_code": "600001"}]),
        guarantors=[{"related_key": "GS-SR/1-2", "full_name": "Ravi", "pan": "ABCPR1234F"}],
        relationships=[{"related_key": "RS-SR/1-2", "full_name": "Meena", "relationship_type": "PROPRIETOR"}],
        securities=[{"security_value": Decimal("5000.00"), "security_type": "PROPERTY"}],
    )  # fmt: skip


def _acc(number="A1", ref="SR/1", **values) -> AccountIn:
    return AccountIn(number, ref, {"account_type": "TERM_LOAN", "sanctioned_amount": Decimal("100000.00"), **values})


def _all(eng, table) -> list:
    with eng.connect() as conn:
        return conn.execute(select(table)).all()


def _count(eng, table) -> int:
    with eng.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _log(eng) -> list[tuple]:
    t = dm_change_log
    with eng.connect() as conn:
        q = select(t.c.entity, t.c.entity_key, t.c.field, t.c.old_value, t.c.new_value).order_by(t.c.change_id)
        return [tuple(r) for r in conn.execute(q)]


def test_one_sanction_with_its_lists_and_several_accounts(eng) -> None:
    r = apply_upload(eng, T, 1, [_acme()], [_acc("A1"), _acc("A2")], now=NOW)
    assert (r.sanctions_added, r.accounts_added, r.changes, r.warnings) == (1, 2, 0, [])
    (s,) = _all(eng, dm_sanction)
    assert (s.sanction_reference_number, s.party_kind, s.full_name, s.first_load_id) == ("SR/1", "ENTITY",
                                                                                          "Acme Traders", 1)  # fmt: skip
    assert {(a.account_number, a.sanction_id, a.sanction_ref_key) for a in _all(eng, dm_sanction_account)} == {
        ("A1", s.sanction_id, "SR1"), ("A2", s.sanction_id, "SR1")}  # fmt: skip
    assert s.sanction_ref_key == "SR1"
    for t in (
        dm_sanction_phone,
        dm_sanction_address,
        dm_sanction_guarantor,
        dm_sanction_relationship,
        dm_sanction_security,
    ):
        assert {r.sanction_ref_key for r in _all(eng, t)} == {"SR1"}, t.name  # the key on every table
    assert [(p.holder_seq, p.seq, p.phone_number) for p in _all(eng, dm_sanction_phone)] == [(0, 1, "9000000001")]
    assert [a.pin_code for a in _all(eng, dm_sanction_address)] == ["600001"]
    assert [g.pan for g in _all(eng, dm_sanction_guarantor)] == ["ABCPR1234F"]
    assert [x.relationship_type for x in _all(eng, dm_sanction_relationship)] == ["PROPRIETOR"]
    assert [x.security_value for x in _all(eng, dm_sanction_security)] == [Decimal("5000.00")]


def test_same_upload_again_changes_nothing(eng) -> None:
    apply_upload(eng, T, 1, [_acme()], [_acc()], now=NOW)
    r = apply_upload(eng, T, 2, [_acme()], [_acc()], now=NOW)
    assert (r.sanctions_unchanged, r.accounts_unchanged, r.changes) == (1, 1, 0)
    assert _all(eng, dm_sanction)[0].last_load_id == 1


def test_newest_wins_and_a_blank_clears_with_change_log(eng) -> None:
    apply_upload(eng, T, 1, [_acme()], [_acc()], now=NOW)
    r = apply_upload(eng, T, 2, [_acme(full_name="Acme Traders Pvt", legal_constitution=" ")],
                     [_acc(sanctioned_amount=Decimal("90000.00"))], now=NOW)  # fmt: skip
    assert (r.sanctions_updated, r.accounts_updated, r.cleared) == (1, 1, 1)
    s = _all(eng, dm_sanction)[0]
    assert (s.full_name, s.legal_constitution, s.last_load_id) == ("Acme Traders Pvt", None, 2)
    assert ("dm_sanction", "SR1", "full_name", "Acme Traders", "Acme Traders Pvt") in _log(eng)
    assert ("dm_sanction_account", "A1", "sanctioned_amount", "100000.00", "90000.00") in _log(eng)


def test_lists_are_replaced_per_sanction(eng) -> None:
    apply_upload(eng, T, 1, [_acme()], [], now=NOW)
    moved = SanctionIn("SR/1", PersonIn("ENTITY", {"full_name": "Acme Traders", "legal_constitution": "PROPRIETORSHIP"},
                                        phones=[{"phone_number": "9000000002"}, {"phone_number": "9000000003"}]))  # fmt: skip
    apply_upload(eng, T, 2, [moved], [], now=NOW)
    assert [p.phone_number for p in _all(eng, dm_sanction_phone)] == ["9000000002", "9000000003"]
    assert _count(eng, dm_sanction_guarantor) == _count(eng, dm_sanction_address) == 0  # replaced by nothing
    assert any(e == "dm_sanction_guarantor" and f == LIST_FIELD for e, _, f, *_ in _log(eng))


def test_consumer_holders_keep_their_order_and_contacts(eng) -> None:
    joint = SanctionIn(
        "L1",
        PersonIn("INDIVIDUAL", {"full_name": "Asha", "ownership_indicator": "JOINT"},
                 phones=[{"phone_number": "9000000001"}]),
        holders=[PersonIn("INDIVIDUAL", {"full_name": "Ravi", "ownership_indicator": "JOINT"}, role="co_borrower",
                          phones=[{"phone_number": "9000000009"}]),
                 PersonIn("INDIVIDUAL", {"full_name": "Kumar", "ownership_indicator": "GUARANTOR"}, role="GUARANTOR")],
    )  # fmt: skip
    apply_upload(eng, T, 1, [joint], [AccountIn("L1", "L1")], now=NOW)
    assert [(h.holder_seq, h.role, h.full_name) for h in _all(eng, dm_sanction_holder)] == [
        (1, "CO_BORROWER", "Ravi"), (2, "GUARANTOR", "Kumar")]  # fmt: skip
    assert sorted((p.holder_seq, p.phone_number) for p in _all(eng, dm_sanction_phone)) == [(0, "9000000001"),
                                                                                           (1, "9000000009")]  # fmt: skip


def test_sanctions_and_accounts_not_in_the_upload_are_untouched(eng) -> None:
    apply_upload(eng, T, 1, [_acme(), _acme("SR/2")], [_acc("A1"), _acc("A2", "SR/2")], now=NOW)
    apply_upload(eng, T, 2, [_acme(full_name="Changed")], [_acc("A1")], now=NOW)
    names = {s.sanction_reference_number: s.full_name for s in _all(eng, dm_sanction)}
    assert names == {"SR/1": "Changed", "SR/2": "Acme Traders"}
    assert _count(eng, dm_sanction_account) == 2


def test_an_account_may_link_a_sanction_from_an_earlier_upload(eng) -> None:
    apply_upload(eng, T, 1, [_acme()], [], now=NOW)
    r = apply_upload(eng, T, 2, [], [_acc("A9")], now=NOW)
    assert r.accounts_added == 1 and r.warnings == []


def test_an_account_of_an_unknown_sanction_is_left_out_with_a_warning(eng) -> None:
    r = apply_upload(eng, T, 1, [_acme()], [_acc("A1"), _acc("A2", "SR/9")], now=NOW)
    assert r.accounts_added == 1 and "no borrower" in r.warnings[0] and "A2" in r.warnings[0]
    assert [(i["code"], i["account_number"], i["sanction_reference_number"]) for i in r.items] == [
        ("NO_BORROWER", "A2", "SR/9")]  # fmt: skip


def test_the_same_key_with_different_values_uses_the_first_and_warns(eng) -> None:
    r = apply_upload(eng, T, 1, [_acme(), _acme(full_name="Other")], [_acc(), _acc(remark="x")], now=NOW)
    assert _all(eng, dm_sanction)[0].full_name == "Acme Traders"
    assert any("Sanction(s)" in w and "SR1" in w for w in r.warnings)
    assert any("Account(s)" in w and "A1" in w for w in r.warnings)
    assert {(i["code"], i.get("sanction_reference_number") or i.get("account_number")) for i in r.items} == {
        ("LISTED_TWICE", "SR1"), ("LISTED_TWICE", "A1")}  # fmt: skip


def test_an_exact_repeat_counts_once(eng) -> None:
    r = apply_upload(eng, T, 1, [_acme(), _acme()], [_acc(), _acc()], now=NOW)
    assert (r.sanctions_added, r.accounts_added, r.warnings) == (1, 1, [])


def test_a_sanction_without_a_reference_blocks_the_upload(eng) -> None:
    with pytest.raises(UploadConflict, match="without a key"):
        apply_upload(eng, T, 1, [_acme(" ")], [], now=NOW)
    assert _count(eng, dm_sanction) == 0


def test_unknown_fields_are_refused(eng) -> None:
    with pytest.raises(ValueError, match="shoe_size"):
        apply_upload(eng, T, 1, [SanctionIn("SR/1", PersonIn("ENTITY", {"shoe_size": 9}))], now=NOW)


def test_clearing_many_values_warns(eng) -> None:
    many = [_acme(f"SR/{i}", short_name="A", hsn_code="1") for i in range(15)]
    apply_upload(eng, T, 1, many, [], now=NOW)
    r = apply_upload(eng, T, 2, [_acme(f"SR/{i}") for i in range(15)], [], now=NOW)
    assert r.cleared == 30 and "clears 30" in r.warnings[0]


def test_the_same_sanction_written_differently_is_one_sanction(eng) -> None:
    """F012/2025-26 and F012-2025-26 are one sanction: the key keeps letters and digits only."""
    apply_upload(eng, T, 1, [_acme("F012/2025-26")], [_acc("A1", "F012/2025-26")], now=NOW)
    r = apply_upload(eng, T, 2, [_acme("f012-2025 26")], [_acc("A2", "F012.2025.26")], now=NOW)
    assert (r.sanctions_added, r.accounts_added) == (0, 1) and _count(eng, dm_sanction) == 1
    (s,) = _all(eng, dm_sanction)
    assert (s.sanction_ref_key, s.sanction_reference_number) == ("F012202526", "f012-2025 26")  # newest as written
    assert {a.sanction_id for a in _all(eng, dm_sanction_account)} == {s.sanction_id}


def test_dates_survive(eng) -> None:
    s = SanctionIn("L1", PersonIn("INDIVIDUAL", {"birth_date": date(1990, 1, 2)}))
    apply_upload(eng, T, 1, [s], [], now=NOW)
    assert _all(eng, dm_sanction)[0].birth_date == date(1990, 1, 2)
