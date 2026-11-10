"""MFI step 2 (ADR 0003): relatives and borrower groups go into the customer tables (apply_upload) and come out in
an MFI snapshot (compile_master); MFI columns on borrowers and accounts flow through by name."""

from datetime import date
from decimal import Decimal

from sqlalchemy import Column, Date, Integer, MetaData, Numeric, String, Table, insert, select

from canonical.master.apply import GroupIn, PartyIn, SanctionIn, SanctionPartyIn, apply_upload
from canonical.master.tables import dm_group, dm_party_relative
from canonical.position import POSITION_COLUMNS, VIEW
from canonical.testing import TENANT
from sources.master import compile_master
from sources.master_load import entities_to_master, groups_of

AS_OF = date(2027, 7, 31)
KINDS = {"date": Date, "amount": Numeric(20, 2), "number": Integer, "id": Integer}


def _positions(engine, *accounts: str, lms_type: str = "MFI") -> None:
    meta = MetaData(schema="automn")
    pos = Table(VIEW, meta, *(Column(c, KINDS.get(k, String(100))) for c, k in POSITION_COLUMNS.items()))
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(pos), [
            {c: None for c in POSITION_COLUMNS} | {"account_number": a, "sanction_number": "", "as_of_date": AS_OF,
             "current_balance": Decimal("1000.00"), "days_past_due": 0, "source": "VIEW", "lms_type": lms_type}
            for a in accounts
        ])  # fmt: skip


def _upload(engine, relatives=None, group_values=None) -> None:
    member = PartyIn(
        "M1", "INDIVIDUAL",
        {"full_name": "Lakshmi Devi", "birth_date": date(1990, 1, 2), "gender": "FEMALE", "centre_id": "KEN-7",
         "group_key": "G1", "family_income": Decimal("12000"), "member_role": "MEMBER", "member_status": "ACTIVE"},
        relatives=relatives if relatives is not None else [
            {"relative_role": "KEY_PERSON", "priority": 1, "full_name": "Ravi Kumar", "relation": "HUSBAND"},
            {"relative_role": "NOMINEE", "priority": 1, "full_name": "Anu", "relation": "DAUGHTER"},
        ],
    )  # fmt: skip
    loan = SanctionIn(
        "MFI-L1",
        values={"loan_category": "SHG_INDIVIDUAL", "loan_cycle": 2, "loan_type": "TERM_LOAN", "group_key": "G1",
                "centre_id": "KEN-7", "insurance_indicator": "YES"},
        parties=[SanctionPartyIn("M1", "BORROWER")],
    )  # fmt: skip
    group = GroupIn("G1", group_values or {"full_name": "Jyothi SHG", "group_account_number": "GL-1",
                                           "govt_program": "NRLM", "state_code": "KARNATAKA", "pin_code": "560062"})  # fmt: skip
    apply_upload(engine, TENANT.tenant_id, 1, [member], [loan], [group])


def _rows(engine, compiled, entity: str) -> list:
    """The MFI SELECT of one entity, run as run_extract would (MFI cannot make snapshots until it is switched on)."""
    from sqlalchemy import text

    from sources.views import bind_params

    sql = compiled.sql[entity]
    order = compiled.order.get(entity)
    if order:
        sql = f"SELECT * FROM (\n{sql}\n) q ORDER BY {order}"
    with engine.connect() as conn:
        return conn.execute(text(sql), bind_params(sql, {"as_of_date": AS_OF, "tenant_id": TENANT.tenant_id})).all()


def test_an_mfi_snapshot_has_members_relatives_groups_and_loan_details(engine):
    _upload(engine)
    _positions(engine, "MFI-L1")
    compiled = compile_master(engine, "MFI")
    (party,) = _rows(engine, compiled, "party")
    assert (party.party_key, party.centre_id, party.group_key, party.family_income, party.member_status) == (
        "M1",
        "KEN-7",
        "G1",
        Decimal("12000.00"),
        "ACTIVE",
    )
    assert [(r.relative_role, r.full_name, r.relation) for r in _rows(engine, compiled, "party_relative")] == [
        ("KEY_PERSON", "Ravi Kumar", "HUSBAND"), ("NOMINEE", "Anu", "DAUGHTER"),
    ]  # fmt: skip
    (account,) = _rows(engine, compiled, "account")
    assert (account.loan_category, account.loan_cycle, account.loan_type, account.insurance_indicator) == (
        "SHG_INDIVIDUAL",
        2,
        "TERM_LOAN",
        "YES",
    )
    assert account.current_balance == Decimal("1000.00")  # from the account position (L2b)
    (group,) = _rows(engine, compiled, "borrower_group")
    assert (group.group_key, group.full_name, group.govt_program, group.pin_code) == (
        "G1",
        "Jyothi SHG",
        "NRLM",
        "560062",
    )


def test_consumer_and_commercial_snapshots_do_not_take_mfi_entities(engine):
    assert "borrower_group" not in compile_master(engine, "CONSUMER").sql
    assert "party_relative" not in compile_master(engine, "COMMERCIAL").sql
    assert {"borrower_group", "party_relative"} <= set(compile_master(engine, "MFI").sql)


def test_relatives_are_replaced_and_groups_overwritten_with_a_change_log(engine):
    from canonical.master.tables import dm_change_log

    _upload(engine)
    _upload(engine, relatives=[{"relative_role": "RELATIVE", "priority": 1, "full_name": "Ravi", "relation": "HUSBAND"}],
            group_values={"full_name": "Jyothi SHG", "group_account_number": "GL-2"})  # fmt: skip
    with engine.connect() as conn:
        relatives = conn.execute(select(dm_party_relative.c.relative_role, dm_party_relative.c.full_name)).all()
        group = conn.execute(select(dm_group)).one()
        log = conn.execute(select(dm_change_log.c.entity, dm_change_log.c.field)).all()
    assert [tuple(r) for r in relatives] == [("RELATIVE", "Ravi")]
    assert (group.group_account_number, group.govt_program) == ("GL-2", None)  # newest wins; a missing field clears
    assert ("dm_group", "group_account_number") in [tuple(r) for r in log]
    assert ("dm_party_relative", "*list*") in [tuple(r) for r in log]


def test_view_rows_with_groups_and_relatives_become_master_input():
    rows = {
        "party": [{"party_key": "M1", "full_name": "Lakshmi Devi"}],
        "party_relative": [
            {"party_key": "M1", "relative_role": "NOMINEE", "priority": 1, "full_name": "Anu", "relation": "DAUGHTER"}
        ],
        "account": [{"account_key": "L1", "account_number": "L1", "loan_category": "JLG_INDIVIDUAL"}],
        "account_holder": [{"account_key": "L1", "party_key": "M1"}],
        "borrower_group": [{"group_key": "G1", "full_name": "Jyothi JLG", "formed_date": "2024-01-02"}],
    }
    parties, sanctions = entities_to_master(rows, "MFI")
    assert parties[0].party_kind == "INDIVIDUAL" and parties[0].relatives[0]["full_name"] == "Anu"
    assert sanctions[0].values["loan_category"] == "JLG_INDIVIDUAL" and sanctions[0].parties[0].role == "BORROWER"
    (group,) = groups_of(rows)
    assert (group.group_key, group.values["formed_date"]) == ("G1", date(2024, 1, 2))
