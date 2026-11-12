-- Uploads in the background with visible progress (owner 2026-11-12): an upload is accepted at once and processed by
-- the worker; input_load.stage says where it is (RECEIVED, LOADING, CHECKING, SAVING, DONE) and stage_at since when.
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 021_upload_stage`.
-- No BEGIN/COMMIT (the runner wraps the file).
ALTER TABLE automn.input_load ADD
	stage NVARCHAR(20) NULL,
	stage_at DATETIME2 NULL;
