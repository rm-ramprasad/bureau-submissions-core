-- Client configuration versions (2026-11-06, issue #16, step 4): every saved change to an NBFC's settings (bureau
-- profiles, rule policies, rule parameters, custom rules) appends the whole configuration as a new numbered version;
-- rows are only ever added. A request records the version that was current when a worker started it.
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 008_config_versions`; no
-- BEGIN/COMMIT (the runner wraps the file). The next `ops.ps1 migrate` records version 1 (the settings of today).
CREATE TABLE automn.config_version (
	version_id BIGINT NOT NULL IDENTITY,
	tenant_id NVARCHAR(30) NOT NULL,
	version_no INTEGER NOT NULL,
	created_at DATETIME2 NOT NULL,
	created_by NVARCHAR(100) NOT NULL,
	action NVARCHAR(50) NOT NULL,
	target NVARCHAR(200) NULL,
	reason NVARCHAR(500) NULL,
	changes NVARCHAR(max) NULL,
	document NVARCHAR(max) NOT NULL,
	PRIMARY KEY (version_id),
	CONSTRAINT uq_config_version_tenant_no UNIQUE (tenant_id, version_no)
);
ALTER TABLE automn.submission_request ADD config_version INTEGER NULL;
