-- L2a demographics master: the workbook's actual payment amount on a sanction (2026-11-10, parity report). Today's
-- consumer view takes the LMS value and, when the LMS has none, the workbook's; the master must hold the workbook's
-- so the new path does the same (the LMS still wins). Run once in rarfinmisdb in SSMS (admin login), then
-- `ops.ps1 migrate --mark-applied 015_sanction_payment_fallback`. No BEGIN/COMMIT (the runner wraps the file).
ALTER TABLE automn.dm_sanction ADD
	actual_payment_amount NUMERIC(20, 2) NULL;
