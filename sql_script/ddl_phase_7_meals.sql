-- ============================================================
-- BODIFY - PHASE 7 (đợt 2: nhật ký ăn uống)
-- WHAT THE USER ACTUALLY ATE
--
-- Đợt 1 built the catalog - 2098 foods, half of them per 100 g and half of
-- them per serving. This is the other half of the feature: the record of what
-- somebody ate, and the target they were eating against.
--
-- The same split runs through everything here. A `per_100g` row is logged in
-- grams, a `per_serving` row is logged in servings, and `meal_items` keeps
-- both the number the user typed and the unit it was typed in, because
-- "1.5" means nothing on its own.
--
-- **Nutrition is copied in, not looked up.** Every macro on `meal_items` is
-- the figure at the moment the food was logged. This is the rule Phase 3
-- already follows for `session_sets.target_*`: correcting a catalog row next
-- month must not quietly rewrite whether somebody hit their target last
-- Tuesday. `food_id` stays as a link for anyone who wants today's figure, and
-- is ON DELETE SET NULL - removing a food from the catalog unlinks the
-- history, it does not delete it.
--
-- Assumptions from earlier phases:
--   auth.users(id)     Supabase Auth, UUID PK
--   foods(food_id)     Phase 7 đợt 1
--
-- Idempotent: every statement is guarded, so re-running is a no-op.
-- Rollback: sql_script/rollback_phase_7_meals.sql
-- ============================================================


-- ============================================================
-- 1. MEALS
-- ============================================================
-- One row per (day, sitting). The four sittings are fixed rather than free
-- text: the diary screen is four sections, and a fifth spelling of "lunch"
-- would render as a section nobody asked for.
--
-- UNIQUE (user_id, logged_date, meal_type) is what keeps that screen
-- well-formed - there is exactly one bữa trưa on any given day, and adding a
-- second food to lunch appends an item rather than creating a rival lunch.
-- Several snacks in a day all land under the one `snack` row, which is how
-- every food diary presents them.
--
-- The row carries `note` and `photo_key` because they belong to the sitting,
-- not to any one food in it.

CREATE TABLE IF NOT EXISTS meals (
    meal_id     BIGSERIAL   PRIMARY KEY,

    user_id     UUID        NOT NULL
                REFERENCES auth.users (id) ON DELETE CASCADE,

    logged_date DATE        NOT NULL,
    meal_type   VARCHAR(16) NOT NULL,

    note        TEXT,
    -- A storage key, never a URL - the same rule as user_profiles.avatar_url.
    -- A signed URL written into the database sits there expiring.
    photo_key   TEXT,

    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_meals_type
        CHECK (meal_type IN ('breakfast', 'lunch', 'dinner', 'snack')),

    CONSTRAINT uq_meals_user_date_type
        UNIQUE (user_id, logged_date, meal_type)
);

-- The diary reads one day, or one month for the calendar strip; both are this
-- index. Descending date because every screen starts at today.
CREATE INDEX IF NOT EXISTS idx_meals_user_date
    ON meals (user_id, logged_date DESC);


-- ============================================================
-- 2. MEAL ITEMS
-- ============================================================
-- One food eaten, with its nutrition frozen at the moment it was logged.
--
-- `quantity` + `unit` is the pair the user typed; `grams` is what that works
-- out to, and is NULLABLE on purpose. 237 of the Institute's dishes have no
-- published serving weight, so "1 suất phở" is a complete, loggable fact whose
-- weight in grams is genuinely unknown. Storing 0 or guessing would both be
-- worse than saying so.

