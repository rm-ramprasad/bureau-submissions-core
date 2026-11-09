-- L4 output files (2026-11-08, ADR 0002): a fingerprint (SHA-256, size, records) per generated file, and the
-- "Mark as submitted" record per generated bureau file. DDL as in docs/adr/0002-layers-ddl.sql.
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 011_output_files`; no
-- BEGIN/COMMIT (the runner wraps the file).

CREATE TABLE automn.out_file (
	file_id BIGINT NOT NULL IDENTITY,
	request_id BIGINT NOT NULL,                    -- the GENERATE request (automn.submission_request)
	snapshot_id BIGINT NOT NULL,                   -- L3: the snapshot it was generated from
	bureau_code NVARCHAR(20) NOT NULL,
	kind NVARCHAR(20) NOT NULL,                    -- BUREAU_FILE, BUREAU_EXCEL, REVIEW_EXCEL, EXCEPTIONS
	file_name NVARCHAR(255) NOT NULL,
	file_uri NVARCHAR(500) NOT NULL,
	sha256 CHAR(64) NOT NULL,                      -- fingerprint: proves the stored file is the one generated
	size_bytes BIGINT NOT NULL,
	record_count INTEGER NULL,
	created_at DATETIME2 NOT NULL,
	PRIMARY KEY (file_id)
);
CREATE INDEX ix_out_file_request ON automn.out_file (request_id);

CREATE TABLE automn.out_submission (
	request_id BIGINT NOT NULL,                    -- the GENERATE request whose bureau file was sent
	submitted_at DATETIME2 NOT NULL,
	submitted_by NVARCHAR(100) NOT NULL,
	bureau_code NVARCHAR(20) NOT NULL,
	ack_reference NVARCHAR(100) NULL,              -- the bureau's acknowledgement, if any
	status NVARCHAR(20) NOT NULL,                  -- SUBMITTED, ACCEPTED, REJECTED (rejections: requirements section 11)
	note NVARCHAR(500) NULL,
	PRIMARY KEY (request_id)
);
