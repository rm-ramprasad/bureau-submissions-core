"""L2a demographics master (ADR 0002, migration 010_demographics_master): the current customers (dm_party), their
identifiers, phones, e-mails and addresses, sanctions (dm_sanction), who is on a sanction in which role
(dm_sanction_party), securities (dm_security) and a log of overwritten values (dm_change_log).

Overwrite by key: the newest upload wins completely (a blank clears), lists are replaced per customer, nothing is
deleted (is_active). L1 staging is the history. Nothing reads or writes these tables yet (PR 2: apply an upload).
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


def _code() -> Unicode:
    return Unicode(40)


def _versioned() -> list[Column]:
    """Every master row: active flag, the uploads that created and last wrote it, when."""
    return [
        Column("is_active", Boolean, nullable=False, server_default=true()),
        Column("first_load_id", _PK, nullable=False),
        Column("last_load_id", _PK, nullable=False),
        Column("updated_at", DateTime, nullable=False),
    ]


dm_party = Table(
    "dm_party",
    metadata,
    Column("party_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", Unicode(30), nullable=False),
    Column("customer_id", Unicode(50), nullable=False),
    Column("party_kind", Unicode(20), nullable=False),  # INDIVIDUAL / ENTITY
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
    Column("party_type", _code()),  # bureau type: INDIAN_INDIVIDUAL, INDIAN_ENTITY, ... (migration 013)
    *_versioned(),
    UniqueConstraint("tenant_id", "customer_id", name="uq_dm_party_customer"),
)


def _party_list(name: str, *columns: Column) -> Table:
    return Table(
        name,
        metadata,
        Column("party_id", _PK, ForeignKey("automn.dm_party.party_id"), primary_key=True),
        Column("seq", Integer, primary_key=True, autoincrement=False),
        *columns,
        Column("load_id", _PK, nullable=False),
    )


dm_party_identifier = _party_list(
    "dm_party_identifier",
    Column("id_type", _code(), nullable=False),  # PAN, AADHAAR, PASSPORT, VOTER_ID, CIN, DIN, GSTIN, CKYC, ...
    Column("id_number", Unicode(50), nullable=False),
    Column("issue_date", Date),
    Column("expiry_date", Date),
)
dm_party_phone = _party_list(
    "dm_party_phone",
    Column("phone_number", Unicode(20), nullable=False),
    Column("extension", Unicode(10)),
    Column("phone_type", _code()),
    Column("area_code", Unicode(10)),  # migration 013
    Column("priority", Integer),
)
dm_party_email = _party_list(
    "dm_party_email", Column("email", Unicode(200), nullable=False), Column("priority", Integer)
)
dm_party_address = _party_list(
    "dm_party_address",
    *(Column(f"address_line_{i}", Unicode(200)) for i in range(1, 6)),
    Column("city", Unicode(100)),
    Column("district", Unicode(100)),
    Column("state_code", _code()),
    Column("pin_code", Unicode(10)),
    Column("country", _code()),
    Column("address_category", _code()),  # PERMANENT, RESIDENCE, OFFICE, REGISTERED, ...
    Column("residence_code", _code()),
    Column("priority", Integer),
)

dm_sanction = Table(
    "dm_sanction",
    metadata,
    Column("sanction_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", Unicode(30), nullable=False),
    Column("account_number", Unicode(50), nullable=False),  # links to L2b
    Column("sanction_number", Unicode(50), nullable=False, server_default=""),  # '' = the account's only sanction
    Column("account_type", _code()),
    Column("date_opened", Date),
    Column("sanctioned_amount", _AMOUNT),
    Column("currency", Unicode(10)),
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
    Column("suit_filed_status", _code()),  # migration 013
    Column("suit_reference", Unicode(50)),
    Column("suit_amount", _AMOUNT),
    Column("suit_date", Date),
    Column("collateral_value", _AMOUNT),
    Column("collateral_type", _code()),
    *_versioned(),
    UniqueConstraint("tenant_id", "account_number", "sanction_number", name="uq_dm_sanction_key"),
)

dm_sanction_party = Table(
    "dm_sanction_party",
    metadata,
    Column("sanction_id", _PK, ForeignKey("automn.dm_sanction.sanction_id"), primary_key=True),
    Column("party_id", _PK, ForeignKey("automn.dm_party.party_id"), primary_key=True),
    Column("role", Unicode(30), primary_key=True),  # BORROWER, CO_BORROWER, GUARANTOR, DIRECTOR, PARTNER, ...
    Column("ownership_indicator", _code()),
    Column("relationship_type", _code()),
    Column("control_percentage", _RATE),
    Column("guarantee_invocation_date", Date),
    Column("load_id", _PK, nullable=False),
    Index("ix_dm_sanction_party_party", "party_id"),
)

dm_security = Table(
    "dm_security",
    metadata,
    Column("sanction_id", _PK, ForeignKey("automn.dm_sanction.sanction_id"), primary_key=True),
    Column("security_ref", Unicode(50), primary_key=True),
    Column("security_type", _code()),
    Column("security_classification", _code()),
    Column("security_value", _AMOUNT),
    Column("currency", Unicode(10)),
    Column("valuation_date", Date),
    Column("load_id", _PK, nullable=False),
)

dm_cheque_dishonour = Table(
    "dm_cheque_dishonour",
    metadata,
    Column("sanction_id", _PK, ForeignKey("automn.dm_sanction.sanction_id"), primary_key=True),
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("dishonour_date", Date),
    Column("amount", _AMOUNT),
    Column("instrument_number", Unicode(50)),
    Column("dishonour_count", Integer),
    Column("cheque_issue_date", Date),
    Column("reason", _code()),
    Column("load_id", _PK, nullable=False),
)

dm_change_log = Table(
    "dm_change_log",
    metadata,
    Column("change_id", _PK, primary_key=True, autoincrement=True),
    Column("tenant_id", Unicode(30), nullable=False),
    Column("entity", _code(), nullable=False),  # dm_party, dm_party_phone, dm_sanction, ...
    Column("entity_key", Unicode(150), nullable=False),  # customer ID, account number / sanction number, ...
    Column("field", Unicode(60), nullable=False),  # column, or '*list*' when a list was replaced
    Column("old_value", Unicode(1000)),
    Column("new_value", Unicode(1000)),
    Column("load_id", _PK, nullable=False),
    Column("changed_at", DateTime, nullable=False),
    Index("ix_dm_change_log_key", "tenant_id", "entity", "entity_key", "changed_at"),
)

MASTER_TABLES = (
    dm_party,
    dm_party_identifier,
    dm_party_phone,
    dm_party_email,
    dm_party_address,
    dm_sanction,
    dm_sanction_party,
    dm_security,
    dm_cheque_dishonour,
    dm_change_log,
)
