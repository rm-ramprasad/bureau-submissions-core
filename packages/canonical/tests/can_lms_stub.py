"""Stand-ins for the client's LMS views in schema `wip` (on rarfincare: views over its loan system). The source views
join them, so a database test needs them: tables with the columns the views read and plausible types."""

from __future__ import annotations

from sqlalchemy import Engine

D, N, INT, T, S = "DATE", "DECIMAL(20, 2)", "INT", "VARCHAR(40)", "VARCHAR(100)"

CONSUMER = {
    "AccountNumber": S,
    "TransactionDate": D,
    "AssetClassification": S,
    "TL_CreditFacilityStatus": INT,
    "TL_DateDisbursed": T,
    "TL_DateofLastPayment": T,
    "TL_DateClosed": T,
    "TL_DateReported": T,
    "TL_NoofDaysPastDue": INT,
    "TL_RepaymentTenure": INT,
    "TL_RateofInterest": N,
    **{
        c: N
        for c in (
            "TL_SanctionedAmt",
            "TL_CurrentBalance",
            "TL_AmtOverdue",
            "TL_CreditLimit",
            "TL_CashLimit",
            "TL_EMIAmount",
            "TL_WrittenoffAmountTotal",
            "TL_WrittenOffPrincipalAmount",
            "TL_SettlementAmt",
            "TL_ActualPaymentAmt",
        )
    },
}

COMMERCIAL = {
    "AccountNumber": S,
    "SanctionRefNo": S,
    "transactiondate": D,
    "AcClosedDate": D,
    "LastPaymentDate": D,
    "IsAccountClosed": S,
    "RBIODDays": INT,
    "Tenure": INT,
    "OTHER_BK": S,
    "Major reasons for Restructuring": INT,
    "Asset based Security coverage": INT,
    "Guarantee Coverage": INT,
    **{
        c: T
        for c in (
            "Sanction Date",
            "Account Status Date",
            "Asset Classification Date",
            "Date Classified as Wilful Default",
            "Loan Renewal Date",
            "Maturity Date",
            "UFCE Date",
        )
    },
    **{
        c: S
        for c in (
            "Account Status",
            "Asset Classification",
            "Bank Remark Code",
            "Currency Code",
            "Dispute ID No.",
            "Previous Account Number",
        )
    },
    **{
        c: N
        for c in (
            "Amount Overdue",
            "Amount of Contracts Classified as NPA",
            "Current Balance",
            "Drawing Power",
            "High Credit",
            "Installment Amount",
            "Last Repaid Amount",
            "Notional Amount of Outstanding Restructured Contracts",
            "Overdue Bucket 01 ( 1 – 30 days)",
            "Overdue Bucket 02 ( 31 – 60 days)",
            "Overdue Bucket 03 ( 61 – 90 days)",
            "Overdue Bucket 04 (91 – 180 days)",
            "Overdue Bucket 05 (Above 180 days)",
            "Sanctioned Amount",
            "Settled Amount",
            "UFCE Amount",
            "Written Off Amount",
        )
    },
}

STUBS = {"VwConsumerSubmissionAccountSegment": CONSUMER, "VwCommercialSubmissionCR": COMMERCIAL}


def _name(engine: Engine, name: str) -> str:
    """Postgres folds unquoted names to lower case, as the generated views write them; names with spaces are quoted."""
    if " " not in name and "." not in name:
        return name
    return f"[{name}]" if engine.dialect.name == "mssql" else f'"{name}"'


def create_lms_stubs(engine: Engine) -> None:
    with engine.begin() as conn:
        for table, columns in STUBS.items():
            cols = ", ".join(f"{_name(engine, c)} {t}" for c, t in columns.items())
            conn.exec_driver_sql(f"CREATE TABLE wip.{table} ({cols})")
