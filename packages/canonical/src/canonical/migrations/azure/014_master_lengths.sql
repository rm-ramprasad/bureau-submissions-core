-- L2a demographics master: columns as wide as the snapshot columns they feed (2026-11-10). rarfincare's commercial
-- data holds two numbers in one phone field ('067-42303999,43137080', 21 characters) and the master's phone_number
-- allowed 20; the snapshot (c_*) columns allow 60. Run once in rarfinmisdb in SSMS (admin login), then
-- `ops.ps1 migrate --mark-applied 014_master_lengths`. No BEGIN/COMMIT (the runner wraps the file).
ALTER TABLE automn.dm_party_phone ALTER COLUMN phone_number NVARCHAR(60) NOT NULL;
ALTER TABLE automn.dm_party_address ALTER COLUMN pin_code NVARCHAR(20) NULL;
ALTER TABLE automn.dm_sanction ALTER COLUMN currency NVARCHAR(40) NULL;
ALTER TABLE automn.dm_security ALTER COLUMN currency NVARCHAR(40) NULL;
