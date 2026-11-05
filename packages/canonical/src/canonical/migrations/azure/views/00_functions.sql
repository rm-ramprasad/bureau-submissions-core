-- Helper functions for the source views (consumer.sql, commercial.sql). Run in rarfinmisdb in SSMS / Azure Data
-- Studio (the GO lines separate batches). Safe to run again: CREATE OR ALTER.
--
-- The views hold the source logic (joins, conversions, labels -> values) so it can change without redeploying the
-- app (user decision, 2026-10-30). See submissions/adapters/views.py for what the app expects of a view.
--
-- These functions are the only per-dialect SQL: tools/build_views.py does not translate them; Postgres and MySQL have
-- their own hand-written copies in migrations/views-overrides/<dialect>/00_functions.sql, which must behave the same.
-- Labels and bureau codes are no longer functions: they are joins to 01_reference.sql's views (portable, faster).
-- (fn_label_value / fn_code_value may still exist in the database from earlier runs; nothing uses them now.)

-- Strict text -> date: DDMMYYYY (the workbooks), YYYY-MM-DD (a real Excel date) or DD.MM.YYYY (NeSL sheets).
-- Anything else is NULL, so validation reports it instead of a wrong date (e.g. 2404199 must not become 4199).
CREATE OR ALTER FUNCTION automn.fn_date(@text NVARCHAR(40))
RETURNS DATE
AS
BEGIN
    DECLARE @t NVARCHAR(40) = LTRIM(RTRIM(@text));
    RETURN CASE
        WHEN @t LIKE N'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' THEN TRY_CONVERT(date, @t, 23)
        WHEN @t LIKE N'[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
            THEN TRY_CONVERT(date, RIGHT(@t, 4) + SUBSTRING(@t, 3, 2) + LEFT(@t, 2), 112)
        WHEN @t LIKE N'[0-9][0-9].[0-9][0-9].[0-9][0-9][0-9][0-9]' THEN TRY_CONVERT(date, @t, 104)
    END;
END;
GO

-- Text -> number, or NULL when it is not a number (so validation reports the empty value instead of the view failing,
-- and never 0 for a bad cell). fn_amount: DECIMAL(20, 2) amounts; fn_percent: DECIMAL(9, 4); fn_int: whole numbers.
CREATE OR ALTER FUNCTION automn.fn_amount(@text NVARCHAR(400))
RETURNS DECIMAL(20, 2)
WITH SCHEMABINDING
AS
BEGIN
    RETURN TRY_CAST(@text AS DECIMAL(20, 2));
END;
GO

CREATE OR ALTER FUNCTION automn.fn_percent(@text NVARCHAR(400))
RETURNS DECIMAL(9, 4)
WITH SCHEMABINDING
AS
BEGIN
    RETURN TRY_CAST(@text AS DECIMAL(9, 4));
END;
GO

CREATE OR ALTER FUNCTION automn.fn_int(@text NVARCHAR(400))
RETURNS INT
WITH SCHEMABINDING
AS
BEGIN
    RETURN TRY_CAST(@text AS INT);
END;
GO

-- A reference as a join key: letters and digits only, upper case (F012/2025-26 and F012-2025-26 both -> F012202526).
-- The workbook and the loan view write sanction references with different separators (user, 2026-10-30).
-- WITH SCHEMABINDING makes it deterministic, so joins on it can hash instead of calling it per row pair (the commercial
-- views join on it; without it a check of one upload ran for minutes, 2026-11-02).
CREATE OR ALTER FUNCTION automn.fn_ref_key(@ref NVARCHAR(200))
RETURNS NVARCHAR(200)
WITH SCHEMABINDING
AS
BEGIN
    DECLARE @s NVARCHAR(200) = UPPER(@ref), @i INT;
    IF @s IS NULL RETURN NULL;
    SET @i = PATINDEX(N'%[^A-Z0-9]%', @s COLLATE Latin1_General_BIN);
    WHILE @i > 0
    BEGIN
        SET @s = STUFF(@s, @i, 1, N'');
        SET @i = PATINDEX(N'%[^A-Z0-9]%', @s COLLATE Latin1_General_BIN);
    END;
    RETURN NULLIF(@s, N'');
END;
GO
