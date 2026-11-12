-- The LMS's sanction terms per date (owner 2026-11-12: read at request time, by the request's as-of date; the customer
-- tables keep only what the workbook gives): the LMS upload table gets the same new columns as the position view
-- (views/position.sql, run it in SSMS too). Run once in rarfinmisdb in SSMS (admin login), then
-- `ops.ps1 migrate --mark-applied 023_position_terms`. No BEGIN/COMMIT (the runner wraps the file).
ALTER TABLE automn.stg_lms_position ADD
	date_opened NVARCHAR(100) NULL,
	sanctioned_amount NVARCHAR(100) NULL,
	currency NVARCHAR(100) NULL,
	rate_of_interest NVARCHAR(100) NULL,
	repayment_tenure NVARCHAR(100) NULL,
	emi_amount NVARCHAR(100) NULL,
	maturity_date NVARCHAR(100) NULL,
	renewal_date NVARCHAR(100) NULL,
	previous_account_number NVARCHAR(100) NULL,
	restructured_notional_amount NVARCHAR(100) NULL,
	restructuring_reason NVARCHAR(100) NULL,
	security_coverage NVARCHAR(100) NULL,
	guarantee_coverage NVARCHAR(100) NULL,
	other_lenders NVARCHAR(100) NULL,
	dispute_id NVARCHAR(100) NULL,
	remark NVARCHAR(100) NULL;
