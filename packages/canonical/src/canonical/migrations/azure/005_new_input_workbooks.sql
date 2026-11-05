-- New input workbooks (2026-10-30): Consumer_Submissions_Workbook_Staging / Commercial_Submissions_Workbook_Staging.
-- The staging tables change shape (consumer: sheet 'Consumer', 55 columns with labels; commercial: every sheet keyed
-- by Sanction Reference Number, new Sanction sheet). Staging holds each upload's rows only until its request is
-- processed (retention purges it), so the old tables are dropped and the new ones created; old uploads must be
-- uploaded again. input_load / input_issue keep their history. Generated from the code:
--   python -m submissions.ddl_mssql (the stg_* tables). Run once in rarfinmisdb; no BEGIN/COMMIT.
DROP TABLE automn.stg_commercial_as;
DROP TABLE automn.stg_commercial_bs;
DROP TABLE automn.stg_commercial_cd;
DROP TABLE automn.stg_commercial_gs;
DROP TABLE automn.stg_commercial_nesl_sanction;
DROP TABLE automn.stg_commercial_nesl_security;
DROP TABLE automn.stg_commercial_rs;
DROP TABLE automn.stg_commercial_ss;
DROP TABLE automn.stg_consumer_data_submission;

CREATE TABLE automn.stg_commercial_as (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	flag NVARCHAR(1000) NULL,
	segment_identifier NVARCHAR(1000) NULL,
	borrower_office_location_type_label NVARCHAR(1000) NULL,
	borrower_office_duns_number NVARCHAR(1000) NULL,
	address_line_1 NVARCHAR(1000) NULL,
	address_line_2 NVARCHAR(1000) NULL,
	address_line_3 NVARCHAR(1000) NULL,
	city_town NVARCHAR(1000) NULL,
	district NVARCHAR(1000) NULL,
	state_label NVARCHAR(1000) NULL,
	pin_code NVARCHAR(1000) NULL,
	country_label NVARCHAR(1000) NULL,
	mobile_number_s NVARCHAR(1000) NULL,
	telephone_area_code NVARCHAR(1000) NULL,
	telephone_number_s NVARCHAR(1000) NULL,
	fax_area_code NVARCHAR(1000) NULL,
	fax_number_s NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_as_key ON automn.stg_commercial_as (load_id, sanction_reference_number);

CREATE TABLE automn.stg_commercial_bs (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	flag NVARCHAR(1000) NULL,
	segment_identifier NVARCHAR(1000) NULL,
	member_branch_code NVARCHAR(1000) NULL,
	previous_member_branch_code NVARCHAR(1000) NULL,
	borrower_name NVARCHAR(1000) NULL,
	borrower_short_name NVARCHAR(1000) NULL,
	company_registration_number NVARCHAR(1000) NULL,
	date_of_incorporation NVARCHAR(1000) NULL,
	pan NVARCHAR(1000) NULL,
	cin NVARCHAR(1000) NULL,
	tin NVARCHAR(1000) NULL,
	service_tax_no NVARCHAR(1000) NULL,
	other_id NVARCHAR(1000) NULL,
	borrowers_legal_constitution_label NVARCHAR(1000) NULL,
	business_category_label NVARCHAR(1000) NULL,
	business_industry_type_label NVARCHAR(1000) NULL,
	class_of_activity_1 NVARCHAR(1000) NULL,
	class_of_activity_2 NVARCHAR(1000) NULL,
	class_of_activity_3 NVARCHAR(1000) NULL,
	sic_code NVARCHAR(1000) NULL,
	sales_figure NVARCHAR(1000) NULL,
	financial_year NVARCHAR(1000) NULL,
	number_of_employees NVARCHAR(1000) NULL,
	credit_rating NVARCHAR(1000) NULL,
	assessment_agency_authority NVARCHAR(1000) NULL,
	credit_rating_as_on NVARCHAR(1000) NULL,
	credit_rating_expiry_date NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_bs_key ON automn.stg_commercial_bs (load_id, sanction_reference_number);

CREATE TABLE automn.stg_commercial_cd (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	flag NVARCHAR(1000) NULL,
	segment_identifier NVARCHAR(1000) NULL,
	date_of_dishonour NVARCHAR(1000) NULL,
	amount NVARCHAR(1000) NULL,
	instrument_cheque_number NVARCHAR(1000) NULL,
	number_of_times_dishonoured NVARCHAR(1000) NULL,
	cheque_issue_date NVARCHAR(1000) NULL,
	reason_for_dishonour NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_cd_key ON automn.stg_commercial_cd (load_id, sanction_reference_number);

CREATE TABLE automn.stg_commercial_gs (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	flag NVARCHAR(1000) NULL,
	segment_identifier NVARCHAR(1000) NULL,
	guarantor_duns_number NVARCHAR(1000) NULL,
	guarantor_type_label NVARCHAR(1000) NULL,
	business_category_label NVARCHAR(1000) NULL,
	business_industry_type_label NVARCHAR(1000) NULL,
	guarantor_entity_name NVARCHAR(1000) NULL,
	individual_name_prefix NVARCHAR(1000) NULL,
	full_name NVARCHAR(1000) NULL,
	gender_label NVARCHAR(1000) NULL,
	company_registration_number NVARCHAR(1000) NULL,
	date_of_incorporation NVARCHAR(1000) NULL,
	date_of_birth NVARCHAR(1000) NULL,
	pan NVARCHAR(1000) NULL,
	voter_id NVARCHAR(1000) NULL,
	passport_number NVARCHAR(1000) NULL,
	driving_licence_id NVARCHAR(1000) NULL,
	uid NVARCHAR(1000) NULL,
	ration_card_no NVARCHAR(1000) NULL,
	cin NVARCHAR(1000) NULL,
	din NVARCHAR(1000) NULL,
	tin NVARCHAR(1000) NULL,
	service_tax_no NVARCHAR(1000) NULL,
	other_id NVARCHAR(1000) NULL,
	address_line_1 NVARCHAR(1000) NULL,
	address_line_2 NVARCHAR(1000) NULL,
	address_line_3 NVARCHAR(1000) NULL,
	city_town NVARCHAR(1000) NULL,
	district NVARCHAR(1000) NULL,
	state_label NVARCHAR(1000) NULL,
	pin_code NVARCHAR(1000) NULL,
	country_label NVARCHAR(1000) NULL,
	mobile_number_s NVARCHAR(1000) NULL,
	telephone_area_code NVARCHAR(1000) NULL,
	telephone_number_s NVARCHAR(1000) NULL,
	fax_area_code NVARCHAR(1000) NULL,
	fax_number_s NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_gs_key ON automn.stg_commercial_gs (load_id, sanction_reference_number);

CREATE TABLE automn.stg_commercial_nesl_security (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	loan_account_no NVARCHAR(1000) NULL,
	customer_name NVARCHAR(1000) NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	date_of_creation_of_security_interest NVARCHAR(1000) NULL,
	nesl_type_of_charge_created NVARCHAR(1000) NULL,
	nesl_asset_type NVARCHAR(1000) NULL,
	nesl_security_type NVARCHAR(1000) NULL,
	nesl_security_category NVARCHAR(1000) NULL,
	asset_id NVARCHAR(1000) NULL,
	description_of_security NVARCHAR(1000) NULL,
	value_of_security NVARCHAR(1000) NULL,
	date_of_valuation NVARCHAR(1000) NULL,
	roc_charge_id NVARCHAR(1000) NULL,
	cersai_security_interest_id NVARCHAR(1000) NULL,
	part_b_remarks NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_nesl_security_key ON automn.stg_commercial_nesl_security (load_id, sanction_reference_number);

CREATE TABLE automn.stg_commercial_rs (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	flag NVARCHAR(1000) NULL,
	segment_identifier NVARCHAR(1000) NULL,
	relationship_duns_number NVARCHAR(1000) NULL,
	related_type_label NVARCHAR(1000) NULL,
	relationship_label NVARCHAR(1000) NULL,
	business_entity_name NVARCHAR(1000) NULL,
	business_category NVARCHAR(1000) NULL,
	business_industry_type NVARCHAR(1000) NULL,
	individual_name_prefix NVARCHAR(1000) NULL,
	full_name NVARCHAR(1000) NULL,
	company_registration_number NVARCHAR(1000) NULL,
	gender_label NVARCHAR(1000) NULL,
	date_of_incorporation NVARCHAR(1000) NULL,
	date_of_birth NVARCHAR(1000) NULL,
	pan NVARCHAR(1000) NULL,
	voter_id NVARCHAR(1000) NULL,
	passport_number NVARCHAR(1000) NULL,
	driving_licence_id NVARCHAR(1000) NULL,
	uid NVARCHAR(1000) NULL,
	ration_card_no NVARCHAR(1000) NULL,
	cin NVARCHAR(1000) NULL,
	din NVARCHAR(1000) NULL,
	tin NVARCHAR(1000) NULL,
	service_tax_no NVARCHAR(1000) NULL,
	other_id NVARCHAR(1000) NULL,
	percentage_of_control NVARCHAR(1000) NULL,
	address_line_1 NVARCHAR(1000) NULL,
	address_line_2 NVARCHAR(1000) NULL,
	address_line_3 NVARCHAR(1000) NULL,
	city_town NVARCHAR(1000) NULL,
	district NVARCHAR(1000) NULL,
	state_label NVARCHAR(1000) NULL,
	pin_code NVARCHAR(1000) NULL,
	country_label NVARCHAR(1000) NULL,
	mobile_number_s NVARCHAR(1000) NULL,
	telephone_number_s NVARCHAR(1000) NULL,
	telephone_area_code NVARCHAR(1000) NULL,
	fax_number_s NVARCHAR(1000) NULL,
	fax_area_code NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_rs_key ON automn.stg_commercial_rs (load_id, sanction_reference_number);

CREATE TABLE automn.stg_commercial_sanction (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	loan_account_no NVARCHAR(1000) NULL,
	customer_name NVARCHAR(1000) NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	credit_type_label NVARCHAR(1000) NULL,
	repayment_frequency_label NVARCHAR(1000) NULL,
	contact_person_name NVARCHAR(1000) NULL,
	suit_filed_status NVARCHAR(1000) NULL,
	suit_reference_number NVARCHAR(1000) NULL,
	suit_amount_in_rupees NVARCHAR(1000) NULL,
	creditor_rm_email NVARCHAR(1000) NULL,
	nesl_debt_subtype NVARCHAR(1000) NULL,
	date_of_suit_filing NVARCHAR(1000) NULL,
	date_of_last_acknowledgement NVARCHAR(1000) NULL,
	nesl_indicator NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_sanction_key ON automn.stg_commercial_sanction (load_id, sanction_reference_number);

CREATE TABLE automn.stg_commercial_ss (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	sanction_reference_number NVARCHAR(100) NULL,
	flag NVARCHAR(1000) NULL,
	segment_identifier NVARCHAR(1000) NULL,
	value_of_security NVARCHAR(1000) NULL,
	currency_type NVARCHAR(1000) NULL,
	type_of_security_label NVARCHAR(1000) NULL,
	security_classification_label NVARCHAR(1000) NULL,
	date_of_valuation NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_commercial_ss_key ON automn.stg_commercial_ss (load_id, sanction_reference_number);

CREATE TABLE automn.stg_consumer (
	load_id BIGINT NOT NULL,
	source_row INTEGER NOT NULL,
	consumer_name NVARCHAR(1000) NULL,
	date_of_birth NVARCHAR(1000) NULL,
	gender_label NVARCHAR(1000) NULL,
	income_tax_id_number NVARCHAR(1000) NULL,
	passport_number NVARCHAR(1000) NULL,
	passport_issue_date NVARCHAR(1000) NULL,
	passport_expiry_date NVARCHAR(1000) NULL,
	voter_id_number NVARCHAR(1000) NULL,
	driving_license_number NVARCHAR(1000) NULL,
	driving_license_issue_date NVARCHAR(1000) NULL,
	driving_license_expiry_date NVARCHAR(1000) NULL,
	ration_card_number NVARCHAR(1000) NULL,
	universal_id_number NVARCHAR(1000) NULL,
	additional_id_no_1 NVARCHAR(1000) NULL,
	additional_id_no_2 NVARCHAR(1000) NULL,
	telephone_no_mobile NVARCHAR(1000) NULL,
	telephone_no_residence NVARCHAR(1000) NULL,
	telephone_no_office NVARCHAR(1000) NULL,
	extension_office NVARCHAR(1000) NULL,
	telephone_no_other NVARCHAR(1000) NULL,
	extension_other NVARCHAR(1000) NULL,
	email_id_1 NVARCHAR(1000) NULL,
	email_id_2 NVARCHAR(1000) NULL,
	address_line_1 NVARCHAR(1000) NULL,
	state_code_1_label NVARCHAR(1000) NULL,
	pin_code_1 NVARCHAR(1000) NULL,
	address_category_1_label NVARCHAR(1000) NULL,
	residence_code_1_label NVARCHAR(1000) NULL,
	address_line_2 NVARCHAR(1000) NULL,
	state_code_2_label NVARCHAR(1000) NULL,
	pin_code_2 NVARCHAR(1000) NULL,
	address_category_2_label NVARCHAR(1000) NULL,
	residence_code_2_label NVARCHAR(1000) NULL,
	current_new_member_code NVARCHAR(1000) NULL,
	current_new_member_short_name NVARCHAR(1000) NULL,
	curr_new_account_no NVARCHAR(100) NULL,
	account_type_label NVARCHAR(1000) NULL,
	account_type NVARCHAR(1000) NULL,
	ownership_indicator_label NVARCHAR(1000) NULL,
	old_mbr_code NVARCHAR(1000) NULL,
	old_mbr_short_name NVARCHAR(1000) NULL,
	old_acc_no NVARCHAR(1000) NULL,
	old_acc_type NVARCHAR(1000) NULL,
	old_ownership_indicator NVARCHAR(1000) NULL,
	suit_filed_wilful_default_label NVARCHAR(1000) NULL,
	value_of_collateral NVARCHAR(1000) NULL,
	type_of_collateral_label NVARCHAR(1000) NULL,
	payment_frequency_label NVARCHAR(1000) NULL,
	actual_payment_amt NVARCHAR(1000) NULL,
	occupation_code_label NVARCHAR(1000) NULL,
	income NVARCHAR(1000) NULL,
	net_gross_income_indicator NVARCHAR(1000) NULL,
	monthly_annual_income_indicator NVARCHAR(1000) NULL,
	ckyc NVARCHAR(1000) NULL,
	g_ram_g NVARCHAR(1000) NULL,
	PRIMARY KEY (load_id, source_row),
	FOREIGN KEY(load_id) REFERENCES automn.input_load (load_id)
);

CREATE INDEX ix_stg_consumer_key ON automn.stg_consumer (load_id, curr_new_account_no);
