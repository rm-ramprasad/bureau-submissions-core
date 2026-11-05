-- Workbook labels (2026-10-30). The input workbooks now hold labels ("Male", "Tamil Nadu", "Private Limited") instead
-- of bureau codes. automn.ref_code_translation is the reference for them (the workbooks' Master Data sheet is never
-- read): fill `label` for each (submission_type, code_list, code) the workbooks use. Mappings turn a label into its
-- canonical value (`lookups: {name: {table: <code_list>}}`), and the upload check `label` blocks unknown labels.
-- Matching ignores case and leading / trailing spaces; one label per code list must not point to two values.
-- Run once in rarfinmisdb; no BEGIN/COMMIT (the runner wraps the file in one transaction).
ALTER TABLE automn.ref_code_translation ADD label NVARCHAR(200) NULL;
