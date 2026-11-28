-- Upload checks on the Admin page (issue #19 part 2, owner 2026-11-27): an NBFC's settings for the product's upload
-- checks (canonical/types/<type>/input_checks.yaml): a check switched off, or its severity changed between warn and
-- block. NULL = the check's own value. Checks that protect the bureau file are locked and never stored here.
-- Run in SSMS from the release's migrations.zip (it records itself). No BEGIN/COMMIT (the runner wraps the file).
CREATE TABLE automn.input_check_policy (
	tenant_id NVARCHAR(30) NOT NULL,
	submission_type NVARCHAR(20) NOT NULL,
	check_id NVARCHAR(60) NOT NULL,
	is_enabled BIT NULL,
	severity NVARCHAR(10) NULL,
	reason NVARCHAR(500) NOT NULL,
	updated_by NVARCHAR(100) NOT NULL,
	updated_at DATETIME2 NOT NULL,
	CONSTRAINT pk_input_check_policy PRIMARY KEY (tenant_id, submission_type, check_id)
);
