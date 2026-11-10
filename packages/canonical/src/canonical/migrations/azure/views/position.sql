-- L2b account position (ADR 0002, PR 3): one row per account and as-of date with the loan system's figures, in the
-- fixed contract columns (canonical.position.POSITION_COLUMNS). The extract (PR 4) reads only
-- automn.vw_pos_account_position, for one date. Nothing reads it yet. Run after 00_functions.sql and 01_reference.sql.
-- Portable source: same rules as consumer.sql (CONCAT, automn.fn_*, joins to vw_ref_code; build_views translates it).
--
-- Two kinds of source, one contract:
--   A. the client's LMS views (rarfincare: wip.VwConsumerSubmissionAccountSegment and wip.VwCommercialSubmissionCR):
--      their history by date, no data load. Same field logic as vw_consumer_account / vw_commercial_account.
--   B. LMS Excel uploads (automn.stg_lms_position, migration 012) for clients without an LMS view: the newest LOADED
--      upload per account, sanction and date wins (a re-upload for a date is a correction).
-- vw_pos_account_position is A and B together; a client uses one of them (the configured mapping, PR 6, will generate
-- only the client's own). lms_type says which LMS view a row came from (CONSUMER / COMMERCIAL; '' for an upload): the
-- same account can be in both of rarfincare's LMS views, and each format reads its own (2026-11-10, parity report). Every column is CAST so the parts line up. No tenant column: one client per database, as in
-- the other source views. sanction_number is '' for LMS views until the mapping names a column (ADR open point).

-- ---------------------------------------------------------------- A1: consumer LMS view
CREATE OR ALTER VIEW automn.vw_pos_lms_consumer AS
SELECT
    CAST(v.AccountNumber AS VARCHAR(50)) AS account_number,
    CAST('' AS VARCHAR(50)) AS sanction_number,
    CAST(v.TransactionDate AS DATE) AS as_of_date,
    CAST(v.TL_CurrentBalance AS DECIMAL(20, 2)) AS current_balance,
    CAST(v.TL_AmtOverdue AS DECIMAL(20, 2)) AS amount_overdue,
    CAST(v.TL_NoofDaysPastDue AS INT) AS days_past_due,
    CAST(COALESCE(c_asset.code, v.asset_code) AS VARCHAR(40)) AS asset_classification,
    CAST(NULL AS DATE) AS asset_classification_date,
    CAST(COALESCE(c_status.code, v.status_code) AS VARCHAR(40)) AS credit_facility_status,
    CAST(NULL AS DATE) AS status_date,
    automn.fn_date(CAST(v.TL_DateofLastPayment AS VARCHAR(40))) AS date_last_payment,
    CAST(v.TL_ActualPaymentAmt AS DECIMAL(20, 2)) AS actual_payment_amount,
    automn.fn_date(CAST(v.TL_DateClosed AS VARCHAR(40))) AS date_closed,
    automn.fn_date(CAST(v.TL_DateReported AS VARCHAR(40))) AS date_reported,
    CAST(v.TL_SanctionedAmt AS DECIMAL(20, 2)) AS high_credit_amount,
    CAST(v.TL_CreditLimit AS DECIMAL(20, 2)) AS credit_limit,
    CAST(v.TL_CashLimit AS DECIMAL(20, 2)) AS cash_limit,
    CAST(NULL AS DECIMAL(20, 2)) AS drawing_power,
    CAST(NULL AS DECIMAL(20, 2)) AS overdue_bucket_1,
    CAST(NULL AS DECIMAL(20, 2)) AS overdue_bucket_2,
    CAST(NULL AS DECIMAL(20, 2)) AS overdue_bucket_3,
    CAST(NULL AS DECIMAL(20, 2)) AS overdue_bucket_4,
    CAST(NULL AS DECIMAL(20, 2)) AS overdue_bucket_5,
    CAST(v.TL_WrittenoffAmountTotal AS DECIMAL(20, 2)) AS written_off_total,
    CAST(v.TL_WrittenOffPrincipalAmount AS DECIMAL(20, 2)) AS written_off_principal,
    CAST(v.TL_SettlementAmt AS DECIMAL(20, 2)) AS settlement_amount,
    CAST(NULL AS DECIMAL(20, 2)) AS npa_contracts_amount,
    CAST(NULL AS DATE) AS wilful_default_date,
    CAST(NULL AS DECIMAL(20, 2)) AS ufce_amount,
    CAST(NULL AS DATE) AS ufce_date,
    CAST('VIEW' AS VARCHAR(10)) AS source,
    CAST(NULL AS BIGINT) AS source_load_id,
    CAST('CONSUMER' AS VARCHAR(20)) AS lms_type
FROM (SELECT x.*,
             CASE WHEN x.TL_CreditFacilityStatus IS NOT NULL      -- 2-digit code: 5 -> 05
                  THEN RIGHT(CONCAT('0', CAST(x.TL_CreditFacilityStatus AS VARCHAR(3))), 2) END AS status_code,
             NULLIF(LTRIM(RTRIM(CAST(x.AssetClassification AS VARCHAR(40)))), '') AS asset_code
      FROM wip.VwConsumerSubmissionAccountSegment x) v
LEFT JOIN automn.vw_ref_code c_status ON c_status.submission_type = 'CONSUMER'
      AND c_status.code_list = 'credit_facility_status' AND c_status.code_key = v.status_code
LEFT JOIN automn.vw_ref_code c_asset ON c_asset.submission_type = 'CONSUMER'
      AND c_asset.code_list = 'asset_classification' AND c_asset.code_key = UPPER(v.asset_code);
GO

-- ---------------------------------------------------------------- A2: commercial LMS view
CREATE OR ALTER VIEW automn.vw_pos_lms_commercial AS
SELECT
    CAST(l.AccountNumber AS VARCHAR(50)) AS account_number,
    CAST('' AS VARCHAR(50)) AS sanction_number,
    CAST(l.transactiondate AS DATE) AS as_of_date,
    CAST(l.[Current Balance] AS DECIMAL(20, 2)) AS current_balance,
    CAST(l.[Amount Overdue] AS DECIMAL(20, 2)) AS amount_overdue,
    CAST(l.RBIODDays AS INT) AS days_past_due,
    CAST(COALESCE(c_asset.code, l.asset_code) AS VARCHAR(40)) AS asset_classification,
    automn.fn_date(l.asset_class_date_text) AS asset_classification_date,
    -- 01 open / 02 closed are not status codes in the spec: the file derives them from the dates (date_closed)
    CAST(CASE WHEN l.status_code IN ('01', '02') THEN NULL
              ELSE COALESCE(c_status.code, l.status_code) END AS VARCHAR(40)) AS credit_facility_status,
    automn.fn_date(CAST(l.[Account Status Date] AS VARCHAR(40))) AS status_date,
    CAST(l.LastPaymentDate AS DATE) AS date_last_payment,
    CAST(l.[Last Repaid Amount] AS DECIMAL(20, 2)) AS actual_payment_amount,
    CAST(CASE WHEN l.IsAccountClosed = 'Yes' THEN l.AcClosedDate END AS DATE) AS date_closed,
    CAST(NULL AS DATE) AS date_reported,
    CAST(l.[High Credit] AS DECIMAL(20, 2)) AS high_credit_amount,
    CAST(NULL AS DECIMAL(20, 2)) AS credit_limit,
    CAST(NULL AS DECIMAL(20, 2)) AS cash_limit,
    CAST(l.[Drawing Power] AS DECIMAL(20, 2)) AS drawing_power,
    CAST(l.[Overdue Bucket 01 ( 1 – 30 days)] AS DECIMAL(20, 2)) AS overdue_bucket_1,
    CAST(l.[Overdue Bucket 02 ( 31 – 60 days)] AS DECIMAL(20, 2)) AS overdue_bucket_2,
    CAST(l.[Overdue Bucket 03 ( 61 – 90 days)] AS DECIMAL(20, 2)) AS overdue_bucket_3,
    CAST(l.[Overdue Bucket 04 (91 – 180 days)] AS DECIMAL(20, 2)) AS overdue_bucket_4,
    CAST(l.[Overdue Bucket 05 (Above 180 days)] AS DECIMAL(20, 2)) AS overdue_bucket_5,
    CAST(l.[Written Off Amount] AS DECIMAL(20, 2)) AS written_off_total,
    CAST(NULL AS DECIMAL(20, 2)) AS written_off_principal,
    CAST(l.[Settled Amount] AS DECIMAL(20, 2)) AS settlement_amount,
    CAST(l.[Amount of Contracts Classified as NPA] AS DECIMAL(20, 2)) AS npa_contracts_amount,
    automn.fn_date(l.wilful_date_text) AS wilful_default_date,
    CAST(l.[UFCE Amount] AS DECIMAL(20, 2)) AS ufce_amount,
    automn.fn_date(l.ufce_date_text) AS ufce_date,
    CAST('VIEW' AS VARCHAR(10)) AS source,
    CAST(NULL AS BIGINT) AS source_load_id,
    CAST('COMMERCIAL' AS VARCHAR(20)) AS lms_type
FROM (SELECT y.*,
             CASE WHEN y.[Asset Classification Date] IS NOT NULL
                  THEN RIGHT(CONCAT('0', CAST(y.[Asset Classification Date] AS VARCHAR(8))), 8) END
                 AS asset_class_date_text,
             CASE WHEN y.[Date Classified as Wilful Default] IS NOT NULL
                  THEN RIGHT(CONCAT('0', CAST(y.[Date Classified as Wilful Default] AS VARCHAR(8))), 8) END
                 AS wilful_date_text,
             CASE WHEN y.[UFCE Date] IS NOT NULL
                  THEN RIGHT(CONCAT('0', CAST(y.[UFCE Date] AS VARCHAR(8))), 8) END AS ufce_date_text,
             NULLIF(LTRIM(RTRIM(CAST(y.[Asset Classification] AS VARCHAR(40)))), '') AS asset_code,
             NULLIF(LTRIM(RTRIM(CAST(y.[Account Status] AS VARCHAR(40)))), '') AS status_code
      FROM wip.VwCommercialSubmissionCR y) l
LEFT JOIN automn.vw_ref_code c_asset ON c_asset.submission_type = 'COMMERCIAL'
      AND c_asset.code_list = 'asset_classification' AND c_asset.code_key = UPPER(l.asset_code)
LEFT JOIN automn.vw_ref_code c_status ON c_status.submission_type = 'COMMERCIAL'
      AND c_status.code_list = 'credit_facility_status' AND c_status.code_key = UPPER(l.status_code);
GO

-- ---------------------------------------------------------------- B: LMS Excel uploads (newest per account and date)
CREATE OR ALTER VIEW automn.vw_pos_upload AS
SELECT
    CAST(x.account_number AS VARCHAR(50)) AS account_number,
    CAST(x.sanction_key AS VARCHAR(50)) AS sanction_number,
    x.as_of_date AS as_of_date,
    automn.fn_amount(x.current_balance) AS current_balance,
    automn.fn_amount(x.amount_overdue) AS amount_overdue,
    automn.fn_int(x.days_past_due) AS days_past_due,
    CAST(NULLIF(LTRIM(RTRIM(x.asset_classification)), '') AS VARCHAR(40)) AS asset_classification,
    automn.fn_date(x.asset_classification_date) AS asset_classification_date,
    CAST(NULLIF(LTRIM(RTRIM(x.credit_facility_status)), '') AS VARCHAR(40)) AS credit_facility_status,
    automn.fn_date(x.status_date) AS status_date,
    automn.fn_date(x.date_last_payment) AS date_last_payment,
    automn.fn_amount(x.actual_payment_amount) AS actual_payment_amount,
    automn.fn_date(x.date_closed) AS date_closed,
    automn.fn_date(x.date_reported) AS date_reported,
    automn.fn_amount(x.high_credit_amount) AS high_credit_amount,
    automn.fn_amount(x.credit_limit) AS credit_limit,
    automn.fn_amount(x.cash_limit) AS cash_limit,
    automn.fn_amount(x.drawing_power) AS drawing_power,
    automn.fn_amount(x.overdue_bucket_1) AS overdue_bucket_1,
    automn.fn_amount(x.overdue_bucket_2) AS overdue_bucket_2,
    automn.fn_amount(x.overdue_bucket_3) AS overdue_bucket_3,
    automn.fn_amount(x.overdue_bucket_4) AS overdue_bucket_4,
    automn.fn_amount(x.overdue_bucket_5) AS overdue_bucket_5,
    automn.fn_amount(x.written_off_total) AS written_off_total,
    automn.fn_amount(x.written_off_principal) AS written_off_principal,
    automn.fn_amount(x.settlement_amount) AS settlement_amount,
    automn.fn_amount(x.npa_contracts_amount) AS npa_contracts_amount,
    automn.fn_date(x.wilful_default_date) AS wilful_default_date,
    automn.fn_amount(x.ufce_amount) AS ufce_amount,
    automn.fn_date(x.ufce_date) AS ufce_date,
    CAST('UPLOAD' AS VARCHAR(10)) AS source,
    CAST(x.load_id AS BIGINT) AS source_load_id,
    CAST('' AS VARCHAR(20)) AS lms_type
FROM (SELECT y.*,
             ROW_NUMBER() OVER (PARTITION BY y.account_number, y.sanction_key, y.as_of_date
                                ORDER BY y.load_id DESC, y.source_row DESC) AS rn
      FROM (SELECT s.*, COALESCE(LTRIM(RTRIM(s.sanction_number)), '') AS sanction_key,
                   automn.fn_date(s.position_date) AS as_of_date
            FROM automn.stg_lms_position s
            JOIN automn.input_load l ON l.load_id = s.load_id AND l.status = 'LOADED') y) x
WHERE x.rn = 1;
GO

-- ---------------------------------------------------------------- the contract the extract reads
CREATE OR ALTER VIEW automn.vw_pos_account_position AS
SELECT * FROM automn.vw_pos_lms_consumer
UNION ALL
SELECT * FROM automn.vw_pos_lms_commercial
UNION ALL
SELECT * FROM automn.vw_pos_upload;
GO
