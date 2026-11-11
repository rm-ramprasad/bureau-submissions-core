"""Sample input workbooks with made-up data, built from the type's workbook layout (canonical/types/<type>/inputs.yaml),
so a template always matches what the upload expects. Real data never goes into git; these are generated on demand.

    uv run python -m sources.samples MFI mfi-sample.xlsx --as-of 2026-10-31            # sample rows
    uv run python -m sources.samples MFI mfi-template.xlsx --blank                     # headers only

MFI (owner 2026-11-10, ADR 0003 step 6, option 2): a provisional workbook until the new client's data is known. The
sample has SHG groups (loan category T05, SHG file) and JLG groups (T01 / T02) plus individual loans (T03; the JLG +
Individual file), with a closed, a delinquent, a written-off loan and a member with two loans, all with valid
identity numbers (Aadhaar check digit), PINs of their state and MFI codes, so it passes the upload checks.
"""

from __future__ import annotations

import argparse
import io
import random
import sys
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from bureau_core.checks.identifiers import verhoeff_valid
from canonical.staging.spec import load_spec

Row = Mapping[str, Any]


def write_workbook(submission_type: str, rows_by_sheet: Mapping[str, Iterable[Row]] | None = None) -> bytes:
    """An .xlsx with the type's sheets and headers (row 1, in layout order) and the given rows (staging column ->
    value; dates are written as DDMMYYYY text, as the bureau files write them)."""
    import openpyxl
    from openpyxl.utils import get_column_letter

    spec = load_spec(submission_type)
    book = openpyxl.Workbook(write_only=False)
    for ws in list(book.worksheets):
        book.remove(ws)
    for sheet in spec.sheets:
        ws = book.create_sheet(sheet.sheet)
        columns = list(sheet.columns)
        ws.append([sheet.columns[c] for c in columns])
        for row in (rows_by_sheet or {}).get(sheet.sheet, []):
            ws.append([_cell(row.get(c)) for c in columns])
        for i, c in enumerate(columns, 1):
            ws.column_dimensions[get_column_letter(i)].width = max(12, len(sheet.columns[c]) + 2)
        ws.freeze_panes = "A2"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _cell(value: Any) -> Any:
    if isinstance(value, date):
        return value.strftime("%d%m%Y")
    return value


# ---------------------------------------------------------------------------------------------------------- MFI
FIRST = ["Lakshmi", "Meena", "Selvi", "Kavitha", "Revathi", "Saranya", "Anitha", "Priya", "Deepa", "Gowri", "Rani",
         "Vasanthi", "Malar", "Jaya", "Sumathi", "Bhavani", "Kamala", "Latha", "Radha", "Uma"]  # fmt: skip
LAST = ["Murugan", "Raman", "Kumar", "Selvam", "Pandian", "Raju", "Krishnan", "Velu", "Subramani", "Arumugam"]
PLACES = [  # (state code, PIN prefix range from ref_state_pin, town)
    ("TN", "600", "Chennai"), ("TN", "625", "Madurai"), ("TN", "641", "Coimbatore"),
    ("KA", "560", "Bengaluru"), ("KA", "570", "Mysuru"),
]  # fmt: skip


def _aadhaar(rnd: random.Random) -> str:
    """12 digits, first 2-9, with a valid Verhoeff check digit."""
    body = str(rnd.randint(2, 9)) + "".join(str(rnd.randint(0, 9)) for _ in range(10))
    return next(body + d for d in "0123456789" if verhoeff_valid(body + d))


def _pan(rnd: random.Random) -> str:
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(rnd.choice(letters) for _ in range(3)) + "P" + rnd.choice(letters) + f"{rnd.randint(1, 9999):04d}" \
        + rnd.choice(letters)  # fmt: skip


