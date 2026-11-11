-- MFI source views, PROVISIONAL (2026-11-10, ADR 0003 step 6, owner: option 2): the uploaded MFI workbook
-- (automn.stg_mfi_loan, automn.stg_mfi_group; canonical/types/mfi/inputs.yaml) as canonical rows. No loan system
-- view: the loan figures are the workbook's, for the upload's as-of date (automn.input_load.as_of_date). To be
-- replaced or joined to the client's loan system once their data source is known. Run 00_functions.sql and
-- 01_reference.sql first. Portable source: see consumer.sql (tools/build_views.py writes the Postgres / MySQL copies).
-- Run in rarfinmisdb in SSMS / Azure Data Studio (GO separates batches); safe to run again (CREATE OR ALTER).
--
-- Rules (as consumer.sql):
--   - Staging is append only. An account's rows are its rows in the NEWEST successfully loaded upload that has it;
--     a group's, the newest upload that has the group. A request reports the accounts in its own upload (load_id).
--   - The member is the customer: party_key = Member ID. A member with several loans has one party (the details of
--     their first account in the upload, by account number; the upload checks that the rows agree).
--   - Coded columns hold the MFI file code (F, TN, T05, K02 ...) -> join automn.vw_ref_code; unknown values are passed
--     on as they are, so validation reports them.

-- ---------------------------------------------------------------- building blocks (not read by the app directly)
-- Each account's rows from its newest LOADED upload.
CREATE OR ALTER VIEW automn.vw_mfi_stg_latest AS
SELECT x.*
FROM (SELECT s.*, l.loaded_at, MAX(s.load_id) OVER (PARTITION BY s.account_number) AS latest_load_id
      FROM automn.stg_mfi_loan s
      JOIN automn.input_load l ON l.load_id = s.load_id AND l.status = 'LOADED') x
WHERE x.load_id = x.latest_load_id;
GO

-- Each group's row from its newest LOADED upload.
CREATE OR ALTER VIEW automn.vw_mfi_group_latest AS
SELECT x.*
FROM (SELECT g.*, MAX(g.load_id) OVER (PARTITION BY g.group_id) AS latest_load_id,
             ROW_NUMBER() OVER (PARTITION BY g.load_id, g.group_id ORDER BY g.source_row) AS rn
      FROM automn.stg_mfi_group g
      JOIN automn.input_load l ON l.load_id = g.load_id AND l.status = 'LOADED') x
WHERE x.load_id = x.latest_load_id AND x.rn = 1;
GO

-- The accounts of each upload (upload_load_id: the request's upload) with their newest rows (load_id: the upload
-- they came from).
CREATE OR ALTER VIEW automn.vw_mfi_loan AS
SELECT u.upload_load_id, s.*
FROM (SELECT DISTINCT load_id AS upload_load_id, account_number AS upload_account_number
      FROM automn.stg_mfi_loan) u
JOIN automn.vw_mfi_stg_latest s ON s.account_number = u.upload_account_number;
GO

-- One row per member of each upload: their first account's row, by account number.
CREATE OR ALTER VIEW automn.vw_mfi_member AS
SELECT x.*
FROM (SELECT m.*, ROW_NUMBER() OVER (PARTITION BY m.upload_load_id, m.member_id ORDER BY m.account_number) AS member_rn
      FROM automn.vw_mfi_loan m
      WHERE NULLIF(LTRIM(RTRIM(m.member_id)), '') IS NOT NULL) x
WHERE x.member_rn = 1;
GO

