-- Filtered unique keys (2026-10-29). SQL Server lets a UNIQUE constraint hold only ONE NULL, so
-- the second finished request (active_key set to NULL) failed to save its result and stayed RUNNING. The same would
-- hit a second user without an e-mail. Replaces both constraints (system-named, so looked up) with unique indexes
-- that ignore NULLs. Run once in rarfinmisdb; no BEGIN/COMMIT (the runner wraps the file in one transaction).
-- replaces: UNIQUE (active_key)
-- replaces: UNIQUE (email)
DECLARE @c1 SYSNAME = (SELECT kc.name FROM sys.key_constraints kc JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id WHERE kc.type = 'UQ' AND kc.parent_object_id = OBJECT_ID('automn.submission_request') AND c.name = 'active_key') IF @c1 IS NOT NULL EXEC ('ALTER TABLE automn.submission_request DROP CONSTRAINT ' + @c1);
DECLARE @c2 SYSNAME = (SELECT kc.name FROM sys.key_constraints kc JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id WHERE kc.type = 'UQ' AND kc.parent_object_id = OBJECT_ID('automn.app_user') AND c.name = 'email') IF @c2 IS NOT NULL EXEC ('ALTER TABLE automn.app_user DROP CONSTRAINT ' + @c2);
CREATE UNIQUE INDEX uq_submission_request_active_key ON automn.submission_request (active_key) WHERE active_key IS NOT NULL;
CREATE UNIQUE INDEX uq_app_user_email ON automn.app_user (email) WHERE email IS NOT NULL;