def mfi_sample(as_of: date, *, groups: int = 6, seed: int = 1) -> dict[str, list[dict[str, Any]]]:
    """Made-up MFI rows: half the groups SHG (members' loans T05, the group loan on the Groups sheet), half JLG
    (T01 / T02), plus a few individual loans (T03, no group)."""
    rnd = random.Random(seed)  # noqa: S311  (made-up sample data, repeatable on purpose)
    loans: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    member_no = 0
    for g in range(1, groups + 1):
        shg = g <= (groups + 1) // 2
        state, prefix, town = PLACES[g % len(PLACES)]
        group_id = f"{'SHG' if shg else 'JLG'}{g:04d}"
        opened = as_of - timedelta(days=rnd.randint(120, 600))
        size = 10 if shg else 5
        group_rows.append({
            "group_id": group_id, "group_name": f"{town} {'Mahalir' if shg else 'Joint'} Group {g}",
            "group_account_number": f"GL{g:06d}" if shg else None,
            "disbursed_amount": 250000 if shg else None, "current_balance": 150000 if shg else None,
            "date_opened": opened if shg else None, "date_last_payment": as_of - timedelta(days=7) if shg else None,
            "instalments": 24 if shg else None, "repayment_frequency": "F03" if shg else None,
            "instalment_amount": 11500 if shg else None, "amount_overdue": 0 if shg else None,
            "days_past_due": 0 if shg else None, "loan_cycle": 1 if shg else None, "loan_type": "C01" if shg else None,
            "address": f"{g} Temple Street, {town}", "state": state, "pin": f"{prefix}{rnd.randint(1, 99):03d}",
            "phone_type": "P03", "phone": f"9{rnd.randint(100000000, 999999999)}",
            "bank_name": "State Bank of India", "bank_branch": "SBIN0001234", "bank_account_number": f"3{g:010d}",
            "bank_balance": 12000 if shg else None, "formed_date": opened - timedelta(days=365),
            "first_linkage_date": opened if shg else None,
            "govt_group_id": f"NRLM{g:05d}" if shg else None, "govt_program": "U01" if shg else None,
        })  # fmt: skip
        for _ in range(size):
            member_no += 1
            loans.append(_mfi_loan(rnd, as_of, member_no, group_id, "T05" if shg else rnd.choice(["T01", "T02"]),
                                   state, prefix, town, opened))  # fmt: skip
    for _ in range(3):  # individual loans, no group
        member_no += 1
        state, prefix, town = PLACES[member_no % len(PLACES)]
        loans.append(_mfi_loan(rnd, as_of, member_no, None, "T03", state, prefix, town, as_of - timedelta(days=200)))
    # the cases the files must show: closed (S07), delinquent (S05), written off (S06), two loans of one member
    loans[1] |= {"date_closed": as_of - timedelta(days=10), "current_balance": 0, "amount_overdue": 0,
                 "days_past_due": 0}  # fmt: skip
    loans[2] |= {"amount_overdue": 2300, "days_past_due": 45}
    loans[3] |= {"account_status": "S06", "write_off_amount": loans[3]["current_balance"],
                 "write_off_date": as_of - timedelta(days=30), "write_off_reason": "X01", "days_past_due": 190,
                 "amount_overdue": loans[3]["current_balance"]}  # fmt: skip
    second = dict(loans[4]) | {"account_number": f"{loans[4]['account_number']}B", "loan_cycle": 2,
                               "unique_reference": None}  # fmt: skip
    loans.append(second)
    return {"Loans": loans, "Groups": group_rows}


