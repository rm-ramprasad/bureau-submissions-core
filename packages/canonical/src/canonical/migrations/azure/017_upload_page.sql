-- Uploads page (2026-11-10, ADR 0002 PR 7, owner: a separate Uploads page): an upload not tied to a request records the
-- as-of date of its data (its data-quality checks and the source views use it) and when it was loaded into the
-- customer tables, with a short summary. Run once in rarfinmisdb in SSMS (admin login), then
-- `ops.ps1 migrate --mark-applied 017_upload_page`. No BEGIN/COMMIT (the runner wraps the file).
ALTER TABLE automn.input_load ADD
	as_of_date DATE NULL,
	applied_at DATETIME2 NULL,
	apply_summary NVARCHAR(1000) NULL;