-- ---------------------------------------------------------------- the account (ACTCRD)
CREATE OR ALTER VIEW automn.vw_mfi_account AS
SELECT
    s.upload_load_id AS load_id,
    s.load_id AS source_load_id,     -- the upload the rows came from, and when (for checking)
    s.loaded_at AS source_loaded_at,
    il.as_of_date,                   -- the request upload's as-of date (Uploads page or the request's date)
    s.account_number AS account_key,
    s.account_number,
    NULLIF(LTRIM(RTRIM(s.unique_reference)), '') AS unique_reference,
    NULLIF(LTRIM(RTRIM(s.branch_id)), '') AS branch_code,
    NULLIF(LTRIM(RTRIM(s.centre_id)), '') AS centre_id,
    NULLIF(LTRIM(RTRIM(s.group_id)), '') AS group_key,
    NULLIF(LTRIM(RTRIM(s.loan_officer)), '') AS loan_officer,
    COALESCE(c_cat.code, NULLIF(LTRIM(RTRIM(s.loan_category)), '')) AS loan_category,
    automn.fn_int(s.loan_cycle) AS loan_cycle,
    NULLIF(LTRIM(RTRIM(s.loan_purpose)), '') AS loan_purpose,
    COALESCE(c_ltype.code, NULLIF(LTRIM(RTRIM(s.loan_type)), '')) AS loan_type,
    COALESCE(c_status.code, NULLIF(LTRIM(RTRIM(s.account_status)), '')) AS credit_facility_status,
    automn.fn_date(s.sanctioned_date) AS sanctioned_date,
    automn.fn_date(s.date_disbursed) AS date_opened,
    automn.fn_date(s.date_closed) AS date_closed,
    automn.fn_date(s.date_last_payment) AS date_last_payment,
    automn.fn_amount(s.applied_amount) AS applied_amount,
    automn.fn_amount(s.sanctioned_amount) AS sanctioned_amount,
    automn.fn_amount(s.disbursed_amount) AS high_credit_amount,
    automn.fn_int(s.instalments) AS repayment_tenure,
    COALESCE(c_freq.code, NULLIF(LTRIM(RTRIM(s.repayment_frequency)), '')) AS payment_frequency,
    automn.fn_amount(s.instalment_amount) AS emi_amount,
    automn.fn_amount(s.current_balance) AS current_balance,
    automn.fn_amount(s.amount_overdue) AS amount_overdue,
    automn.fn_int(s.days_past_due) AS days_past_due,
    automn.fn_amount(s.write_off_amount) AS written_off_total,
    automn.fn_date(s.write_off_date) AS write_off_date,
    COALESCE(c_wo.code, NULLIF(LTRIM(RTRIM(s.write_off_reason)), '')) AS write_off_reason,
    COALESCE(c_ins.code, NULLIF(LTRIM(RTRIM(s.insurance_indicator)), '')) AS insurance_indicator,
    COALESCE(c_itype.code, NULLIF(LTRIM(RTRIM(s.insurance_type)), '')) AS insurance_type,
    automn.fn_amount(s.sum_assured) AS sum_assured,
    automn.fn_amount(s.settlement_amount) AS settlement_amount,
    NULLIF(LTRIM(RTRIM(s.old_unique_reference)), '') AS old_unique_reference,
    NULLIF(LTRIM(RTRIM(s.old_account_number)), '') AS old_account_number
FROM automn.vw_mfi_loan s
JOIN automn.input_load il ON il.load_id = s.upload_load_id
LEFT JOIN automn.vw_ref_code c_cat ON c_cat.submission_type = 'MFI' AND c_cat.code_list = 'loan_category'
      AND c_cat.code_key = UPPER(LTRIM(RTRIM(s.loan_category)))
LEFT JOIN automn.vw_ref_code c_ltype ON c_ltype.submission_type = 'MFI' AND c_ltype.code_list = 'loan_type'
      AND c_ltype.code_key = UPPER(LTRIM(RTRIM(s.loan_type)))
LEFT JOIN automn.vw_ref_code c_status ON c_status.submission_type = 'MFI'
      AND c_status.code_list = 'credit_facility_status' AND c_status.code_key = UPPER(LTRIM(RTRIM(s.account_status)))
LEFT JOIN automn.vw_ref_code c_freq ON c_freq.submission_type = 'MFI' AND c_freq.code_list = 'payment_frequency'
      AND c_freq.code_key = UPPER(LTRIM(RTRIM(s.repayment_frequency)))
LEFT JOIN automn.vw_ref_code c_wo ON c_wo.submission_type = 'MFI' AND c_wo.code_list = 'write_off_reason'
      AND c_wo.code_key = UPPER(LTRIM(RTRIM(s.write_off_reason)))
LEFT JOIN automn.vw_ref_code c_ins ON c_ins.submission_type = 'MFI' AND c_ins.code_list = 'yes_no'
      AND c_ins.code_key = UPPER(LTRIM(RTRIM(s.insurance_indicator)))
LEFT JOIN automn.vw_ref_code c_itype ON c_itype.submission_type = 'MFI' AND c_itype.code_list = 'insurance_type'
      AND c_itype.code_key = UPPER(LTRIM(RTRIM(s.insurance_type)));
GO

-- One borrower per loan: the member.
CREATE OR ALTER VIEW automn.vw_mfi_account_holder AS
SELECT s.upload_load_id AS load_id, s.account_number AS account_key, LTRIM(RTRIM(s.member_id)) AS party_key,
       'INDIVIDUAL' AS ownership_indicator
