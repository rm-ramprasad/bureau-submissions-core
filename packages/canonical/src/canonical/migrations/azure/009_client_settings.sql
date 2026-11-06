-- Client settings without a table of their own (2026-11-06, issue #18, admin page part 1): one row per NBFC and
-- setting, the value as JSON (first: reporting_cycles, the cycles offered for new requests). No row = the default.
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 009_client_settings`; no
-- BEGIN/COMMIT (the runner wraps the file).
CREATE TABLE automn.client_setting (
	tenant_id NVARCHAR(30) NOT NULL,
	name NVARCHAR(50) NOT NULL,
	value NVARCHAR(max) NOT NULL,
	updated_by NVARCHAR(100) NOT NULL,
	updated_at DATETIME2 NOT NULL,
	PRIMARY KEY (tenant_id, name)
);
