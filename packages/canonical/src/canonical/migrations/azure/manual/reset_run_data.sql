-- RESET RUN DATA (owner 2026-11-10: clear all past runs; one-off, run by hand; never run by deploy or migrate).
-- Deletes every past upload, request and result in automn, for all NBFCs:
--   staging (stg_*), upload issues; upload headers (input_load);
--   customer tables (dm_*);
--   requests, snapshots (c_*), validation results, adjustments, reconciliation, output files (out_*).
-- KEEPS settings and history of who did what: tenant, app_user, audit_event, client_setting, config_version,
--   bureau_profile, ref_*, validation_rule / _parameter / _policy, schema_migration. The views are not touched.
-- NOT deleted here: the workbook and bureau files kept in blob storage (delete them in the storage account if wanted).
-- After this: requests and their files are gone from the pages; every account must be uploaded again.
--
-- How to run (SSMS, admin login, database rarfinmisdb):
--   1. Run as is: it only SHOWS the row counts (@confirm = 0).
--   2. Set @confirm = 1 and run again: deletes in one transaction (all or nothing), then shows the counts (all 0).

DECLARE @confirm BIT = 0;   -- 1 = delete

SET XACT_ABORT ON;
SET NOCOUNT ON;

DECLARE @tables TABLE (seq INT IDENTITY, name SYSNAME);
INSERT INTO @tables (name) VALUES
    -- snapshot children first, then snapshots
    ('validation_finding'), ('validation_summary'), ('record_outcome'), ('snapshot_code_translation'),
    ('snapshot_adjustment_effect'), ('snapshot_adjustment'),
    ('c_account'), ('c_account_history'), ('c_account_holder'), ('c_borrower_group'), ('c_cheque_dishonour'),
    ('c_extension_attribute'), ('c_guarantor'), ('c_party'), ('c_party_address'), ('c_party_email'),
    ('c_party_identifier'), ('c_party_phone'), ('c_party_relative'), ('c_relationship'), ('c_security'),
    ('out_submission'), ('out_file'),
    ('reconciliation_mismatch'), ('reconciliation_column'), ('reconciliation_summary'), ('reconciliation_check'),
    ('extract_snapshot'), ('submission_adjustment'), ('submission_request'),
    -- customer tables
    ('dm_change_log'), ('dm_sanction_party'), ('dm_security'), ('dm_cheque_dishonour'), ('dm_party_relative'),
    ('dm_party_address'), ('dm_party_email'), ('dm_party_identifier'), ('dm_party_phone'),
    ('dm_sanction'), ('dm_party'), ('dm_group'),
    -- staging, then the upload headers
    ('stg_consumer'), ('stg_commercial_bs'), ('stg_commercial_as'), ('stg_commercial_rs'), ('stg_commercial_gs'),
    ('stg_commercial_ss'), ('stg_commercial_sanction'), ('stg_commercial_nesl_security'), ('stg_commercial_cd'),
    ('stg_lms_position'), ('stg_mfi_loan'), ('stg_mfi_group'),
    ('input_issue'), ('input_load');

DECLARE @counts TABLE (name SYSNAME, rows_before BIGINT, rows_after BIGINT);
DECLARE @i INT = 1, @n INT = (SELECT COUNT(*) FROM @tables), @t SYSNAME, @sql NVARCHAR(400), @c BIGINT;

WHILE @i <= @n
BEGIN
    SELECT @t = name FROM @tables WHERE seq = @i;
    IF OBJECT_ID(N'automn.' + @t, N'U') IS NOT NULL
    BEGIN
        SET @sql = N'SELECT @c = COUNT_BIG(*) FROM automn.' + QUOTENAME(@t);
        EXEC sp_executesql @sql, N'@c BIGINT OUTPUT', @c = @c OUTPUT;
        INSERT INTO @counts (name, rows_before) VALUES (@t, @c);
    END
    SET @i += 1;
END

IF @confirm = 1
BEGIN
    BEGIN TRANSACTION;
    SET @i = 1;
    WHILE @i <= @n
    BEGIN
        SELECT @t = name FROM @tables WHERE seq = @i;
        IF OBJECT_ID(N'automn.' + @t, N'U') IS NOT NULL
        BEGIN
            SET @sql = N'DELETE FROM automn.' + QUOTENAME(@t);
            EXEC sp_executesql @sql;
        END
        SET @i += 1;
    END
    COMMIT TRANSACTION;

    UPDATE x SET rows_after = 0 FROM @counts x;  -- refreshed below
    DECLARE c CURSOR LOCAL FAST_FORWARD FOR SELECT name FROM @counts;
    OPEN c; FETCH NEXT FROM c INTO @t;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        SET @sql = N'SELECT @c = COUNT_BIG(*) FROM automn.' + QUOTENAME(@t);
        EXEC sp_executesql @sql, N'@c BIGINT OUTPUT', @c = @c OUTPUT;
        UPDATE @counts SET rows_after = @c WHERE name = @t;
        FETCH NEXT FROM c INTO @t;
    END
    CLOSE c; DEALLOCATE c;
END

SELECT name AS table_name, rows_before, rows_after,
       CASE WHEN @confirm = 1 THEN 'deleted' ELSE 'preview only (set @confirm = 1 to delete)' END AS action
FROM @counts ORDER BY name;
