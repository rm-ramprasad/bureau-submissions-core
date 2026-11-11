"""L2a customer tables (ADR 0002; sanction model, owner 2026-11-11, migration 019_sanction_master): one data model for
consumer and commercial, keyed by the SANCTION REFERENCE NUMBER.

  dm_sanction                one row per sanction: its borrower (the customer) and who they are
   +- dm_sanction_holder         other holders of the sanction (consumer: joint holders, co-borrowers, guarantors on
   |                             the account), holder_seq 1, 2, ...; the borrower is holder_seq 0, on dm_sanction
   +- dm_sanction_identifier     IDs, phones, e-mails, addresses of the borrower (holder_seq 0) and the holders
   +- dm_sanction_phone / _email / _address (AS)
   +- dm_sanction_guarantor (GS) many guarantors, each a flat row (name, IDs, one address, phones)
   +- dm_sanction_relationship (RS)  many related parties of the borrower, flat rows
   +- dm_sanction_security (SS)  many securities
   +- dm_sanction_cheque (CD)    cheque dishonours
   +- dm_sanction_account        the loan accounts of the sanction (one or more), keyed by account number: the link to
                                 the LMS position (L2b, per account and date) and the account's static fields

Consumer: the sanction reference number is the account number (rarfincare's consumer data has no sanction number).
Overwrite by key: the newest upload wins completely (a blank clears), lists are replaced per sanction, nothing is
deleted (is_active). L1 staging is the history; dm_change_log keeps overwritten values.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Table,
    Unicode,
    UniqueConstraint,
    true,
)

from canonical.db import metadata

_PK = BigInteger().with_variant(Integer(), "sqlite")
_AMOUNT = Numeric(20, 2)
_RATE = Numeric(9, 4)
KEY_LENGTH = 100  # sanction reference numbers and account numbers


def _code() -> Unicode:
    return Unicode(40)


def _like(entity: str, *skip: str) -> list[Column]:
    """Columns typed like the snapshot's (canonical model): text as Unicode of the same length."""
    from canonical.schema import ENTITY_TABLES

    out = []
    for c in ENTITY_TABLES[entity].columns:
        if c.name in ("row_id", "snapshot_id", *skip):
            continue
        length = getattr(c.type, "length", None)
        out.append(Column(c.name, Unicode(length) if length else c.type))
    return out


