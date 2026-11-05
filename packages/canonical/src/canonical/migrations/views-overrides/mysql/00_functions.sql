-- Helper functions for the source views, MySQL 8.0.19+ (Azure Database for MySQL, Amazon RDS / Aurora MySQL).
-- Hand-written: the T-SQL functions (migrations/azure/views/00_functions.sql) do not translate. Each must give the
-- same result as its T-SQL twin for every input. tools/build_views.py copies this file to migrations/mysql/views/.
-- Run first, with the mysql client (the DELIMITER lines are client commands); safe to run again.
-- In MySQL a schema is a database: automn holds the app tables and these functions.

CREATE DATABASE IF NOT EXISTS automn;

DELIMITER $$

-- Strict text -> date: DDMMYYYY, YYYY-MM-DD or DD.MM.YYYY; anything else, or an impossible date (31.02.2025), NULL.
DROP FUNCTION IF EXISTS automn.fn_date $$
CREATE FUNCTION automn.fn_date(p_text VARCHAR(40)) RETURNS DATE DETERMINISTIC NO SQL
BEGIN
    DECLARE t VARCHAR(40) DEFAULT TRIM(p_text);
    DECLARE y INT; DECLARE m INT; DECLARE d INT;
    IF t REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        SET y = SUBSTRING(t, 1, 4), m = SUBSTRING(t, 6, 2), d = SUBSTRING(t, 9, 2);
    ELSEIF t REGEXP '^[0-9]{8}$' THEN
        SET y = SUBSTRING(t, 5, 4), m = SUBSTRING(t, 3, 2), d = SUBSTRING(t, 1, 2);
    ELSEIF t REGEXP '^[0-9]{2}[.][0-9]{2}[.][0-9]{4}$' THEN
        SET y = SUBSTRING(t, 7, 4), m = SUBSTRING(t, 4, 2), d = SUBSTRING(t, 1, 2);
    ELSE
        RETURN NULL;
    END IF;
    IF y < 1 OR m NOT BETWEEN 1 AND 12 OR d < 1
       OR d > DAY(LAST_DAY(DATE_ADD(MAKEDATE(y, 1), INTERVAL m - 1 MONTH))) THEN
        RETURN NULL;                              -- impossible day / month / year 0
    END IF;
    RETURN DATE_ADD(DATE_ADD(MAKEDATE(y, 1), INTERVAL m - 1 MONTH), INTERVAL d - 1 DAY);
END $$

-- Text -> number, or NULL when it is not a number. MySQL's own CAST gives 0 for a bad cell, so the text is checked
-- first; too large -> NULL (as T-SQL TRY_CAST).
DROP FUNCTION IF EXISTS automn.fn_amount $$
CREATE FUNCTION automn.fn_amount(p_text VARCHAR(400)) RETURNS DECIMAL(20, 2) DETERMINISTIC NO SQL
BEGIN
    DECLARE t VARCHAR(400) DEFAULT TRIM(p_text);
    IF t REGEXP '^[+-]?([0-9]+[.]?[0-9]*|[.][0-9]+)$' AND ABS(CAST(t AS DOUBLE)) < 1e18 THEN
        RETURN CAST(t AS DECIMAL(20, 2));
    END IF;
    RETURN NULL;
END $$

DROP FUNCTION IF EXISTS automn.fn_percent $$
CREATE FUNCTION automn.fn_percent(p_text VARCHAR(400)) RETURNS DECIMAL(9, 4) DETERMINISTIC NO SQL
BEGIN
    DECLARE t VARCHAR(400) DEFAULT TRIM(p_text);
    IF t REGEXP '^[+-]?([0-9]+[.]?[0-9]*|[.][0-9]+)$' AND ABS(CAST(t AS DOUBLE)) < 1e5 THEN
        RETURN CAST(t AS DECIMAL(9, 4));
    END IF;
    RETURN NULL;
END $$

-- Whole numbers only ('12.0' is NULL, as in T-SQL); outside INT range -> NULL.
DROP FUNCTION IF EXISTS automn.fn_int $$
CREATE FUNCTION automn.fn_int(p_text VARCHAR(400)) RETURNS INT DETERMINISTIC NO SQL
BEGIN
    DECLARE t VARCHAR(400) DEFAULT TRIM(p_text);
    IF t REGEXP '^[+-]?[0-9]+$' AND LENGTH(t) <= 11 AND CAST(t AS SIGNED) BETWEEN -2147483648 AND 2147483647 THEN
        RETURN CAST(t AS SIGNED);
    END IF;
    RETURN NULL;
END $$

-- A reference as a join key: letters and digits only, upper case (F012/2025-26 = F012-2025-26 -> F012202526).
DROP FUNCTION IF EXISTS automn.fn_ref_key $$
CREATE FUNCTION automn.fn_ref_key(p_ref VARCHAR(200)) RETURNS VARCHAR(200) DETERMINISTIC NO SQL
BEGIN
    RETURN NULLIF(REGEXP_REPLACE(UPPER(p_ref), '[^A-Z0-9]', ''), '');
END $$

DELIMITER ;