CREATE TABLE IF NOT EXISTS meal_items (
    meal_item_id BIGSERIAL     PRIMARY KEY,

    meal_id      BIGINT        NOT NULL
                 REFERENCES meals (meal_id) ON DELETE CASCADE,

    -- SET NULL, never CASCADE: the catalog is ours to tidy, the diary is the
    -- user's record. Dropping a food must not delete somebody's Tuesday.
    food_id      BIGINT
                 REFERENCES foods (food_id) ON DELETE SET NULL,

    -- Snapshot, for the same reason session_exercises.exercise_name is one:
    -- an unlinked row still has to say what was eaten.
    food_name    VARCHAR(200)  NOT NULL,

    quantity     NUMERIC(8, 2) NOT NULL,
    unit         VARCHAR(10)   NOT NULL,
    grams        NUMERIC(8, 1),

    -- Frozen nutrition. energy is NOT NULL because every catalog row has it;
    -- the macros are nullable because 30 of the Institute's dishes are missing
    -- one, and inventing a zero would understate somebody's protein.
    energy_kcal  NUMERIC(8, 2) NOT NULL,
    protein_g    NUMERIC(8, 2),
    fat_g        NUMERIC(8, 2),
    carb_g       NUMERIC(8, 2),
    fiber_g      NUMERIC(8, 2),

    sort_order   INT           NOT NULL DEFAULT 0,

    created_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT chk_meal_items_quantity CHECK (quantity > 0),
    CONSTRAINT chk_meal_items_unit     CHECK (unit IN ('serving', 'gram')),
    CONSTRAINT chk_meal_items_grams    CHECK (grams IS NULL OR grams > 0),
    CONSTRAINT chk_meal_items_energy   CHECK (energy_kcal >= 0),
    CONSTRAINT chk_meal_items_macros   CHECK (
        (protein_g IS NULL OR protein_g >= 0) AND
        (fat_g     IS NULL OR fat_g     >= 0) AND
        (carb_g    IS NULL OR carb_g    >= 0) AND
        (fiber_g   IS NULL OR fiber_g   >= 0)
    )
);

CREATE INDEX IF NOT EXISTS idx_meal_items_meal
    ON meal_items (meal_id, sort_order);


-- ============================================================
-- 3. NUTRITION TARGETS
-- ============================================================
-- What the user is aiming for in a day. One row per user, upserted:
--
--   INSERT INTO nutrition_targets (...) VALUES (...)
--   ON CONFLICT (user_id) DO UPDATE SET ...
--
-- Deliberately NOT an effective-dated history. A target is a setting the user
-- changes when their goal changes, and the diary compares *today* against it;
-- nothing in the app asks what they were aiming for back in March. If that
-- question ever arrives it wants an `effective_from` column and a real
-- decision about what the old totals mean, not a table shaped for it in
-- advance.
--
-- Only energy is required. Somebody counting calories and nothing else is the
-- common case, and demanding three macros they have not thought about is how
-- a setup screen gets abandoned.

CREATE TABLE IF NOT EXISTS nutrition_targets (
    nutrition_target_id BIGSERIAL     PRIMARY KEY,

    user_id     UUID          NOT NULL
                REFERENCES auth.users (id) ON DELETE CASCADE,

    energy_kcal NUMERIC(7, 1) NOT NULL,
    protein_g   NUMERIC(7, 1),
    fat_g       NUMERIC(7, 1),
    carb_g      NUMERIC(7, 1),

    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),

    CONSTRAINT chk_nutrition_targets_energy CHECK (energy_kcal > 0),
    CONSTRAINT chk_nutrition_targets_macros CHECK (
        (protein_g IS NULL OR protein_g >= 0) AND
        (fat_g     IS NULL OR fat_g     >= 0) AND
        (carb_g    IS NULL OR carb_g    >= 0)
    ),

    CONSTRAINT uq_nutrition_targets_user UNIQUE (user_id)
);


-- ============================================================
-- 4. WHAT LANDED
-- ============================================================
DO $$
DECLARE
    n_meals   INT;
    n_items   INT;
    n_targets INT;
BEGIN
    SELECT count(*) INTO n_meals   FROM meals;
    SELECT count(*) INTO n_items   FROM meal_items;
    SELECT count(*) INTO n_targets FROM nutrition_targets;

    RAISE NOTICE 'meals:              % row(s)', n_meals;
    RAISE NOTICE 'meal_items:         % row(s)', n_items;
    RAISE NOTICE 'nutrition_targets:  % row(s)', n_targets;
END $$;