FROM automn.vw_mfi_loan s
WHERE NULLIF(LTRIM(RTRIM(s.member_id)), '') IS NOT NULL;
GO

-- ---------------------------------------------------------------- the member (CNSCRD)
CREATE OR ALTER VIEW automn.vw_mfi_party AS
SELECT
    m.upload_load_id AS load_id,
    m.load_id AS source_load_id,
    m.loaded_at AS source_loaded_at,
    LTRIM(RTRIM(m.member_id)) AS party_key,
    m.member_name AS full_name,
    automn.fn_date(m.date_of_birth) AS birth_date,
    automn.fn_int(m.age) AS age,
    automn.fn_date(m.age_as_on) AS age_as_of,
    COALESCE(c_gender.code, NULLIF(LTRIM(RTRIM(m.gender)), '')) AS gender,
    COALESCE(c_marital.code, NULLIF(LTRIM(RTRIM(m.marital_status)), '')) AS marital_status,
    NULLIF(LTRIM(RTRIM(m.branch_id)), '') AS branch_id,
    NULLIF(LTRIM(RTRIM(m.centre_id)), '') AS centre_id,
    NULLIF(LTRIM(RTRIM(m.group_id)), '') AS group_key,
    NULLIF(LTRIM(RTRIM(m.old_member_id)), '') AS old_party_key,
    COALESCE(c_edu.code, NULLIF(LTRIM(RTRIM(m.education)), '')) AS education,
    COALESCE(c_occ.code, NULLIF(LTRIM(RTRIM(m.occupation)), '')) AS occupation,
    COALESCE(c_rel.code, NULLIF(LTRIM(RTRIM(m.religion)), '')) AS religion,
    COALESCE(c_soc.code, NULLIF(LTRIM(RTRIM(m.social_category)), '')) AS social_category,
    COALESCE(c_role.code, NULLIF(LTRIM(RTRIM(m.member_role)), '')) AS member_role,
    COALESCE(c_mstat.code, NULLIF(LTRIM(RTRIM(m.member_status)), '')) AS member_status,
    automn.fn_amount(m.family_income) AS family_income,
    automn.fn_amount(m.family_expenses) AS family_expenses,
    NULLIF(LTRIM(RTRIM(m.bank_name)), '') AS bank_name,
    NULLIF(LTRIM(RTRIM(m.bank_branch)), '') AS bank_branch,
    NULLIF(LTRIM(RTRIM(m.bank_account_number)), '') AS bank_account_number
FROM automn.vw_mfi_member m
LEFT JOIN automn.vw_ref_code c_gender ON c_gender.submission_type = 'MFI' AND c_gender.code_list = 'gender'
      AND c_gender.code_key = UPPER(LTRIM(RTRIM(m.gender)))
LEFT JOIN automn.vw_ref_code c_marital ON c_marital.submission_type = 'MFI' AND c_marital.code_list = 'marital_status'
      AND c_marital.code_key = UPPER(LTRIM(RTRIM(m.marital_status)))
LEFT JOIN automn.vw_ref_code c_edu ON c_edu.submission_type = 'MFI' AND c_edu.code_list = 'education'
      AND c_edu.code_key = UPPER(LTRIM(RTRIM(m.education)))
LEFT JOIN automn.vw_ref_code c_occ ON c_occ.submission_type = 'MFI' AND c_occ.code_list = 'occupation'
      AND c_occ.code_key = UPPER(LTRIM(RTRIM(m.occupation)))
LEFT JOIN automn.vw_ref_code c_rel ON c_rel.submission_type = 'MFI' AND c_rel.code_list = 'religion'
      AND c_rel.code_key = UPPER(LTRIM(RTRIM(m.religion)))
LEFT JOIN automn.vw_ref_code c_soc ON c_soc.submission_type = 'MFI' AND c_soc.code_list = 'social_category'
      AND c_soc.code_key = UPPER(LTRIM(RTRIM(m.social_category)))
LEFT JOIN automn.vw_ref_code c_role ON c_role.submission_type = 'MFI' AND c_role.code_list = 'member_role'
      AND c_role.code_key = UPPER(LTRIM(RTRIM(m.member_role)))
LEFT JOIN automn.vw_ref_code c_mstat ON c_mstat.submission_type = 'MFI' AND c_mstat.code_list = 'member_status'
      AND c_mstat.code_key = UPPER(LTRIM(RTRIM(m.member_status)));
