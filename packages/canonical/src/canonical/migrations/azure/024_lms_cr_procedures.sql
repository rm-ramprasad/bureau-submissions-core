-- The LMS's CR section from stored procedures (owner 2026-11-16): the app runs dbo.usp_GetConsumerSubmissionCR /
-- dbo.usp_GetCommercialSubmissionCR for a request's date and saves the rows here, one set per type and date (a new
-- fetch replaces it); the source views (views/consumer.sql, commercial.sql, position.sql: run them in SSMS too) read
-- these tables instead of wip.VwConsumerSubmissionAccountSegment / wip.VwCommercialSubmissionCR. Same column names
-- as those views, plus SelectionReason; types as rarfincare describes the procedures (2026-11-17). lms_cr_fetch logs every run. canonical/lms_cr.py has the same columns.
-- Run once in rarfinmisdb in SSMS (admin login), then `ops.ps1 migrate --mark-applied 024_lms_cr_procedures`.
-- The app identity also needs EXECUTE on both procedures (granted 2026-11-16). No BEGIN/COMMIT (the runner wraps it).
CREATE TABLE automn.lms_cr_consumer (
	AccountNumber VARCHAR(20) NULL,
	TransactionDate DATE NULL,
	AssetClassification VARCHAR(2) NULL,
	TL_CreditFacilityStatus INTEGER NULL,
	TL_DateDisbursed NVARCHAR(4000) NULL,
	TL_DateofLastPayment NVARCHAR(4000) NULL,
	TL_DateClosed NVARCHAR(4000) NULL,
	TL_DateReported NVARCHAR(4000) NULL,
	TL_NoofDaysPastDue INTEGER NULL,
	TL_RepaymentTenure INTEGER NULL,
	TL_RateofInterest NUMERIC(18, 2) NULL,
	TL_SanctionedAmt INTEGER NULL,
	TL_CurrentBalance NUMERIC(19, 0) NULL,
	TL_AmtOverdue NUMERIC(18, 0) NULL,
	TL_CreditLimit INTEGER NULL,
	TL_CashLimit INTEGER NULL,
	TL_EMIAmount NUMERIC(18, 0) NULL,
	TL_WrittenoffAmountTotal INTEGER NULL,
	TL_WrittenOffPrincipalAmount INTEGER NULL,
	TL_SettlementAmt INTEGER NULL,
	TL_ActualPaymentAmt NUMERIC(18, 2) NULL,
	SelectionReason VARCHAR(100) NULL
);
CREATE INDEX ix_lms_cr_consumer_date ON automn.lms_cr_consumer (TransactionDate, AccountNumber);
CREATE TABLE automn.lms_cr_commercial (
	AccountNumber VARCHAR(20) NULL,
	SanctionRefNo VARCHAR(255) NULL,
	transactiondate DATE NULL,
	AcClosedDate DATE NULL,
	LastPaymentDate DATE NULL,
	IsAccountClosed VARCHAR(3) NULL,
	RBIODDays INTEGER NULL,
	Tenure INTEGER NULL,
	OTHER_BK INTEGER NULL,
	[Major reasons for Restructuring] INTEGER NULL,
	[Asset based Security coverage] INTEGER NULL,
	[Guarantee Coverage] INTEGER NULL,
	[Sanction Date] NVARCHAR(4000) NULL,
	[Account Status Date] NVARCHAR(4000) NULL,
	[Asset Classification Date] INTEGER NULL,
	[Date Classified as Wilful Default] INTEGER NULL,
	[Loan Renewal Date] INTEGER NULL,
	[Maturity Date] NVARCHAR(4000) NULL,
	[UFCE Date] INTEGER NULL,
	[Account Status] VARCHAR(2) NULL,
	[Asset Classification] VARCHAR(4) NULL,
	[Bank Remark Code] INTEGER NULL,
	[Currency Code] VARCHAR(3) NULL,
	[Dispute ID No.] INTEGER NULL,
	[Previous Account Number] VARCHAR(20) NULL,
	[Amount Overdue] INTEGER NULL,
	[Amount of Contracts Classified as NPA] INTEGER NULL,
	[Current Balance] INTEGER NULL,
	[Drawing Power] INTEGER NULL,
	[High Credit] INTEGER NULL,
	[Installment Amount] INTEGER NULL,
	[Last Repaid Amount] INTEGER NULL,
	[Notional Amount of Outstanding Restructured Contracts] INTEGER NULL,
	[Overdue Bucket 01 ( 1 – 30 days)] INTEGER NULL,
	[Overdue Bucket 02 ( 31 – 60 days)] INTEGER NULL,
	[Overdue Bucket 03 ( 61 – 90 days)] INTEGER NULL,
	[Overdue Bucket 04 (91 – 180 days)] INTEGER NULL,
	[Overdue Bucket 05 (Above 180 days)] INTEGER NULL,
	[Sanctioned Amount] INTEGER NULL,
	[Settled Amount] INTEGER NULL,
	[UFCE Amount] INTEGER NULL,
	[Written Off Amount] INTEGER NULL,
	SelectionReason VARCHAR(100) NULL
);
CREATE INDEX ix_lms_cr_commercial_date ON automn.lms_cr_commercial (transactiondate, AccountNumber);
CREATE TABLE automn.lms_cr_fetch (
	fetch_id BIGINT NOT NULL IDENTITY,
	submission_type VARCHAR(20) NOT NULL,
	transaction_date DATE NOT NULL,
	previous_date DATE NOT NULL,
	row_count INTEGER NULL,
	outcome VARCHAR(20) NOT NULL,
	message NVARCHAR(1000) NULL,
	fetched_by NVARCHAR(200) NULL,
	fetched_at DATETIME2 NOT NULL,
	PRIMARY KEY (fetch_id)
);
