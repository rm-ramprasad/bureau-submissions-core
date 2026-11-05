-- Three outputs per bureau (2026-10-29): the bureau Excel (Data Submission Form), the review Excel for the credit
-- team, and the TUDF converted from the bureau Excel (output_file_path). Run once in rarfinmisdb, by a user allowed
-- to alter tables in schema automn (or let `ops.ps1 migrate` apply it if the app's identity may). No BEGIN/COMMIT:
-- the migration runner wraps each file in one transaction.
ALTER TABLE automn.submission_request ADD excel_file_path NVARCHAR(500) NULL, review_file_path NVARCHAR(500) NULL;
