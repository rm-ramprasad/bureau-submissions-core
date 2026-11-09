-- =====================================================================================================================
-- ADR 2 (docs/adr/0002-data-load-and-adapters.md): proposed tables per layer, SQL Server (schema automn).
-- PROPOSED, NOT A MIGRATION: do not run. Each table becomes a numbered migration (packages/canonical/.../azure/) in
-- the order of work, reviewed in its own pull request; the owner runs it in SSMS as usual.
--
-- Naming: the prefix says the layer.
--   L1  Staging                stg_*      (exists; one table per source template; append only)
--   L2a Demographics master    dm_*       (new; current state; overwrite by key)
--   L2b Account position       vw_pos_*   (new; a VIEW, no copy; over the client's LMS view or L1 staging)
--   L3  Submission snapshot    c_*        (exists, unchanged; frozen per format x as-of date x validation run)
--   L4  Output files           out_*      (new; one row per generated file; kept forever)
-- Retention: nothing is deleted in any layer (owner, 2026-11-08; deletion revisited later).
-- Columns follow the canonical model (bureau_core/model.yaml); "code" columns hold canonical values, not file codes.
-- =====================================================================================================================


-- =====================================================================================================================
-- L1  STAGING (exists): uploads as received, append only. Shown: the upload header (exists) and an example LMS
-- position template for clients without an LMS view (new, only for such clients; columns follow their template).
-- =====================================================================================================================

-- L1 (exists, migration 001/005): one row per upload; every stg_* row carries its load_id.
-- automn.input_load (load_id, tenant_id, batch_id, submission_type, file_uri, status LOADED/FAILED/PURGED, loaded_at, ...)

-- L1 (new, example): LMS positions uploaded as Excel, column for column; the client's template decides the columns.
CREATE TABLE automn.stg_lms_position (
	stg_row_id BIGINT NOT NULL IDENTITY,
	load_id BIGINT NOT NULL,                       -- L1: the upload (automn.input_load)
	source_row INTEGER NOT NULL,                   -- L1: Excel row, for messages
	account_no NVARCHAR(50) NULL,                  -- as in the client's file (mapped to dm_sanction.account_number)
	sanction_no NVARCHAR(50) NULL,
	position_date NVARCHAR(30) NULL,               -- as typed; the mapping parses it
	principal_outstanding NVARCHAR(40) NULL,
	overdue_amount NVARCHAR(40) NULL,
	dpd NVARCHAR(10) NULL,
	asset_class NVARCHAR(40) NULL,
	account_status NVARCHAR(40) NULL,
	last_payment_date NVARCHAR(30) NULL,
	last_payment_amount NVARCHAR(40) NULL,
	closed_date NVARCHAR(30) NULL,
	PRIMARY KEY (stg_row_id)
);
CREATE INDEX ix_stg_lms_position_key ON automn.stg_lms_position (account_no, position_date, load_id);


-- =====================================================================================================================
-- L2a  DEMOGRAPHICS MASTER (new): current customers, sanctions, roles, securities, contacts. Overwrite by key: the
-- newest upload wins completely (a blank clears); lists are replaced per customer; nothing is deleted (is_active).
-- No SCD 2: L1 staging is the history. Every row records the upload that last wrote it.
-- =====================================================================================================================

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


-- =====================================================================================================================
-- L2b  ACCOUNT POSITION (new): one canonical VIEW per client, no copy. The extract only ever reads
-- automn.vw_pos_account_position for one as-of date. Columns are fixed (the contract); the body is generated from
-- the client's mapping. Two shapes, one per kind of source:
-- =====================================================================================================================

-- L2b contract (columns every client's view returns):
--   tenant_id, account_number, sanction_number, as_of_date,
--   current_balance, amount_overdue, days_past_due, asset_classification, asset_classification_date,
--   credit_facility_status, status_date, date_last_payment, actual_payment_amount, date_closed, date_reported,
--   high_credit_amount, credit_limit, cash_limit, drawing_power,
--   overdue_bucket_1 .. overdue_bucket_5, written_off_total, written_off_principal, settlement_amount,
--   npa_contracts_amount, suit_filed_status, suit_reference, suit_amount, suit_date, wilful_default_date,
--   ufce_amount, ufce_date, source (VIEW / UPLOAD), source_load_id (UPLOAD only)

-- L2b shape A: the client has an LMS view (history by date in their database). No data load. (rarfincare)
CREATE OR ALTER VIEW automn.vw_pos_account_position AS
SELECT
	'nbfc_001' AS tenant_id,
	CAST(v.AccountNumber AS NVARCHAR(50)) AS account_number,
	'' AS sanction_number,
	CAST(v.TransactionDate AS DATE) AS as_of_date,
	automn.fn_amount(v.CurrentBalance) AS current_balance,
	automn.fn_amount(v.AmountOverdue) AS amount_overdue,
	automn.fn_int(v.DaysPastDue) AS days_past_due,
	-- ... the remaining contract columns, mapped from the view (codes through automn.vw_ref_code) ...
	'VIEW' AS source,
	CAST(NULL AS BIGINT) AS source_load_id
FROM wip.VwConsumerSubmissionAccountSegment v;     -- illustrative column names: the mapping supplies the real ones

-- L2b shape B: the client uploads LMS Excel to L1 (stg_lms_position). The newest successful upload per account and
-- date wins (a re-upload for a date is a correction). Materialise later only if measurements need it.
-- CREATE OR ALTER VIEW automn.vw_pos_account_position AS
-- SELECT x.tenant_id, x.account_number, x.sanction_number, x.as_of_date, x.current_balance, ..., 'UPLOAD' AS source,
--        x.load_id AS source_load_id
-- FROM (SELECT l.tenant_id, s.account_no AS account_number, COALESCE(s.sanction_no, '') AS sanction_number,
--              automn.fn_date(s.position_date) AS as_of_date, automn.fn_amount(s.principal_outstanding) AS current_balance,
--              ..., s.load_id,
--              ROW_NUMBER() OVER (PARTITION BY s.account_no, COALESCE(s.sanction_no, ''), s.position_date
--                                 ORDER BY s.load_id DESC) AS rn
--       FROM automn.stg_lms_position s
--       JOIN automn.input_load l ON l.load_id = s.load_id AND l.status = 'LOADED') x
-- WHERE x.rn = 1;


-- =====================================================================================================================
-- L3  SUBMISSION SNAPSHOT (exists, unchanged): automn.extract_snapshot (header: tenant, submission_type, as_of_date,
-- status, ...) and the c_* tables (c_party, c_account, c_account_holder, c_party_identifier/_phone/_email/_address,
-- c_relationship, c_guarantor, c_security, c_account_history, c_cheque_dishonour, ...), every row keyed by
-- snapshot_id. Frozen once made. Built by the extract: L2a now + L2b for the as-of date, per the format's selection.
-- Added: where the snapshot's data came from, for tracing.
-- =====================================================================================================================

ALTER TABLE automn.extract_snapshot ADD
	dm_last_load_id BIGINT NULL,                   -- L2a: the newest L1 upload reflected in the master at extract time
	position_source NVARCHAR(10) NULL;             -- L2b: VIEW or UPLOAD


-- =====================================================================================================================
-- L4  OUTPUT FILES (new): one row per generated file, with its fingerprint; and the submission to the bureau.
-- The files themselves stay in storage forever (owner, 2026-11-06).
-- =====================================================================================================================

CREATE TABLE automn.out_file (
	file_id BIGINT NOT NULL IDENTITY,
	request_id BIGINT NOT NULL,                    -- the GENERATE request (automn.submission_request)
	snapshot_id BIGINT NOT NULL,                   -- L3: the snapshot it was generated from
	bureau_code NVARCHAR(20) NOT NULL,
	kind NVARCHAR(20) NOT NULL,                    -- BUREAU_FILE, BUREAU_EXCEL, REVIEW_EXCEL, EXCEPTIONS
	file_name NVARCHAR(255) NOT NULL,
	file_uri NVARCHAR(500) NOT NULL,
	sha256 CHAR(64) NOT NULL,                      -- fingerprint: proves the stored file is the one generated
	size_bytes BIGINT NOT NULL,
	record_count INTEGER NULL,
	created_at DATETIME2 NOT NULL,
	PRIMARY KEY (file_id)
);
CREATE INDEX ix_out_file_request ON automn.out_file (request_id);

CREATE TABLE automn.out_submission (
	request_id BIGINT NOT NULL,                    -- the GENERATE request whose bureau file was sent
	submitted_at DATETIME2 NOT NULL,
	submitted_by NVARCHAR(100) NOT NULL,
	bureau_code NVARCHAR(20) NOT NULL,
	ack_reference NVARCHAR(100) NULL,              -- the bureau's acknowledgement, if any
	status NVARCHAR(20) NOT NULL,                  -- SUBMITTED, ACCEPTED, REJECTED (rejections: requirements section 11)
	note NVARCHAR(500) NULL,
	PRIMARY KEY (request_id)
);
