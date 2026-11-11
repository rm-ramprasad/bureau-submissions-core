-- Customer tables keyed by the SANCTION REFERENCE NUMBER (2026-11-11, owner: one data model for consumer and
-- commercial; one borrower per sanction with its guarantors (GS), related parties (RS), addresses (AS), securities
-- (SS); one or more loan accounts per sanction, linked to the LMS by account number). Replaces the party-based tables
-- of migrations 010/013/014/015/016 (dm_party and its lists, dm_sanction_party, dm_security, dm_cheque_dishonour,
-- dm_party_relative, dm_group); their data is dropped (the owner reset all run data on 2026-11-10): upload consumer
-- and commercial again on the Uploads page afterwards. MFI is not on the customer tables (later).
-- Generated from the code (canonical.master.tables). Run once in rarfinmisdb in SSMS (admin login), then
-- `ops.ps1 migrate --mark-applied 019_sanction_master`. No BEGIN/COMMIT (the runner wraps the file).

DROP TABLE automn.dm_sanction_party;
DROP TABLE automn.dm_security;
DROP TABLE automn.dm_cheque_dishonour;
DROP TABLE automn.dm_party_identifier;
DROP TABLE automn.dm_party_phone;
DROP TABLE automn.dm_party_email;
DROP TABLE automn.dm_party_address;
DROP TABLE automn.dm_party_relative;
DROP TABLE automn.dm_sanction;
DROP TABLE automn.dm_party;
DROP TABLE automn.dm_group;
DELETE FROM automn.dm_change_log;  -- its keys were customer IDs / account numbers of the old tables

CREATE TABLE automn.dm_sanction (
	sanction_id BIGINT NOT NULL IDENTITY,
	tenant_id NVARCHAR(30) NOT NULL,
	sanction_reference_number NVARCHAR(100) NOT NULL,
	party_kind NVARCHAR(20) NOT NULL,
	ownership_indicator NVARCHAR(40) NULL,
	full_name NVARCHAR(200) NULL,
	name_prefix NVARCHAR(20) NULL,
	short_name NVARCHAR(20) NULL,
	birth_date DATETIME NULL,
	gender NVARCHAR(40) NULL,
	occupation NVARCHAR(40) NULL,
	income NUMERIC(20, 2) NULL,
	income_basis NVARCHAR(40) NULL,
	income_period NVARCHAR(40) NULL,
	legal_constitution NVARCHAR(40) NULL,
	business_category NVARCHAR(40) NULL,
	industry_type NVARCHAR(40) NULL,
	incorporation_date DATETIME NULL,
	activity_class_1 NVARCHAR(100) NULL,
	activity_class_2 NVARCHAR(100) NULL,
	activity_class_3 NVARCHAR(100) NULL,
	hsn_code NVARCHAR(20) NULL,
	sales_amount NUMERIC(20, 2) NULL,
	sales_year DATETIME NULL,
	employee_count INTEGER NULL,
	credit_rating NVARCHAR(20) NULL,
	rating_agency NVARCHAR(40) NULL,
	rating_date DATETIME NULL,
	rating_expiry_date DATETIME NULL,
	party_type NVARCHAR(40) NULL,
	is_active BIT NOT NULL DEFAULT 1,
	first_load_id BIGINT NOT NULL,
	last_load_id BIGINT NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (sanction_id),
	CONSTRAINT uq_dm_sanction_ref UNIQUE (tenant_id, sanction_reference_number)
);

