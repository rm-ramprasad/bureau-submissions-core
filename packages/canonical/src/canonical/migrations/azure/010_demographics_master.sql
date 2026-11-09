-- L2a demographics master (2026-11-08, ADR 0002, PR 1): current customers, sanctions, party roles, securities and
-- contacts, plus a change log. Nothing reads or writes these tables yet. DDL as in docs/adr/0002-layers-ddl.sql.
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 010_demographics_master`; no
-- BEGIN/COMMIT (the runner wraps the file).

-- L2a party: one per real person or company, key = the client's customer ID.
CREATE TABLE automn.dm_party (
	party_id BIGINT NOT NULL IDENTITY,
	tenant_id NVARCHAR(30) NOT NULL,
	customer_id NVARCHAR(50) NOT NULL,             -- L2a key (the client's customer ID)
	party_kind NVARCHAR(20) NOT NULL,              -- INDIVIDUAL / ENTITY
	full_name NVARCHAR(200) NULL,
	name_prefix NVARCHAR(20) NULL,
	short_name NVARCHAR(20) NULL,
	birth_date DATE NULL,                          -- individuals
	gender NVARCHAR(40) NULL,
	occupation NVARCHAR(40) NULL,
	income NUMERIC(20, 2) NULL,
	income_basis NVARCHAR(40) NULL,
	income_period NVARCHAR(40) NULL,
	legal_constitution NVARCHAR(40) NULL,          -- entities
	business_category NVARCHAR(40) NULL,
	industry_type NVARCHAR(40) NULL,
	incorporation_date DATE NULL,
	activity_class_1 NVARCHAR(100) NULL,
	activity_class_2 NVARCHAR(100) NULL,
	activity_class_3 NVARCHAR(100) NULL,
	hsn_code NVARCHAR(20) NULL,
	sales_amount NUMERIC(20, 2) NULL,
	sales_year DATE NULL,
	employee_count INTEGER NULL,
	credit_rating NVARCHAR(20) NULL,
	rating_agency NVARCHAR(40) NULL,
	rating_date DATE NULL,
	rating_expiry_date DATE NULL,
	is_active BIT NOT NULL DEFAULT 1,
	first_load_id BIGINT NOT NULL,                 -- L1 upload that created it
	last_load_id BIGINT NOT NULL,                  -- L1 upload that last wrote it
	updated_at DATETIME2 NOT NULL,
	PRIMARY KEY (party_id),
	CONSTRAINT uq_dm_party_customer UNIQUE (tenant_id, customer_id)
);

-- L2a contact lists of a party: replaced as a whole for a customer in an upload (corrections; latest is right).
CREATE TABLE automn.dm_party_identifier (
	party_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,                          -- order within the customer's list
	id_type NVARCHAR(40) NOT NULL,                 -- PAN, AADHAAR, PASSPORT, VOTER_ID, CIN, DIN, GSTIN, CKYC, UDYAM, ...
	id_number NVARCHAR(50) NOT NULL,
	issue_date DATE NULL,
	expiry_date DATE NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (party_id, seq),
	CONSTRAINT fk_dm_party_identifier FOREIGN KEY (party_id) REFERENCES automn.dm_party (party_id)
);
CREATE TABLE automn.dm_party_phone (
	party_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	phone_number NVARCHAR(20) NOT NULL,
	extension NVARCHAR(10) NULL,
	phone_type NVARCHAR(40) NULL,                  -- MOBILE, HOME, OFFICE, FAX, ...
	load_id BIGINT NOT NULL,
	PRIMARY KEY (party_id, seq),
	CONSTRAINT fk_dm_party_phone FOREIGN KEY (party_id) REFERENCES automn.dm_party (party_id)
);
CREATE TABLE automn.dm_party_email (
	party_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	email NVARCHAR(200) NOT NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (party_id, seq),
	CONSTRAINT fk_dm_party_email FOREIGN KEY (party_id) REFERENCES automn.dm_party (party_id)
);
CREATE TABLE automn.dm_party_address (
	party_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	address_line_1 NVARCHAR(200) NULL,
	address_line_2 NVARCHAR(200) NULL,
	address_line_3 NVARCHAR(200) NULL,
	address_line_4 NVARCHAR(200) NULL,
	address_line_5 NVARCHAR(200) NULL,
	city NVARCHAR(100) NULL,
	district NVARCHAR(100) NULL,
	state_code NVARCHAR(40) NULL,
	pin_code NVARCHAR(10) NULL,
	country NVARCHAR(40) NULL,
	address_category NVARCHAR(40) NULL,            -- PERMANENT, RESIDENCE, OFFICE, REGISTERED, ...
	residence_code NVARCHAR(40) NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (party_id, seq),
	CONSTRAINT fk_dm_party_address FOREIGN KEY (party_id) REFERENCES automn.dm_party (party_id)
);

-- L2a sanction / account: the static details; key = account number (+ sanction number where one account has several).
-- Daily figures are NOT here: they are L2b. Where L2b also has a field, L2b (the LMS) wins.
CREATE TABLE automn.dm_sanction (
	sanction_id BIGINT NOT NULL IDENTITY,
	tenant_id NVARCHAR(30) NOT NULL,
	account_number NVARCHAR(50) NOT NULL,          -- L2a key, links to L2b
	sanction_number NVARCHAR(50) NOT NULL DEFAULT '',  -- '' when the account has one sanction (SQL Server: no NULL in keys)
	account_type NVARCHAR(40) NULL,
	date_opened DATE NULL,
	sanctioned_amount NUMERIC(20, 2) NULL,
	currency NVARCHAR(10) NULL,
	rate_of_interest NUMERIC(9, 4) NULL,
	repayment_tenure INTEGER NULL,
	emi_amount NUMERIC(20, 2) NULL,
	payment_frequency NVARCHAR(40) NULL,
	maturity_date DATE NULL,
	renewal_date DATE NULL,
	branch_code NVARCHAR(20) NULL,
	previous_branch_code NVARCHAR(20) NULL,
	previous_account_number NVARCHAR(50) NULL,
	old_member_code NVARCHAR(20) NULL,
	old_member_short_name NVARCHAR(20) NULL,
	old_account_number NVARCHAR(50) NULL,
	old_account_type NVARCHAR(40) NULL,
	old_ownership_indicator NVARCHAR(40) NULL,
	restructured_notional_amount NUMERIC(20, 2) NULL,
	restructuring_reason NVARCHAR(40) NULL,
	security_coverage NVARCHAR(40) NULL,
	guarantee_coverage NVARCHAR(40) NULL,
	other_lenders NVARCHAR(200) NULL,
	dispute_id NVARCHAR(50) NULL,
	remark NVARCHAR(500) NULL,
	is_active BIT NOT NULL DEFAULT 1,
	first_load_id BIGINT NOT NULL,
	last_load_id BIGINT NOT NULL,
	updated_at DATETIME2 NOT NULL,
	PRIMARY KEY (sanction_id),
	CONSTRAINT uq_dm_sanction_key UNIQUE (tenant_id, account_number, sanction_number)
);

-- L2a who is on a sanction, in which role: replaces the separate guarantor / relationship shapes (party + role).
CREATE TABLE automn.dm_sanction_party (
	sanction_id BIGINT NOT NULL,
	party_id BIGINT NOT NULL,
	role NVARCHAR(30) NOT NULL,                    -- BORROWER, CO_BORROWER, GUARANTOR, DIRECTOR, PARTNER, PROPRIETOR, ...
	ownership_indicator NVARCHAR(40) NULL,         -- consumer: individual / joint / guarantor ...
	relationship_type NVARCHAR(40) NULL,           -- commercial: the related party's relation to the borrower
	control_percentage NUMERIC(9, 4) NULL,
	guarantee_invocation_date DATE NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, party_id, role),
	CONSTRAINT fk_dm_sanction_party_s FOREIGN KEY (sanction_id) REFERENCES automn.dm_sanction (sanction_id),
	CONSTRAINT fk_dm_sanction_party_p FOREIGN KEY (party_id) REFERENCES automn.dm_party (party_id)
);
CREATE INDEX ix_dm_sanction_party_party ON automn.dm_sanction_party (party_id);

-- L2a securities of a sanction.
CREATE TABLE automn.dm_security (
	sanction_id BIGINT NOT NULL,
	security_ref NVARCHAR(50) NOT NULL,            -- the client's security / collateral identifier
	security_type NVARCHAR(40) NULL,
	security_classification NVARCHAR(40) NULL,
	security_value NUMERIC(20, 2) NULL,
	currency NVARCHAR(10) NULL,
	valuation_date DATE NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, security_ref),
	CONSTRAINT fk_dm_security FOREIGN KEY (sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

-- L2a change log: one row per value an upload overwrote ("what changed for this customer").
CREATE TABLE automn.dm_change_log (
	change_id BIGINT NOT NULL IDENTITY,
	tenant_id NVARCHAR(30) NOT NULL,
	entity NVARCHAR(40) NOT NULL,                  -- dm_party, dm_party_phone, dm_sanction, ...
	entity_key NVARCHAR(150) NOT NULL,             -- customer ID, account number / sanction number, ...
	field NVARCHAR(60) NOT NULL,                   -- column, or '*list*' when a list was replaced
	old_value NVARCHAR(1000) NULL,
	new_value NVARCHAR(1000) NULL,
	load_id BIGINT NOT NULL,                       -- the L1 upload that made the change
	changed_at DATETIME2 NOT NULL,
	PRIMARY KEY (change_id)
);
CREATE INDEX ix_dm_change_log_key ON automn.dm_change_log (tenant_id, entity, entity_key, changed_at);
