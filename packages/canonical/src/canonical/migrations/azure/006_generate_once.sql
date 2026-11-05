-- Files are generated once per validation (2026-10-30). generate_key = "<snapshot_id>|<bureau>" on a generation row
-- while it is pending, running or succeeded (cleared when it fails or is cancelled, so it can be retried). The unique
-- index makes the database refuse a second generation of the same validation for the same bureau, even when two
-- clicks arrive together. Existing rows keep NULL (earlier duplicates stay as they are). Run once in rarfinmisdb;
-- no BEGIN/COMMIT (the runner wraps the file in one transaction).
ALTER TABLE automn.submission_request ADD generate_key NVARCHAR(60) NULL;
CREATE UNIQUE INDEX uq_submission_request_generate_key ON automn.submission_request (generate_key) WHERE generate_key IS NOT NULL;
