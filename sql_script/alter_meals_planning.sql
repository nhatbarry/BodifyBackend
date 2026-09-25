-- ============================================================
-- BODIFY - PHASE 7 (đợt 3: bữa ăn tự đặt tên, kế hoạch và thực tế)
--
-- Two changes to the diary, made together because they touch the same rows:
--
-- 1. MEALS ARE NAMED BY THE USER, NOT PICKED FROM FOUR.
--    `meals.meal_type` was an enum of breakfast/lunch/dinner/snack with a
--    UNIQUE (user, date, type), which is exactly what stopped somebody who eats
--    five small meals from having five. It is replaced by a free-text `name`
--    and a `position`. `name` is NULL until the user types one - the app
--    shows "Bữa 1", "Bữa 2" as a placeholder by position, so nothing is
--    stored that the user did not write.
--
--    There is deliberately no UNIQUE on (user, date, position). Positions are
--    only ever appended (max + 1) and reading breaks ties by meal_id, so two
--    concurrent creates cost a harmless tie instead of a unique-key error - and
--    a UNIQUE here would drag in the deferrable-constraint dance the workout
--    reorder endpoints need.
--
-- 2. A FOOD CAN BE PLANNED BEFORE IT IS EATEN.
--    `meal_items.status` is 'planned' or 'eaten'. A planned item is ticked when
--    it is eaten, optionally with a different amount. The plan is kept next to
--    the actual (`planned_quantity`, `planned_unit`, `planned_energy_kcal`) so
--    "you planned 1 bowl, you ate half" stays answerable - the same rule
--    session_sets.target_* follows. Items added directly as eaten carry no plan.
--    Day totals and the target comparison count eaten items only.
--
-- EXPAND-ONLY. `meals.meal_type` is kept as a nullable, unconstrained legacy
-- column rather than dropped, so the previous backend build keeps working
-- against this schema while the new one deploys. Dropping it is a separate,
-- later step (see the bottom of this file).
--
-- Idempotent: every statement is guarded, so re-running is a no-op.
-- Rollback: sql_script/rollback_meals_planning.sql
-- ============================================================


-- ============================================================
-- 1. MEALS: name + position
-- ============================================================

ALTER TABLE meals ADD COLUMN IF NOT EXISTS name     VARCHAR(60);
ALTER TABLE meals ADD COLUMN IF NOT EXISTS position INT NOT NULL DEFAULT 0;

-- Existing rows keep the meal they were. Vietnamese, because that is the
-- language the app ships in; the user can rename them like any other.
UPDATE meals
SET name = CASE meal_type
        WHEN 'breakfast' THEN 'Bữa sáng'
        WHEN 'lunch'     THEN 'Bữa trưa'
        WHEN 'dinner'    THEN 'Bữa tối'
        WHEN 'snack'     THEN 'Ăn vặt'
    END,
    position = CASE meal_type
        WHEN 'breakfast' THEN 0
        WHEN 'lunch'     THEN 1
        WHEN 'dinner'    THEN 2
        WHEN 'snack'     THEN 3
        ELSE 0
    END
WHERE name IS NULL
  AND meal_type IS NOT NULL;

-- The old rules that made four fixed meals the only possibility.
ALTER TABLE meals DROP CONSTRAINT IF EXISTS uq_meals_user_date_type;
ALTER TABLE meals DROP CONSTRAINT IF EXISTS chk_meals_type;
ALTER TABLE meals ALTER COLUMN meal_type DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_meals_name'
    ) THEN
        ALTER TABLE meals
            ADD CONSTRAINT chk_meals_name
            CHECK (name IS NULL OR length(btrim(name)) > 0);
    END IF;
END $$;

-- The diary reads one day in display order.
CREATE INDEX IF NOT EXISTS idx_meals_user_date_position
    ON meals (user_id, logged_date, position, meal_id);


-- ============================================================
-- 2. MEAL ITEMS: planned or eaten
-- ============================================================

ALTER TABLE meal_items
    ADD COLUMN IF NOT EXISTS status              VARCHAR(10)   NOT NULL DEFAULT 'eaten',
    ADD COLUMN IF NOT EXISTS planned_quantity    NUMERIC(8, 2),
    ADD COLUMN IF NOT EXISTS planned_unit        VARCHAR(10),
    ADD COLUMN IF NOT EXISTS planned_energy_kcal NUMERIC(8, 2);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_meal_items_status') THEN
        ALTER TABLE meal_items
            ADD CONSTRAINT chk_meal_items_status
            CHECK (status IN ('planned', 'eaten'));
    END IF;

    -- The three plan columns describe one snapshot: all present or all absent.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_meal_items_plan_shape') THEN
        ALTER TABLE meal_items
            ADD CONSTRAINT chk_meal_items_plan_shape
            CHECK (num_nonnulls(planned_quantity, planned_unit, planned_energy_kcal) IN (0, 3));
    END IF;

    -- Something still planned has to say what was planned.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_meal_items_planned_has_plan') THEN
        ALTER TABLE meal_items
            ADD CONSTRAINT chk_meal_items_planned_has_plan
            CHECK (status <> 'planned' OR planned_quantity IS NOT NULL);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_meal_items_plan_values') THEN
        ALTER TABLE meal_items
            ADD CONSTRAINT chk_meal_items_plan_values
            CHECK (
                (planned_quantity IS NULL OR planned_quantity > 0) AND
                (planned_unit IS NULL OR planned_unit IN ('serving', 'gram')) AND
                (planned_energy_kcal IS NULL OR planned_energy_kcal >= 0)
            );
    END IF;
END $$;


-- ============================================================
-- 3. MEAL ITEMS: typed in by hand
-- ============================================================
-- A food the catalog does not have. The user types a name and what one
-- serving comes to, and it is written into the row like any other entry -
-- nutrition lives on meal_items, so nothing needs adding to the shared
-- catalog. `food_id` is NULL for these, which is also what a catalog row
-- removed later looks like; `is_custom` tells the two apart, because a custom
-- entry can still be rescaled (from its own per-serving figures) and an
-- orphaned catalog entry cannot.

ALTER TABLE meal_items
    ADD COLUMN IF NOT EXISTS is_custom BOOLEAN NOT NULL DEFAULT false;

DO $$
BEGIN
    -- Typed in by hand means no catalog row and no weight to convert grams
    -- from, so it is counted in servings only.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chk_meal_items_custom_shape') THEN
        ALTER TABLE meal_items
            ADD CONSTRAINT chk_meal_items_custom_shape
            CHECK (NOT is_custom OR (food_id IS NULL AND unit = 'serving'));
    END IF;
END $$;

-- "What have I eaten lately" reads the newest entries per user.
CREATE INDEX IF NOT EXISTS idx_meal_items_created
    ON meal_items (meal_id, created_at DESC);


-- ============================================================
-- 4. WHAT LANDED
-- ============================================================
DO $$
DECLARE
    n_meals   INT;
    n_named   INT;
    n_planned INT;
BEGIN
    SELECT count(*) INTO n_meals   FROM meals;
    SELECT count(*) INTO n_named   FROM meals WHERE name IS NOT NULL;
    SELECT count(*) INTO n_planned FROM meal_items WHERE status = 'planned';

    RAISE NOTICE 'meals:               % row(s), % named', n_meals, n_named;
    RAISE NOTICE 'planned meal_items:  % row(s)', n_planned;
END $$;


-- ============================================================
-- LATER: drop the legacy column
-- ============================================================
-- Once the backend that ignores it is the only one running:
--
--     ALTER TABLE meals DROP COLUMN meal_type;
--
-- Not done here on purpose - see EXPAND-ONLY above.
