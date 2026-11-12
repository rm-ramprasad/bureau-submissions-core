-- The sanction reference key between all customer tables (owner 2026-11-11): sanction_ref_key = the sanction
-- reference number in upper case, letters and digits only (F012/2025-26 -> F012202526; the app computes it as
-- automn.fn_ref_key does). Added to dm_sanction (unique per NBFC, replacing the unique reference number) and to every
-- table that belongs to a sanction, accounts included; existing rows are filled (punctuation / - space . _ \ , removed).
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 020_sanction_ref_key`.
-- No BEGIN/COMMIT (the runner wraps the file).

ALTER TABLE automn.dm_sanction ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_holder ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_identifier ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_phone ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_email ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_address ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_guarantor ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_relationship ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_security ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_cheque ADD sanction_ref_key NVARCHAR(100) NULL;
ALTER TABLE automn.dm_sanction_account ADD sanction_ref_key NVARCHAR(100) NULL;
UPDATE automn.dm_sanction SET sanction_ref_key = UPPER(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(sanction_reference_number, '/', ''), '-', ''), ' ', ''), '.', ''), '_', ''), '\', ''), ',', ''));
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_holder c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_identifier c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_phone c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_email c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_address c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_guarantor c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_relationship c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_security c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_cheque c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
UPDATE c SET sanction_ref_key = s.sanction_ref_key FROM automn.dm_sanction_account c JOIN automn.dm_sanction s ON s.sanction_id = c.sanction_id;
ALTER TABLE automn.dm_sanction ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_holder ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_identifier ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_phone ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_email ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_address ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_guarantor ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_relationship ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_security ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_cheque ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction_account ALTER COLUMN sanction_ref_key NVARCHAR(100) NOT NULL;
ALTER TABLE automn.dm_sanction DROP CONSTRAINT uq_dm_sanction_ref;
ALTER TABLE automn.dm_sanction ADD CONSTRAINT uq_dm_sanction_ref_key UNIQUE (tenant_id, sanction_ref_key);
CREATE INDEX ix_dm_sanction_account_ref_key ON automn.dm_sanction_account (tenant_id, sanction_ref_key);