def _mfi_loan(rnd: random.Random, as_of: date, n: int, group_id: str | None, category: str, state: str, prefix: str,
              town: str, opened: date) -> dict[str, Any]:  # fmt: skip
    first, last = rnd.choice(FIRST), rnd.choice(LAST)
    born = date(as_of.year - rnd.randint(22, 55), rnd.randint(1, 12), rnd.randint(1, 28))
    amount = rnd.choice([20000, 25000, 30000, 40000, 50000])
    paid = rnd.randint(2, 10)
    instalment = round(amount * 1.2 / 24)
    pin = f"{prefix}{rnd.randint(1, 99):03d}"
    return {
        "account_number": f"MF{n:07d}", "unique_reference": f"UR{n:08d}", "member_id": f"M{n:06d}",
        "branch_id": f"BR{(n % 3) + 1:02d}", "centre_id": f"C{(n % 7) + 1:03d}", "group_id": group_id,
        "loan_officer": f"LO{(n % 4) + 1:02d}", "member_name": f"{first} {last}", "date_of_birth": born,
        "gender": "F", "marital_status": "M01", "key_person_name": f"{rnd.choice(LAST)} {last}",
        "key_person_relation": "K02", "relative_1_name": f"{rnd.choice(LAST)} {last}", "relative_1_relation": "K01",
        "nominee_name": f"{rnd.choice(FIRST)} {last}", "nominee_relation": "K05",
        "voter_id": f"{'ABCDEFGH'[n % 8]}{'XYZ'[n % 3]}{'KLM'[n % 3]}{rnd.randint(1000000, 9999999)}",
        "aadhaar": _aadhaar(rnd), "pan": _pan(rnd) if n % 4 == 0 else None,
        "mobile": f"{rnd.choice('6789')}{rnd.randint(100000000, 999999999)}",
        "education": rnd.choice(["Y02", "Y03", "Y04"]), "occupation": rnd.choice(["Z01", "Z03", "Z05"]),
        "religion": "R01", "social_category": rnd.choice(["V01", "V03", "V05"]),
        "member_role": "L01" if n % 10 == 1 else "L03", "member_status": "W01",
        "family_income": rnd.choice([12000, 15000, 18000, 22000]), "family_expenses": rnd.choice([6000, 8000, 9000]),
        "bank_name": "Indian Bank", "bank_branch": "IDIB000M123", "bank_account_number": f"6{n:010d}",
        "permanent_address": f"{n} Main Road, {town}", "permanent_state": state, "permanent_pin": pin,
        "current_address": f"{n} Main Road, {town}", "current_state": state, "current_pin": pin,
        "loan_category": category, "loan_cycle": 1, "loan_purpose": "A01", "loan_type": "C01",
        "sanctioned_date": opened - timedelta(days=7), "date_disbursed": opened,
        "date_last_payment": as_of - timedelta(days=rnd.randint(1, 20)),
        "applied_amount": amount, "sanctioned_amount": amount, "disbursed_amount": amount, "instalments": 24,
        "repayment_frequency": "F03", "instalment_amount": instalment,
        "current_balance": max(amount - paid * round(amount / 24), 0), "amount_overdue": 0, "days_past_due": 0,
        "insurance_indicator": "Y", "insurance_type": "L02", "sum_assured": amount,
    }  # fmt: skip


SAMPLES = {"MFI": mfi_sample}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m sources.samples", description=__doc__.split("\n\n")[0])
    p.add_argument("submission_type", type=str.upper)
    p.add_argument("out", type=Path, help="the .xlsx to write")
    p.add_argument("--as-of", type=date.fromisoformat, default=None, help="data date (default: last month's end)")
    p.add_argument("--groups", type=int, default=6)
    p.add_argument("--blank", action="store_true", help="headers only (a template to fill)")
    a = p.parse_args(argv)
    if not a.blank and a.submission_type not in SAMPLES:
        print(f"No sample data for {a.submission_type}; use --blank for its template.", file=sys.stderr)
        return 2
    as_of = a.as_of or date.today().replace(day=1) - timedelta(days=1)
    rows = None if a.blank else SAMPLES[a.submission_type](as_of, groups=a.groups)
    a.out.write_bytes(write_workbook(a.submission_type, rows))
    print(f"wrote {a.out}" + ("" if a.blank else f" (as of {as_of:%Y-%m-%d}: upload it with this as-of date)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