GO

-- Key person, relatives and nominee: one row per name present (relative_role decides the file field).
CREATE OR ALTER VIEW automn.vw_mfi_party_relative AS
SELECT m.upload_load_id AS load_id, LTRIM(RTRIM(m.member_id)) AS party_key, r.relative_role, r.priority,
       r.full_name, COALESCE(c_relation.code, NULLIF(LTRIM(RTRIM(r.relation)), '')) AS relation
FROM automn.vw_mfi_member m
CROSS APPLY (VALUES
    ('KEY_PERSON', 1, m.key_person_name, m.key_person_relation),
    ('RELATIVE', 1, m.relative_1_name, m.relative_1_relation),
    ('RELATIVE', 2, m.relative_2_name, m.relative_2_relation),
    ('NOMINEE', 1, m.nominee_name, m.nominee_relation)
) r(relative_role, priority, full_name, relation)
LEFT JOIN automn.vw_ref_code c_relation ON c_relation.submission_type = 'MFI' AND c_relation.code_list = 'relation'
      AND c_relation.code_key = UPPER(LTRIM(RTRIM(r.relation)))
WHERE NULLIF(LTRIM(RTRIM(r.full_name)), '') IS NOT NULL;
GO

CREATE OR ALTER VIEW automn.vw_mfi_party_identifier AS
SELECT m.upload_load_id AS load_id, LTRIM(RTRIM(m.member_id)) AS party_key, d.id_type, d.id_number
FROM automn.vw_mfi_member m
CROSS APPLY (VALUES
    ('VOTER_ID', m.voter_id),
    ('AADHAAR', m.aadhaar),
    ('PAN', m.pan),
    ('RATION_CARD', m.ration_card),
    ('G_RAM_G', m.g_ram_g),
    ('CKYC', m.ckyc),
    ('DRIVING_LICENCE', m.driving_licence),
    ('PASSPORT', m.passport)
) d(id_type, id_number)
WHERE NULLIF(LTRIM(RTRIM(d.id_number)), '') IS NOT NULL;
GO

CREATE OR ALTER VIEW automn.vw_mfi_party_phone AS
SELECT m.upload_load_id AS load_id, LTRIM(RTRIM(m.member_id)) AS party_key, p.phone_number, p.phone_type, p.priority
FROM automn.vw_mfi_member m
CROSS APPLY (VALUES (m.mobile, 'MOBILE', 1), (m.landline, 'HOME', 2)) p(phone_number, phone_type, priority)
WHERE NULLIF(LTRIM(RTRIM(p.phone_number)), '') IS NOT NULL;
GO

CREATE OR ALTER VIEW automn.vw_mfi_party_email AS
SELECT m.upload_load_id AS load_id, LTRIM(RTRIM(m.member_id)) AS party_key, m.email, 1 AS priority
FROM automn.vw_mfi_member m
WHERE NULLIF(LTRIM(RTRIM(m.email)), '') IS NOT NULL;
GO

-- ---------------------------------------------------------------- addresses (ADRCRD): permanent and current
CREATE OR ALTER VIEW automn.vw_mfi_party_address AS
SELECT m.upload_load_id AS load_id, LTRIM(RTRIM(m.member_id)) AS party_key,
       a.address_line_1,
       COALESCE(c_state.code, NULLIF(LTRIM(RTRIM(a.state)), '')) AS state_code,
       a.pin_code, a.address_category, a.priority
FROM automn.vw_mfi_member m
CROSS APPLY (VALUES
    (m.permanent_address, m.permanent_state, m.permanent_pin, 'PERMANENT', 1),
    (m.current_address, m.current_state, m.current_pin, 'RESIDENCE', 2)
) a(address_line_1, state, pin_code, address_category, priority)
LEFT JOIN automn.vw_ref_code c_state ON c_state.submission_type = 'MFI' AND c_state.code_list = 'state_code'
      AND c_state.code_key = UPPER(LTRIM(RTRIM(a.state)))
WHERE NULLIF(LTRIM(RTRIM(a.address_line_1)), '') IS NOT NULL;
GO

