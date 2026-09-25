-- ============================================================
-- ROLLBACK for alter_meals_planning.sql
--
-- Refuses to run if anything only the new schema can express exists:
--   * a meal with no meal_type (created by the new backend - it has a user's
--     own name and no way back into the four fixed meals), or
--   * any planned item, or any item carrying a plan snapshot.
-- Losing somebody's planning to a rollback is unrecoverable, so it stops and
-- says which. Clear them by hand first if that is really what is wanted.
-- ============================================================

DO $$
DECLARE
    n_free    INT;
    n_planned INT;
    n_custom  INT;
BEGIN
    SELECT count(*) INTO n_free
    FROM meals WHERE meal_type IS NULL;

    SELECT count(*) INTO n_planned
    FROM meal_items WHERE status = 'planned' OR planned_quantity IS NOT NULL;

    SELECT count(*) INTO n_custom
    FROM meal_items WHERE is_custom;

    IF n_free > 0 OR n_planned > 0 OR n_custom > 0 THEN
        RAISE EXCEPTION
            'Refusing to roll back: % user-named meal(s), % planned item(s) and '
            '% hand-typed item(s) would be lost.', n_free, n_planned, n_custom;
    END IF;
END $$;

DROP INDEX IF EXISTS idx_meal_items_created;
ALTER TABLE meal_items DROP CONSTRAINT IF EXISTS chk_meal_items_custom_shape;
ALTER TABLE meal_items DROP COLUMN IF EXISTS is_custom;

ALTER TABLE meal_items DROP CONSTRAINT IF EXISTS chk_meal_items_plan_values;
ALTER TABLE meal_items DROP CONSTRAINT IF EXISTS chk_meal_items_planned_has_plan;
ALTER TABLE meal_items DROP CONSTRAINT IF EXISTS chk_meal_items_plan_shape;
ALTER TABLE meal_items DROP CONSTRAINT IF EXISTS chk_meal_items_status;

ALTER TABLE meal_items
    DROP COLUMN IF EXISTS planned_energy_kcal,
    DROP COLUMN IF EXISTS planned_unit,
    DROP COLUMN IF EXISTS planned_quantity,
    DROP COLUMN IF EXISTS status;

DROP INDEX IF EXISTS idx_meals_user_date_position;
ALTER TABLE meals DROP CONSTRAINT IF EXISTS chk_meals_name;

ALTER TABLE meals ALTER COLUMN meal_type SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_meals_type') THEN
        ALTER TABLE meals
            ADD CONSTRAINT chk_meals_type
            CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_meals_user_date_type') THEN
        ALTER TABLE meals
            ADD CONSTRAINT uq_meals_user_date_type
            UNIQUE (user_id, logged_date, meal_type);
    END IF;
END $$;

ALTER TABLE meals DROP COLUMN IF EXISTS position;
ALTER TABLE meals DROP COLUMN IF EXISTS name;
