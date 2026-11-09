-- L2b LMS Excel uploads (2026-11-08, ADR 0002, PR 3): for clients without an LMS view, each upload's positions as typed
-- (text), one row per Excel row; automn.vw_pos_upload (views/position.sql) parses them and keeps the newest LOADED
-- upload per account, sanction and date. Append only. Nothing writes it yet (upload page: ADR PR 7).
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 012_lms_position_upload`; then run
-- the views (position.sql reads this table). No BEGIN/COMMIT (the runner wraps the file).
CREATE TABLE automn.stg_lms_position (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	account_number NVARCHAR(100) NULL,
	sanction_number NVARCHAR(100) NULL,
	position_date NVARCHAR(100) NULL,
	current_balance NVARCHAR(100) NULL,
	amount_overdue NVARCHAR(100) NULL,
	days_past_due NVARCHAR(100) NULL,
	asset_classification NVARCHAR(100) NULL,
	asset_classification_date NVARCHAR(100) NULL,
	credit_facility_status NVARCHAR(100) NULL,
	status_date NVARCHAR(100) NULL,
	date_last_payment NVARCHAR(100) NULL,
	actual_payment_amount NVARCHAR(100) NULL,
	date_closed NVARCHAR(100) NULL,
	date_reported NVARCHAR(100) NULL,
	high_credit_amount NVARCHAR(100) NULL,
	credit_limit NVARCHAR(100) NULL,
	cash_limit NVARCHAR(100) NULL,
	drawing_power NVARCHAR(100) NULL,
	overdue_bucket_1 NVARCHAR(100) NULL,
	overdue_bucket_2 NVARCHAR(100) NULL,
	overdue_bucket_3 NVARCHAR(100) NULL,
	overdue_bucket_4 NVARCHAR(100) NULL,
	overdue_bucket_5 NVARCHAR(100) NULL,
	written_off_total NVARCHAR(100) NULL,
	written_off_principal NVARCHAR(100) NULL,
	settlement_amount NVARCHAR(100) NULL,
	npa_contracts_amount NVARCHAR(100) NULL,
	wilful_default_date NVARCHAR(100) NULL,
	ufce_amount NVARCHAR(100) NULL,
	ufce_date NVARCHAR(100) NULL,
	PRIMARY KEY (load_id, source_row),
	CONSTRAINT fk_stg_lms_position_load FOREIGN KEY (load_id) REFERENCES automn.input_load (load_id)
);
CREATE INDEX ix_stg_lms_position_key ON automn.stg_lms_position (account_number, position_date, load_id);
