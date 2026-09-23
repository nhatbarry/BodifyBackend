-- ============================================================
-- ROLLBACK PHASE 7 (đợt 2: nhật ký ăn uống)
--
-- Undoes ddl_phase_7_meals.sql.
--
-- Unlike đợt 1, this is NOT safe to run casually. The catalog can be rebuilt
-- from the seeders; a diary cannot. `meals` / `meal_items` hold what somebody
-- ate, typed in by hand, and there is no source to restore them from.
--
-- So the guard below refuses to drop tables that hold rows, the same way
-- ddl_phase_3.sql refuses to drop session tables with history in them. Losing
-- a food diary to a migration is unrecoverable.
--
-- To drop anyway - only when the rows are known to be test data:
--   DELETE FROM meals;  -- meal_items follows by CASCADE
--   then re-run this file.
-- ============================================================

DO $$
DECLARE
    n_meals   BIGINT := 0;
    n_targets BIGINT := 0;
BEGIN
    IF to_regclass('public.meals') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM meals' INTO n_meals;
    END IF;
    IF to_regclass('public.nutrition_targets') IS NOT NULL THEN
        EXECUTE 'SELECT count(*) FROM nutrition_targets' INTO n_targets;
    END IF;

    IF n_meals > 0 OR n_targets > 0 THEN
        RAISE EXCEPTION
            'Refusing to drop: % meal(s) and % target(s) are real user data '
            'with no source to rebuild them from. Delete them explicitly '
            'first if that is genuinely what you want.', n_meals, n_targets;
    END IF;
END $$;

DROP TABLE IF EXISTS meal_items;
DROP TABLE IF EXISTS meals;
DROP TABLE IF EXISTS nutrition_targets;

DO $$
BEGIN
    RAISE NOTICE 'Phase 7 đợt 2 rolled back: meals, meal_items, nutrition_targets dropped.';
END $$;
