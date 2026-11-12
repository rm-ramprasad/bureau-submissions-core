-- Upload details (owner 2026-11-12): what the save into the customer tables left out or noted, one row per item
-- (e.g. an account whose sanction has no borrower row), so the upload's details page can list and export them all.
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 022_upload_save_items`.
-- No BEGIN/COMMIT (the runner wraps the file).
CREATE TABLE automn.input_save_item (
	item_id BIGINT NOT NULL IDENTITY,
	load_id BIGINT NOT NULL,
	code NVARCHAR(40) NOT NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	account_number NVARCHAR(100) NULL,
	message NVARCHAR(1000) NOT NULL,
	PRIMARY KEY (item_id),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);
CREATE INDEX ix_input_save_item_load ON automn.input_save_item (load_id);
