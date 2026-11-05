"""Bureau Data Submission Forms for tests (the 72-column Excel the converter reads, types/consumer/excel_layout.yaml).

The input workbook users upload is a different sheet now ('Consumer', labels); the bureau form keeps the bureau's
template. Rows are dicts keyed by FIELDS (field name -> the form's header)."""

import io

import openpyxl

from bureau_core.formats.excel import excel_layout

FIELDS = {
    "consumer_name": "Consumer Name",
    "date_of_birth": "Date of Birth",
    "gender": "Gender",
    "income_tax_id_number": "Income Tax ID Number",
    "passport_number": "Passport Number",
    "passport_issue_date": "Passport Issue Date",
    "passport_expiry_date": "Passport Expiry Date",
    "voter_id_number": "Voter ID Number",
    "driving_license_number": "Driving License Number",
    "driving_license_issue_date": "Driving License Issue Date",
    "driving_license_expiry_date": "Driving License Expiry Date",
    "ration_card_number": "Ration Card Number",
    "universal_id_number": "Universal ID Number",
    "additional_id_no_1": "Additional ID #1",
    "additional_id_no_2": "Additional ID #2",
    "telephone_no_mobile": "Telephone No.Mobile",
    "telephone_no_residence": "Telephone No.Residence",
    "telephone_no_office": "Telephone No.Office",
    "extension_office": "Extension Office",
    "telephone_no_other": "Telephone No.Other ",
    "extension_other": "Extension Other",
    "email_id_1": "Email ID 1",
    "email_id_2": "Email ID 2",
    "address_line_1": "Address Line 1",
    "state_code_1": "State Code 1",
    "pin_code_1": "PIN Code 1",
    "address_category_1": "Address Category 1",
    "residence_code_1": "Residence Code 1",
    "address_line_2": "Address Line 2",
    "state_code_2": "State Code 2",
    "pin_code_2": "PIN Code 2",
    "address_category_2": "Address Category 2",
    "residence_code_2": "Residence Code 2",
    "current_new_member_code": "Current/New Member Code",
    "current_new_member_short_name": "Current/New Member Short Name",
    "curr_new_account_no": "Curr/New Account No",
    "account_type": "Account Type",
    "ownership_indicator": "Ownership Indicator",
    "date_opened_disbursed": "Date Opened/Disbursed",
    "date_of_last_payment": "Date of Last Payment",
    "date_closed": "Date Closed",
    "date_reported": "Date Reported",
    "high_credit_sanctioned_amt": "High Credit/Sanctioned Amt",
    "current_balance": "Current  Balance",
    "amt_overdue": "Amt Overdue",
    "no_of_days_past_due": "No of Days Past Due",
    "old_mbr_code": "Old Mbr Code",
    "old_mbr_short_name": "Old Mbr Short Name",
    "old_acc_no": "Old Acc No",
    "old_acc_type": "Old Acc Type",
    "old_ownership_indicator": "Old Ownership Indicator",
    "suit_filed_wilful_default": "Suit Filed / Wilful Default",
    "credit_facility_status": "Credit Facility Status",
    "asset_classification": "Asset Classification",
    "value_of_collateral": "Value of Collateral",
    "type_of_collateral": "Type of Collateral",
    "credit_limit": "Credit Limit",
    "cash_limit": "Cash Limit",
    "rate_of_interest": "Rate of Interest",
    "repaymenttenure": "RepaymentTenure",
    "emi_amount": "EMI Amount",
    "written_off_amount_total": "Written- off Amount (Total) ",
    "written_off_principal_amount": "Written- off Principal Amount",
    "settlement_amt": "Settlement Amt",
    "payment_frequency": "Payment Frequency",
    "actual_payment_amt": "Actual Payment Amt",
    "occupation_code": "Occupation Code",
    "income": "Income",
    "net_gross_income_indicator": "Net/Gross Income Indicator",
    "monthly_annual_income_indicator": "Monthly/Annual Income Indicator",
    "ckyc": "CKYC",
    "g_ram_g": "G RAM G",
}


def bureau_form(rows=(), *, headers=None, sheet=None) -> bytes:
    """The bureau form with these rows (dicts field -> value); `headers` replaces the header row."""
    layout = excel_layout("CONSUMER")
    names = {h: f for f, h in FIELDS.items()}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet or layout.sheet
    cols = [c.header for c in layout.columns]
    ws.append(headers or cols)
    for r in rows:
        ws.append([r.get(names.get(h, h)) for h in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
