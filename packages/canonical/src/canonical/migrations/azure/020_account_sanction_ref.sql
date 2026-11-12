-- dm_sanction_account gets its sanction's reference number as a column (owner 2026-11-11), next to sanction_id, so
-- an account's sanction can be read without a join. Existing rows are filled from dm_sanction. Run once in rarfinmisdb
-- in SSMS (admin login), then `ops.ps1 migrate --mark-applied 020_account_sanction_ref`. No BEGIN/COMMIT.
ALTER TABLE automn.dm_sanction_account ADD
	sanction_reference_number NVARCHAR(100) NULL;
UPDATE a SET sanction_reference_number = s.sanction_reference_number
FROM automn.dm_sanction_account a
JOIN automn.dm_sanction s ON s.sanction_id = a.sanction_id;
ALTER TABLE automn.dm_sanction_account ALTER COLUMN sanction_reference_number NVARCHAR(100) NOT NULL;
