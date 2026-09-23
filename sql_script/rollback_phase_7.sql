-- ============================================================
-- ROLLBACK PHASE 7 (đợt 1: danh mục thực phẩm)
--
-- Undoes ddl_phase_7.sql. The catalog is imported data, not user data: every
-- row can be rebuilt from data/nin/*.json and the USDA files by re-running
-- the seeder, so dropping these tables loses nothing that cannot be restored.
--
-- That stops being true once the diary exists and points at foods - a meal
-- item is the user's own record and cannot be regenerated. The guard below
-- refuses to run in that case, the same way ddl_phase_3.sql refuses to drop
-- session tables that hold rows.
-- ============================================================

DO $$
DECLARE
    n_items BIGINT;
BEGIN
    IF to_regclass('public.meal_items') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM meal_items' INTO n_items;
        IF n_items > 0 THEN
            RAISE EXCEPTION
                'meal_items holds % row(s) of user diary entries that reference '
                'foods. Roll back Phase 7 đợt 2 first '
                '(sql_script/rollback_phase_7_meals.sql).', n_items;
        END IF;
    END IF;
END $$;

DROP TABLE IF EXISTS food_components;
DROP TABLE IF EXISTS food_portions;
DROP TABLE IF EXISTS food_translations;
DROP TABLE IF EXISTS foods;
DROP TABLE IF EXISTS food_categories;

DO $$
BEGIN
    RAISE NOTICE 'Phase 7 catalog tables dropped.';
END $$;
