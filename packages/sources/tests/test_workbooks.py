"""The consumer test workbooks in input-templates/ (local only, never committed; `-m workbook`, BSC_WORKBOOK_DIR).

- Consumer_Test_Coverage.xlsx (every column on every row, every label / code value at least once) and
  Consumer_Test_AllFields.xlsx load with no errors, with the real labels (labels.sql, as on rarfincare).
- Every row of Consumer_Coverage_Expected.xlsx "Expected codes": the workbook value gives the expected canonical value
  and that value the expected bureau code, through automn.ref_code_translation, matched as the views match
  (vw_ref_label: label ignoring case and outer spaces; vw_ref_code: the bureau code, active rows first).
  A bureau code that several canonical values share (Old Acc Type 39: MUDRA_LOAN, MUDRA_WORKING_CAPITAL) is read as
  the first (as vw_ref_code does); only the bureau code is compared there, as it is what reaches the file.
  Where each code lands in the TUDF is checked by the bureau_core golden tests, not here.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import Engine, select, text, update

from canonical.migrate import MIGRATIONS_ROOT
from canonical.schema import ref_code_translation
from canonical.testing import TENANT, add_view_accounts
from sources.excel import load_workbook

pytestmark = pytest.mark.workbook

TEMPLATES = Path(os.environ.get("BSC_WORKBOOK_DIR", "/nonexistent"))
AS_OF = date(2026, 10, 31)

COLUMNS = {  # workbook column -> (code list, how the view reads it)
    "Gender (Name)": ("gender", "label"),
    "State Code 1 (Name)": ("state_code", "label"),
    "State Code 2 (Name)": ("state_code", "label"),
    "Address Category 1 (Name)": ("address_category", "label"),
    "Address Category 2 (Name)": ("address_category", "label"),
    "Residence Code 1 (Name)": ("residence_code", "label"),
    "Residence Code 2 (Name)": ("residence_code", "label"),
    "Account Type (Name)": ("account_type", "label"),
    "Ownership Indicator (Name)": ("ownership_indicator", "label"),
    "Suit Filed / Wilful Default (Name)": ("suit_filed_status", "label"),
    "Type Of Collateral (Name)": ("collateral_type", "label"),
    "Payment Frequency (Name)": ("payment_frequency", "label"),
    "Occupation Code (Name)": ("occupation", "label"),
    "Old Acc Type": ("account_type", "code"),
    "Old Ownership Indicator": ("ownership_indicator", "code"),
    "Net/Gross Income Indicator": ("income_basis", "code"),
    "Monthly/Annual Income Indicator": ("income_period", "code"),
}


def apply_real_labels(engine: Engine) -> None:
    """The labels rarfincare has: the UPDATEs of labels.sql (T-SQL N'' literals made plain for SQLite)."""
    sql = (MIGRATIONS_ROOT / "azure" / "views" / "labels.sql").read_text("utf-8")
    updates = re.findall(r"^UPDATE automn\.ref_code_translation SET label = N'.*;$", sql, flags=re.M)
    assert len(updates) > 400
    with engine.begin() as conn:
        conn.execute(update(ref_code_translation).values(label=None))
        for stmt in updates:
            conn.execute(text(stmt.replace("SET label = N'", "SET label = '", 1)))


def accounts(path: Path) -> list[str]:
    ws = openpyxl.load_workbook(path, read_only=True)["Consumer"]
    header = [str(h or "") for h in next(ws.iter_rows(max_row=1, values_only=True))]
    col = next(i for i, h in enumerate(header) if h.lower().startswith("curr/new account no"))
    return [str(r[col]) for r in ws.iter_rows(min_row=2, values_only=True) if r[col] is not None]


@pytest.fixture
def labelled(engine: Engine) -> Engine:
    apply_real_labels(engine)
    return engine


@pytest.mark.parametrize("file", ["Consumer_Test_Coverage.xlsx", "Consumer_Test_AllFields.xlsx"])
def test_test_workbooks_load_with_the_real_labels(labelled: Engine, file: str) -> None:
    path = TEMPLATES / file
    add_view_accounts(labelled, AS_OF, *accounts(path))
    result = load_workbook(
        labelled,
        "CONSUMER",
        path.read_bytes(),
        tenant_id=TENANT.tenant_id,
        file_name=file,
        loaded_by="t",
        clock=lambda: datetime(2026, 11, 1),
    )
    assert result.status == "LOADED" and result.errors == [], result.errors[:10]
    assert result.row_counts["Consumer"] == len(accounts(path))


def test_every_expected_code_of_the_coverage_workbook(labelled: Engine) -> None:
    t = ref_code_translation.c
    with labelled.connect() as conn:
        rows = conn.execute(
            select(t.code_list, t.code, t.output_code, t.label, t.is_active).where(t.submission_type == "CONSUMER")
        ).all()

    def by_label(code_list: str, value: str) -> str | None:
        key = value.strip().upper()
        found = sorted(r.code for r in rows if r.code_list == code_list and (r.label or "").strip().upper() == key)
        return found[0] if found and any(r.is_active for r in rows if r.code == found[0]) else None

    def by_code(code_list: str, value: str) -> str | None:
        key = value.strip().upper()
        found = sorted(
            (not r.is_active, r.code)
            for r in rows
            if r.code_list == code_list and (r.output_code or "").strip().upper() == key
        )
        return found[0][1] if found else None

    def bureau_code(code_list: str, canonical: str) -> str | None:
        return next((r.output_code for r in rows if r.code_list == code_list and r.code == canonical), None)

    ws = openpyxl.load_workbook(TEMPLATES / "Consumer_Coverage_Expected.xlsx", read_only=True)["Expected codes"]
    problems, checked = [], 0
    for row_no, account, column, value, canonical, code, _where in ws.iter_rows(min_row=2, values_only=True):
        code_list, kind = COLUMNS[column]
        got = (by_label if kind == "label" else by_code)(code_list, str(value))
        out = bureau_code(code_list, got) if got else None
        shared = (
            kind == "code" and len({r.code for r in rows if r.code_list == code_list and r.output_code == code}) > 1
        )
        if out != str(code) or (got != canonical and not shared):  # a code several values share: the code counts
            problems.append(
                f"row {row_no} {account} {column}={value!r}: {got} -> {out}, expected {canonical} -> {code}"
            )
        checked += 1
    assert checked > 900
    assert not problems, "\n".join(problems[:30])
