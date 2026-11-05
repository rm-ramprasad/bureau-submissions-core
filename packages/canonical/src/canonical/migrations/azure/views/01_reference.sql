-- Label and code lookups for the source views, from automn.ref_code_translation. Run after 00_functions.sql and before
-- consumer.sql / commercial.sql, in rarfinmisdb in SSMS / Azure Data Studio; safe to run again (CREATE OR ALTER).
--
-- They replace the functions fn_label_value / fn_code_value with joins, which every database runs the same way
-- (tools/build_views.py generates the Postgres and MySQL copies). The views join them as
--     LEFT JOIN automn.vw_ref_label x ON x.submission_type = '<TYPE>' AND x.code_list = '<list>'
--                                    AND x.label_key = UPPER(LTRIM(RTRIM(<column>)))
-- and report COALESCE(x.code, NULLIF(LTRIM(RTRIM(<column>)), '')): an unknown value is passed on as it is, so
-- validation reports it instead of losing it; empty -> NULL. Keys are upper case and trimmed, so the match ignores
-- case and outer spaces on every database (as Azure SQL's default collation did). is_active is compared as
-- CAST(... AS INT): a BIT on SQL Server, a boolean on Postgres, a TINYINT on MySQL.

-- A workbook label ("Male", "Tamil Nadu") -> its canonical value: active rows only; where two values share a label,
-- the first by name.
CREATE OR ALTER VIEW automn.vw_ref_label AS
SELECT x.submission_type, x.code_list, x.label_key, x.code
FROM (SELECT t.submission_type, t.code_list, UPPER(LTRIM(RTRIM(t.label))) AS label_key, t.code,
             ROW_NUMBER() OVER (PARTITION BY t.submission_type, t.code_list, UPPER(LTRIM(RTRIM(t.label)))
                                ORDER BY t.code) AS rn
      FROM automn.ref_code_translation t
      WHERE CAST(t.is_active AS INT) = 1 AND NULLIF(LTRIM(RTRIM(t.label)), '') IS NOT NULL) x
WHERE x.rn = 1;
GO

-- A bureau code ("0012", "05") -> its canonical value. Where several values share one code (e.g. SMA_0 / SMA_1 /
-- SMA_2 -> 05) the first by name, active rows first: the file writes the same code back.
CREATE OR ALTER VIEW automn.vw_ref_code AS
SELECT x.submission_type, x.code_list, x.code_key, x.code
FROM (SELECT t.submission_type, t.code_list, UPPER(LTRIM(RTRIM(t.output_code))) AS code_key, t.code,
             ROW_NUMBER() OVER (PARTITION BY t.submission_type, t.code_list, UPPER(LTRIM(RTRIM(t.output_code)))
                                ORDER BY CAST(t.is_active AS INT) DESC, t.code) AS rn
      FROM automn.ref_code_translation t) x
WHERE x.rn = 1;
GO
