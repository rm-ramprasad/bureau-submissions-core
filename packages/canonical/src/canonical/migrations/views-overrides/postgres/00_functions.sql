-- Helper functions for the source views, Postgres (Azure Database for PostgreSQL, Amazon RDS / Aurora PostgreSQL).
-- Hand-written: the T-SQL functions (migrations/azure/views/00_functions.sql) do not translate. Each must give the
-- same result as its T-SQL twin for every input. tools/build_views.py copies this file to
-- migrations/postgres/views/. Run first; safe to run again (CREATE OR REPLACE).

CREATE SCHEMA IF NOT EXISTS automn;

-- Strict text -> date: DDMMYYYY, YYYY-MM-DD or DD.MM.YYYY; anything else, or an impossible date (31.02.2025), NULL.
CREATE OR REPLACE FUNCTION automn.fn_date(p_text TEXT) RETURNS DATE
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE t TEXT := btrim(p_text, ' ');
BEGIN
    IF t ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' THEN
        RETURN make_date(substr(t, 1, 4)::int, substr(t, 6, 2)::int, substr(t, 9, 2)::int);
    ELSIF t ~ '^[0-9]{8}$' THEN
        RETURN make_date(substr(t, 5, 4)::int, substr(t, 3, 2)::int, substr(t, 1, 2)::int);
    ELSIF t ~ '^[0-9]{2}\.[0-9]{2}\.[0-9]{4}$' THEN
        RETURN make_date(substr(t, 7, 4)::int, substr(t, 4, 2)::int, substr(t, 1, 2)::int);
    END IF;
    RETURN NULL;
EXCEPTION WHEN others THEN
    RETURN NULL;                                  -- impossible day / month / year 0
END $$;

-- Text -> number, or NULL when it is not a number (never 0 for a bad cell), as T-SQL TRY_CAST: optional sign,
-- digits with an optional decimal point, outer spaces allowed; rounded to the scale; too large -> NULL.
CREATE OR REPLACE FUNCTION automn.fn_amount(p_text TEXT) RETURNS NUMERIC(20, 2)
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_text ~ '^\s*[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)\s*$' THEN
        RETURN btrim(p_text)::NUMERIC(20, 2);
    END IF;
    RETURN NULL;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION automn.fn_percent(p_text TEXT) RETURNS NUMERIC(9, 4)
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_text ~ '^\s*[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)\s*$' THEN
        RETURN btrim(p_text)::NUMERIC(9, 4);
    END IF;
    RETURN NULL;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

-- Whole numbers only ('12.0' is NULL, as in T-SQL); outside INT range -> NULL.
CREATE OR REPLACE FUNCTION automn.fn_int(p_text TEXT) RETURNS INTEGER
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF p_text ~ '^\s*[+-]?[0-9]+\s*$' THEN
        RETURN btrim(p_text)::INTEGER;
    END IF;
    RETURN NULL;
EXCEPTION WHEN others THEN
    RETURN NULL;
END $$;

-- A reference as a join key: letters and digits only, upper case (F012/2025-26 = F012-2025-26 -> F012202526).
-- IMMUTABLE, so joins on it can hash (the T-SQL twin needs WITH SCHEMABINDING for the same reason).
CREATE OR REPLACE FUNCTION automn.fn_ref_key(p_ref TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT NULLIF(regexp_replace(upper(p_ref), '[^A-Z0-9]', '', 'g'), '')
$$;