-- ---------------------------------------------------------------- the group (GRPCRD): the groups of each upload's
-- accounts, from the newest upload that has the group
CREATE OR ALTER VIEW automn.vw_mfi_borrower_group AS
SELECT
    u.upload_load_id AS load_id,
    g.group_id AS group_key,
    g.group_name AS full_name,
    NULLIF(LTRIM(RTRIM(g.group_account_number)), '') AS group_account_number,
    automn.fn_amount(g.disbursed_amount) AS disbursed_amount,
    automn.fn_amount(g.current_balance) AS current_balance,
    automn.fn_date(g.date_opened) AS date_opened,
    automn.fn_date(g.date_closed) AS date_closed,
    automn.fn_date(g.date_last_payment) AS date_last_payment,
    automn.fn_int(g.instalments) AS repayment_tenure,
    COALESCE(c_freq.code, NULLIF(LTRIM(RTRIM(g.repayment_frequency)), '')) AS payment_frequency,
    automn.fn_amount(g.instalment_amount) AS emi_amount,
    automn.fn_amount(g.amount_overdue) AS amount_overdue,
    automn.fn_int(g.days_past_due) AS days_past_due,
    automn.fn_int(g.loan_cycle) AS loan_cycle,
    COALESCE(c_ltype.code, NULLIF(LTRIM(RTRIM(g.loan_type)), '')) AS loan_type,
    automn.fn_amount(g.write_off_amount) AS written_off_total,
    automn.fn_date(g.write_off_date) AS write_off_date,
    COALESCE(c_wo.code, NULLIF(LTRIM(RTRIM(g.write_off_reason)), '')) AS write_off_reason,
    NULLIF(LTRIM(RTRIM(g.address)), '') AS address_line_1,
    COALESCE(c_state.code, NULLIF(LTRIM(RTRIM(g.state)), '')) AS state_code,
    NULLIF(LTRIM(RTRIM(g.pin)), '') AS pin_code,
    COALESCE(c_ptype.code, NULLIF(LTRIM(RTRIM(g.phone_type)), '')) AS phone_type,
    NULLIF(LTRIM(RTRIM(g.phone)), '') AS phone_number,
    NULLIF(LTRIM(RTRIM(g.bank_name)), '') AS bank_name,
    NULLIF(LTRIM(RTRIM(g.bank_branch)), '') AS bank_branch,
    NULLIF(LTRIM(RTRIM(g.bank_account_number)), '') AS bank_account_number,
    automn.fn_amount(g.bank_balance) AS bank_balance,
    automn.fn_date(g.formed_date) AS formed_date,
    automn.fn_date(g.first_linkage_date) AS first_linkage_date,
    NULLIF(LTRIM(RTRIM(g.govt_group_id)), '') AS govt_group_id,
    COALESCE(c_prog.code, NULLIF(LTRIM(RTRIM(g.govt_program)), '')) AS govt_program
FROM (SELECT DISTINCT upload_load_id, LTRIM(RTRIM(group_id)) AS group_id FROM automn.vw_mfi_loan
      WHERE NULLIF(LTRIM(RTRIM(group_id)), '') IS NOT NULL) u
JOIN automn.vw_mfi_group_latest g ON LTRIM(RTRIM(g.group_id)) = u.group_id
LEFT JOIN automn.vw_ref_code c_freq ON c_freq.submission_type = 'MFI' AND c_freq.code_list = 'payment_frequency'
      AND c_freq.code_key = UPPER(LTRIM(RTRIM(g.repayment_frequency)))
LEFT JOIN automn.vw_ref_code c_ltype ON c_ltype.submission_type = 'MFI' AND c_ltype.code_list = 'loan_type'
      AND c_ltype.code_key = UPPER(LTRIM(RTRIM(g.loan_type)))
LEFT JOIN automn.vw_ref_code c_wo ON c_wo.submission_type = 'MFI' AND c_wo.code_list = 'write_off_reason'
      AND c_wo.code_key = UPPER(LTRIM(RTRIM(g.write_off_reason)))
LEFT JOIN automn.vw_ref_code c_state ON c_state.submission_type = 'MFI' AND c_state.code_list = 'state_code'
      AND c_state.code_key = UPPER(LTRIM(RTRIM(g.state)))
LEFT JOIN automn.vw_ref_code c_ptype ON c_ptype.submission_type = 'MFI' AND c_ptype.code_list = 'phone_type'
      AND c_ptype.code_key = UPPER(LTRIM(RTRIM(g.phone_type)))
LEFT JOIN automn.vw_ref_code c_prog ON c_prog.submission_type = 'MFI' AND c_prog.code_list = 'govt_program'
      AND c_prog.code_key = UPPER(LTRIM(RTRIM(g.govt_program)));
GO