CREATE TABLE automn.dm_sanction_holder (
	sanction_id BIGINT NOT NULL,
	holder_seq INTEGER NOT NULL,
	role NVARCHAR(30) NOT NULL,
	party_kind NVARCHAR(20) NOT NULL,
	ownership_indicator NVARCHAR(40) NULL,
	full_name NVARCHAR(200) NULL,
	name_prefix NVARCHAR(20) NULL,
	short_name NVARCHAR(20) NULL,
	birth_date DATETIME NULL,
	gender NVARCHAR(40) NULL,
	occupation NVARCHAR(40) NULL,
	income NUMERIC(20, 2) NULL,
	income_basis NVARCHAR(40) NULL,
	income_period NVARCHAR(40) NULL,
	legal_constitution NVARCHAR(40) NULL,
	business_category NVARCHAR(40) NULL,
	industry_type NVARCHAR(40) NULL,
	incorporation_date DATETIME NULL,
	activity_class_1 NVARCHAR(100) NULL,
	activity_class_2 NVARCHAR(100) NULL,
	activity_class_3 NVARCHAR(100) NULL,
	hsn_code NVARCHAR(20) NULL,
	sales_amount NUMERIC(20, 2) NULL,
	sales_year DATETIME NULL,
	employee_count INTEGER NULL,
	credit_rating NVARCHAR(20) NULL,
	rating_agency NVARCHAR(40) NULL,
	rating_date DATETIME NULL,
	rating_expiry_date DATETIME NULL,
	party_type NVARCHAR(40) NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, holder_seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_identifier (
	sanction_id BIGINT NOT NULL,
	holder_seq INTEGER NOT NULL,
	seq INTEGER NOT NULL,
	id_type NVARCHAR(40) NOT NULL,
	id_number NVARCHAR(50) NOT NULL,
	issue_date DATETIME NULL,
	expiry_date DATETIME NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, holder_seq, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_phone (
	sanction_id BIGINT NOT NULL,
	holder_seq INTEGER NOT NULL,
	seq INTEGER NOT NULL,
	phone_number NVARCHAR(60) NOT NULL,
	extension NVARCHAR(10) NULL,
	phone_type NVARCHAR(40) NULL,
	area_code NVARCHAR(10) NULL,
	priority INTEGER NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, holder_seq, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_email (
	sanction_id BIGINT NOT NULL,
	holder_seq INTEGER NOT NULL,
	seq INTEGER NOT NULL,
	email NVARCHAR(200) NOT NULL,
	priority INTEGER NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, holder_seq, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_address (
	sanction_id BIGINT NOT NULL,
	holder_seq INTEGER NOT NULL,
	seq INTEGER NOT NULL,
	address_line_1 NVARCHAR(200) NULL,
	address_line_2 NVARCHAR(200) NULL,
	address_line_3 NVARCHAR(200) NULL,
	address_line_4 NVARCHAR(200) NULL,
	address_line_5 NVARCHAR(200) NULL,
	city NVARCHAR(100) NULL,
	district NVARCHAR(100) NULL,
	state_code NVARCHAR(40) NULL,
	pin_code NVARCHAR(20) NULL,
	country NVARCHAR(40) NULL,
	address_category NVARCHAR(40) NULL,
	residence_code NVARCHAR(40) NULL,
	priority INTEGER NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, holder_seq, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_guarantor (
	sanction_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	related_key NVARCHAR(50) NULL,
	related_type NVARCHAR(40) NULL,
	full_name NVARCHAR(150) NULL,
	name_prefix NVARCHAR(15) NULL,
	business_category NVARCHAR(40) NULL,
	industry_type NVARCHAR(40) NULL,
	gender NVARCHAR(40) NULL,
	birth_date DATETIME NULL,
	incorporation_date DATETIME NULL,
	pan NVARCHAR(20) NULL,
	voter_id NVARCHAR(30) NULL,
	passport NVARCHAR(30) NULL,
	driving_licence NVARCHAR(30) NULL,
	aadhaar NVARCHAR(30) NULL,
	ration_card NVARCHAR(30) NULL,
	company_registration NVARCHAR(30) NULL,
	cin NVARCHAR(30) NULL,
	din NVARCHAR(12) NULL,
	tin NVARCHAR(30) NULL,
	service_tax NVARCHAR(30) NULL,
	ckyc NVARCHAR(30) NULL,
	udyam NVARCHAR(30) NULL,
	address_line_1 NVARCHAR(200) NULL,
	address_line_2 NVARCHAR(200) NULL,
	address_line_3 NVARCHAR(200) NULL,
	city NVARCHAR(40) NULL,
	district NVARCHAR(40) NULL,
	state_code NVARCHAR(40) NULL,
	pin_code NVARCHAR(20) NULL,
	country NVARCHAR(40) NULL,
	mobile NVARCHAR(60) NULL,
	telephone NVARCHAR(60) NULL,
	telephone_area_code NVARCHAR(6) NULL,
	fax NVARCHAR(60) NULL,
	fax_area_code NVARCHAR(6) NULL,
	guarantee_invocation_date DATETIME NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_relationship (
	sanction_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	relationship_type NVARCHAR(40) NULL,
	control_percentage NUMERIC(5, 2) NULL,
	related_key NVARCHAR(50) NULL,
	related_type NVARCHAR(40) NULL,
	full_name NVARCHAR(150) NULL,
	name_prefix NVARCHAR(15) NULL,
	business_category NVARCHAR(40) NULL,
	industry_type NVARCHAR(40) NULL,
	gender NVARCHAR(40) NULL,
	birth_date DATETIME NULL,
	incorporation_date DATETIME NULL,
	pan NVARCHAR(20) NULL,
	voter_id NVARCHAR(30) NULL,
	passport NVARCHAR(30) NULL,
	driving_licence NVARCHAR(30) NULL,
	aadhaar NVARCHAR(30) NULL,
	ration_card NVARCHAR(30) NULL,
	company_registration NVARCHAR(30) NULL,
	cin NVARCHAR(30) NULL,
	din NVARCHAR(12) NULL,
	tin NVARCHAR(30) NULL,
	service_tax NVARCHAR(30) NULL,
	ckyc NVARCHAR(30) NULL,
	udyam NVARCHAR(30) NULL,
	address_line_1 NVARCHAR(200) NULL,
	address_line_2 NVARCHAR(200) NULL,
	address_line_3 NVARCHAR(200) NULL,
	city NVARCHAR(40) NULL,
	district NVARCHAR(40) NULL,
	state_code NVARCHAR(40) NULL,
	pin_code NVARCHAR(20) NULL,
	country NVARCHAR(40) NULL,
	mobile NVARCHAR(60) NULL,
	telephone NVARCHAR(60) NULL,
	telephone_area_code NVARCHAR(6) NULL,
	fax NVARCHAR(60) NULL,
	fax_area_code NVARCHAR(6) NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_security (
	sanction_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	security_value NUMERIC(20, 2) NULL,
	currency NVARCHAR(40) NULL,
	security_type NVARCHAR(40) NULL,
	security_classification NVARCHAR(40) NULL,
	valuation_date DATETIME NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_cheque (
	sanction_id BIGINT NOT NULL,
	seq INTEGER NOT NULL,
	dishonour_date DATETIME NULL,
	amount NUMERIC(20, 2) NULL,
	instrument_number NVARCHAR(30) NULL,
	dishonour_count INTEGER NULL,
	cheque_issue_date DATETIME NULL,
	reason NVARCHAR(40) NULL,
	load_id BIGINT NOT NULL,
	PRIMARY KEY (sanction_id, seq),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE TABLE automn.dm_sanction_account (
	account_id BIGINT NOT NULL IDENTITY,
	tenant_id NVARCHAR(30) NOT NULL,
	account_number NVARCHAR(100) NOT NULL,
	sanction_id BIGINT NOT NULL,
	account_type NVARCHAR(40) NULL,
	date_opened DATETIME NULL,
	sanctioned_amount NUMERIC(20, 2) NULL,
	currency NVARCHAR(40) NULL,
	rate_of_interest NUMERIC(9, 4) NULL,
	repayment_tenure INTEGER NULL,
	emi_amount NUMERIC(20, 2) NULL,
	payment_frequency NVARCHAR(40) NULL,
	maturity_date DATETIME NULL,
	renewal_date DATETIME NULL,
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
	suit_filed_status NVARCHAR(40) NULL,
	suit_reference NVARCHAR(50) NULL,
	suit_amount NUMERIC(20, 2) NULL,
	suit_date DATETIME NULL,
	collateral_value NUMERIC(20, 2) NULL,
	collateral_type NVARCHAR(40) NULL,
	actual_payment_amount NUMERIC(20, 2) NULL,
	is_active BIT NOT NULL DEFAULT 1,
	first_load_id BIGINT NOT NULL,
	last_load_id BIGINT NOT NULL,
	updated_at DATETIME NOT NULL,
	PRIMARY KEY (account_id),
	CONSTRAINT uq_dm_sanction_account UNIQUE (tenant_id, account_number),
	FOREIGN KEY(sanction_id) REFERENCES automn.dm_sanction (sanction_id)
);

CREATE INDEX ix_dm_sanction_account_sanction ON automn.dm_sanction_account (sanction_id);

