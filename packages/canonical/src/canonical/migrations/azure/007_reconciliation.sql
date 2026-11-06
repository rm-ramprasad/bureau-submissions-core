-- Reconciliation results in the database (2026-11-06, issue #39): each generated consumer file's reconciliation
-- (bureau Excel vs TUDF) is kept like the validation results, so trends across months can be queried (which columns
-- mismatch, which bureau's files fail) and the result outlives the files. The review Excel shows the same data.
-- Run once in rarfinmisdb, or let `ops.ps1 migrate` apply it; no BEGIN/COMMIT (the runner wraps the file).
CREATE TABLE automn.reconciliation_summary (
	request_id BIGINT NOT NULL,
	snapshot_id BIGINT NOT NULL,
	submission_type NVARCHAR(20) NOT NULL,
	bureau_code NVARCHAR(20) NOT NULL,
	matched BIT NOT NULL,
	form_rows INTEGER NOT NULL,
	tudf_records INTEGER NOT NULL,
	mismatches INTEGER NOT NULL,
	created_at DATETIME2 NOT NULL,
	PRIMARY KEY (request_id)
);
CREATE INDEX ix_reconciliation_summary_time ON automn.reconciliation_summary (created_at);
CREATE TABLE automn.reconciliation_check (
	request_id BIGINT NOT NULL,
	check_no INTEGER NOT NULL,
	check_name NVARCHAR(200) NOT NULL,
	form_value NVARCHAR(500) NULL,
	tudf_value NVARCHAR(500) NULL,
	ok BIT NOT NULL,
	PRIMARY KEY (request_id, check_no)
);
CREATE TABLE automn.reconciliation_column (
	request_id BIGINT NOT NULL,
	column_no INTEGER NOT NULL,
	column_name NVARCHAR(200) NOT NULL,
	tudf_place NVARCHAR(200) NOT NULL,
	compared INTEGER NOT NULL,
	matched INTEGER NOT NULL,
	both_empty INTEGER NOT NULL,
	spec_rules NVARCHAR(1000) NULL,
	mismatched INTEGER NOT NULL,
	PRIMARY KEY (request_id, column_no)
);
CREATE TABLE automn.reconciliation_mismatch (
	mismatch_id BIGINT NOT NULL IDENTITY,
	request_id BIGINT NOT NULL,
	form_row INTEGER NULL,
	column_name NVARCHAR(200) NOT NULL,
	form_value NVARCHAR(500) NULL,
	tudf_place NVARCHAR(200) NULL,
	tudf_value NVARCHAR(500) NULL,
	problem NVARCHAR(500) NULL,
	PRIMARY KEY (mismatch_id)
);
CREATE INDEX ix_reconciliation_mismatch_request ON automn.reconciliation_mismatch (request_id);