def _versioned() -> list[Column]:
    """Every master row: active flag, the uploads that created and last wrote it, when."""
    return [
        Column("is_active", Boolean, nullable=False, server_default=true()),
        Column("first_load_id", _PK, nullable=False),
        Column("last_load_id", _PK, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    ]


def _person() -> list[Column]:
    """Who a borrower or holder is (consumer: a person; commercial: a business)."""
    return [
        Column("party_kind", Unicode(20), nullable=False),  # INDIVIDUAL / ENTITY
        Column("ownership_indicator", _code()),
        Column("full_name", Unicode(200)),
        Column("name_prefix", Unicode(20)),
        Column("short_name", Unicode(20)),
        Column("birth_date", Date),
        Column("gender", _code()),
        Column("occupation", _code()),
        Column("income", _AMOUNT),
        Column("income_basis", _code()),
        Column("income_period", _code()),
        Column("legal_constitution", _code()),
        Column("business_category", _code()),
        Column("industry_type", _code()),
        Column("incorporation_date", Date),
        Column("activity_class_1", Unicode(100)),
        Column("activity_class_2", Unicode(100)),
        Column("activity_class_3", Unicode(100)),
        Column("hsn_code", Unicode(20)),
        Column("sales_amount", _AMOUNT),
        Column("sales_year", Date),
        Column("employee_count", Integer),
        Column("credit_rating", Unicode(20)),
        Column("rating_agency", _code()),
        Column("rating_date", Date),
        Column("rating_expiry_date", Date),
        Column("party_type", _code()),  # bureau type: INDIAN_INDIVIDUAL, INDIAN_ENTITY, ...
    ]


dm_sanction = Table(
    "dm_sanction",
    metadata,
    Column("sanction_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", Unicode(30), nullable=False),
    Column("sanction_reference_number", Unicode(KEY_LENGTH), nullable=False),
    *_person(),  # the borrower
    *_versioned(),
    UniqueConstraint("tenant_id", "sanction_reference_number", name="uq_dm_sanction_ref"),
)


def _sanction_list(name: str, *columns: Column, holder: bool = False) -> Table:
    keys = [Column("holder_seq", Integer, primary_key=True, autoincrement=False)] if holder else []
    return Table(
        name,
        metadata,
        Column("sanction_id", _PK, ForeignKey("automn.dm_sanction.sanction_id"), primary_key=True),
        *keys,
        Column("seq", Integer, primary_key=True, autoincrement=False),
        *columns,
        Column("load_id", _PK, nullable=False),
    )


dm_sanction_holder = Table(
    "dm_sanction_holder",
    metadata,
    Column("sanction_id", _PK, ForeignKey("automn.dm_sanction.sanction_id"), primary_key=True),
    Column("holder_seq", Integer, primary_key=True, autoincrement=False),  # 1, 2, ... (0 = the borrower)
    Column("role", Unicode(30), nullable=False),  # CO_BORROWER / GUARANTOR
    *_person(),
    Column("load_id", _PK, nullable=False),
)

dm_sanction_identifier = _sanction_list(
    "dm_sanction_identifier",
    Column("id_type", _code(), nullable=False),  # PAN, AADHAAR, PASSPORT, VOTER_ID, CIN, TIN, CKYC, ...
    Column("id_number", Unicode(50), nullable=False),
    Column("issue_date", Date),
    Column("expiry_date", Date),
    holder=True,
)
dm_sanction_phone = _sanction_list(
    "dm_sanction_phone",
    Column("phone_number", Unicode(60), nullable=False),
    Column("extension", Unicode(10)),
    Column("phone_type", _code()),
    Column("area_code", Unicode(10)),
    Column("priority", Integer),
    holder=True,
)
dm_sanction_email = _sanction_list(
    "dm_sanction_email", Column("email", Unicode(200), nullable=False), Column("priority", Integer), holder=True
)
dm_sanction_address = _sanction_list(
    "dm_sanction_address",
    *(Column(f"address_line_{i}", Unicode(200)) for i in range(1, 6)),
    Column("city", Unicode(100)),
    Column("district", Unicode(100)),
    Column("state_code", _code()),
    Column("pin_code", Unicode(20)),
    Column("country", _code()),
    Column("address_category", _code()),
    Column("residence_code", _code()),
    Column("priority", Integer),
    holder=True,
)
dm_sanction_guarantor = _sanction_list("dm_sanction_guarantor", *_like("guarantor", "account_key"))  # GS
dm_sanction_relationship = _sanction_list("dm_sanction_relationship", *_like("relationship", "party_key"))  # RS
dm_sanction_security = _sanction_list("dm_sanction_security", *_like("security", "account_key"))  # SS
dm_sanction_cheque = _sanction_list("dm_sanction_cheque", *_like("cheque_dishonour", "account_key"))  # CD

dm_sanction_account = Table(
    "dm_sanction_account",
    metadata,
    Column("account_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", Unicode(30), nullable=False),
    Column("account_number", Unicode(KEY_LENGTH), nullable=False),  # links to L2b (the LMS position)
    Column("sanction_id", _PK, ForeignKey("automn.dm_sanction.sanction_id"), nullable=False),
    # the account's static fields (the LMS figures for a date are L2b, not stored here)
    Column("account_type", _code()),
    Column("date_opened", Date),
    Column("sanctioned_amount", _AMOUNT),
    Column("currency", Unicode(40)),
    Column("rate_of_interest", _RATE),
    Column("repayment_tenure", Integer),
    Column("emi_amount", _AMOUNT),
    Column("payment_frequency", _code()),
    Column("maturity_date", Date),
    Column("renewal_date", Date),
    Column("branch_code", Unicode(20)),
    Column("previous_branch_code", Unicode(20)),
    Column("previous_account_number", Unicode(50)),
    Column("old_member_code", Unicode(20)),
    Column("old_member_short_name", Unicode(20)),
    Column("old_account_number", Unicode(50)),
    Column("old_account_type", _code()),
    Column("old_ownership_indicator", _code()),
    Column("restructured_notional_amount", _AMOUNT),
    Column("restructuring_reason", _code()),
    Column("security_coverage", _code()),
    Column("guarantee_coverage", _code()),
    Column("other_lenders", Unicode(200)),
    Column("dispute_id", Unicode(50)),
    Column("remark", Unicode(500)),
    Column("suit_filed_status", _code()),
    Column("suit_reference", Unicode(50)),
    Column("suit_amount", _AMOUNT),
    Column("suit_date", Date),
    Column("collateral_value", _AMOUNT),
    Column("collateral_type", _code()),
    Column("actual_payment_amount", _AMOUNT),  # the workbook's, used when the LMS has none
    *_versioned(),
    UniqueConstraint("tenant_id", "account_number", name="uq_dm_sanction_account"),
    Index("ix_dm_sanction_account_sanction", "sanction_id"),
)

dm_change_log = Table(
    "dm_change_log",
    metadata,
    Column("change_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", Unicode(30), nullable=False),
    Column("entity", _code(), nullable=False),  # dm_sanction, dm_sanction_address, dm_sanction_account, ...
    Column("entity_key", Unicode(150), nullable=False),  # sanction reference number or account number
    Column("field", Unicode(60), nullable=False),  # column, or '*list*' when a list was replaced
    Column("old_value", Unicode(1000)),
    Column("new_value", Unicode(1000)),
    Column("load_id", _PK, nullable=False),
    Column("changed_at", DateTime, nullable=False),
    Index("ix_dm_change_log_key", "tenant_id", "entity", "entity_key", "changed_at"),
)

HOLDER_LISTS = {  # PartyIn-style list name -> table (keyed by sanction, holder_seq, seq)
    "identifiers": dm_sanction_identifier,
    "phones": dm_sanction_phone,
    "emails": dm_sanction_email,
    "addresses": dm_sanction_address,
}
SANCTION_LISTS = {  # list name -> table (keyed by sanction, seq)
    "guarantors": dm_sanction_guarantor,
    "relationships": dm_sanction_relationship,
    "securities": dm_sanction_security,
    "cheques": dm_sanction_cheque,
}

MASTER_TABLES = (
    dm_sanction,
    dm_sanction_holder,
    dm_sanction_identifier,
    dm_sanction_phone,
    dm_sanction_email,
    dm_sanction_address,
    dm_sanction_guarantor,
    dm_sanction_relationship,
    dm_sanction_security,
    dm_sanction_cheque,
    dm_sanction_account,
    dm_change_log,
)
