-- L2a demographics master, fields the extract needs (2026-11-09, ADR 0002 PR 4): suit and collateral details of a
-- sanction, the party's bureau type (INDIAN_INDIVIDUAL, INDIAN_ENTITY, ...), phone area codes, the order (priority) of
-- contacts, and cheque dishonours. Run once in rarfinmisdb in SSMS (admin login), then
-- `ops.ps1 migrate --mark-applied 013_master_fields`. No BEGIN/COMMIT (the runner wraps the file).
ALTER TABLE automn.dm_party ADD
	party_type NVARCHAR(40) NULL;
ALTER TABLE automn.dm_sanction ADD
	suit_filed_status NVARCHAR(40) NULL,
	suit_reference NVARCHAR(50) NULL,
	suit_amount NUMERIC(20, 2) NULL,
	suit_date DATE NULL,
	collateral_value NUMERIC(20, 2) NULL,
	collateral_type NVARCHAR(40) NULL;
ALTER TABLE automn.dm_party_phone ADD
	area_code NVARCHAR(10) NULL,
	priority INTEGER NULL;
ALTER TABLE automn.dm_party_email ADD
	priority INTEGER NULL;
ALTER TABLE automn.dm_party_address ADD
	priority INTEGER NULL;

-- L2a cheque dishonours of a sanction: replaced as a whole for a sanction in an upload.
CREATE TABLE automn.dm_cheque_dishonour (
	sanction_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	dishonour_date DATE NULL,
	amount NUMERIC(20, 2) NULL,
	instrument_number NVARCHAR(50) NULL,
	dishonour_count INTEGER NULL,
	cheque_issue_date DATE NULL,
	reason NVARCHAR(40) NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, seq),
	CONSTRAINT fk_dm_cheque_dishonour FOREIGN KEY (sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);
