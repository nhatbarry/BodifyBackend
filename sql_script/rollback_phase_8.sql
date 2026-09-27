-- Undo ddl_phase_8.sql. Refuses while any row exists: a synced day can be
-- re-read from the phone, but only while the phone still holds it.
DO $$
BEGIN
    IF to_regclass('public.health_daily') IS NOT NULL
       AND EXISTS (SELECT 1 FROM health_daily) THEN
        RAISE EXCEPTION 'health_daily holds rows; refusing to drop';
    END IF;
END $$;
DROP TABLE IF EXISTS health_daily;
